"""ずんだもんニュースソング(週次まとめの歌唱パート)を生成するサービス。

- MELODY_TEMPLATE: オリジナルのメロディ(4フレーズ、A-A'-B-A''構成)
- モーラ分割・歌詞割り当て・スコア組み立て(純粋関数、I/Oなし)
- Geminiによる歌詞生成(GEMINI_PROJECT未設定・失敗時はFALLBACK_LYRICSへフォールバック)
- VOICEVOXの歌唱合成(sing_frame_audio_query → frame_synthesis)呼び出し
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings
from ..schemas.draft import VideoPlanDraft

logger = logging.getLogger(__name__)

# VOICEVOXの歌唱合成におけるフレームレート(1秒 = 93.75フレーム)
FRAMES_PER_SECOND = 93.75

# 120BPM換算の音価(フレーム数)。四分音符 = 60/120秒 = 0.5秒 ≈ 47フレーム
EIGHTH_FRAMES = 24
QUARTER_FRAMES = 47
DOTTED_QUARTER_FRAMES = 70
HALF_FRAMES = 94
DOTTED_HALF_FRAMES = 141
WHOLE_FRAMES = 188

# 歌い出し前・歌い終わり後の無音(フレーム数)
LEADING_REST_FRAMES = 47
TRAILING_REST_FRAMES = 94
# フレーズ間の短い無音(フレーム数)
INTER_PHRASE_REST_FRAMES = 47


@dataclass(frozen=True)
class SongNote:
    key: int | None  # MIDIノート番号。Noneは休符
    frame_length: int  # フレーム数。1秒 ≈ 93.75フレーム


@dataclass(frozen=True)
class SongPhrase:
    notes: tuple[SongNote, ...]  # 歌唱ノートのみ(すべてkey != None)


# オリジナルメロディ(A-A'-B-A''構成)。
# ずんだもんの声域で自然に響くよう、G4(67)〜E5(76)のCメジャーペンタトニック
# (G4/A4/C5/D5/E5)を中心に、フレーズ末尾のノートを長めにしている。
MELODY_TEMPLATE: tuple[SongPhrase, ...] = (
    # A: 8モーラ
    SongPhrase(
        notes=(
            SongNote(72, QUARTER_FRAMES),
            SongNote(74, DOTTED_QUARTER_FRAMES),
            SongNote(76, EIGHTH_FRAMES),
            SongNote(74, EIGHTH_FRAMES),
            SongNote(72, QUARTER_FRAMES),
            SongNote(69, QUARTER_FRAMES),
            SongNote(72, EIGHTH_FRAMES),
            SongNote(74, DOTTED_HALF_FRAMES),
        )
    ),
    # A': 8モーラ(Aの変奏)
    SongPhrase(
        notes=(
            SongNote(72, QUARTER_FRAMES),
            SongNote(76, DOTTED_QUARTER_FRAMES),
            SongNote(74, EIGHTH_FRAMES),
            SongNote(72, EIGHTH_FRAMES),
            SongNote(74, QUARTER_FRAMES),
            SongNote(76, QUARTER_FRAMES),
            SongNote(74, EIGHTH_FRAMES),
            SongNote(72, DOTTED_HALF_FRAMES),
        )
    ),
    # B: 8モーラ(対照的な下降フレーズ)
    SongPhrase(
        notes=(
            SongNote(69, QUARTER_FRAMES),
            SongNote(67, QUARTER_FRAMES),
            SongNote(69, EIGHTH_FRAMES),
            SongNote(72, EIGHTH_FRAMES),
            SongNote(74, DOTTED_QUARTER_FRAMES),
            SongNote(72, QUARTER_FRAMES),
            SongNote(69, EIGHTH_FRAMES),
            SongNote(67, WHOLE_FRAMES),
        )
    ),
    # A'': 6モーラ(Aの短縮リプライズ)
    SongPhrase(
        notes=(
            SongNote(72, QUARTER_FRAMES),
            SongNote(76, DOTTED_QUARTER_FRAMES),
            SongNote(74, EIGHTH_FRAMES),
            SongNote(72, EIGHTH_FRAMES),
            SongNote(69, QUARTER_FRAMES),
            SongNote(72, DOTTED_HALF_FRAMES),
        )
    ),
)

# 各フレーズのモーラ数バジェット(8, 8, 8, 6)。MELODY_TEMPLATEから導出する。
PHRASE_MORA_BUDGETS: tuple[int, ...] = tuple(len(phrase.notes) for phrase in MELODY_TEMPLATE)

# フォールバック歌詞。GEMINI_PROJECT未設定時・生成失敗時に使う。
# 各フレーズのモーラ数はPHRASE_MORA_BUDGETS(8, 8, 8, 6)と厳密に一致させてある。
FALLBACK_LYRICS: list[str] = [
    "こんしゅうもげんき",  # こ・ん・しゅ・う・も・げ・ん・き = 8モーラ
    "えーあいのはなし",  # え・ー・あ・い・の・は・な・し = 8モーラ
    "いっしょにきこうよ",  # い・っ・しょ・に・き・こ・う・よ = 8モーラ
    "たのしいのだ",  # た・の・し・い・の・だ = 6モーラ
]

# 拗音(小書きの母音・ワ行)。直前の文字と結合して1モーラになる。
_SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
# ひらがな・カタカナ・長音符のみを許可する(ん・っ・ーは単独モーラとして扱う)
_KANA_CHAR_RE = re.compile(r"[ぁ-んァ-ヶー]")
# 歌詞テキストの正規化で取り除く記号類
_LYRIC_PUNCT_RE = re.compile(r"[\s、。！？!?・…「」『』,.]")


def split_moras(text: str) -> list[str]:
    """かなテキストをモーラ単位に分割する。

    拗音(ゃゅょぁぃぅぇぉゎとカタカナ相当)は直前の文字と結合して1モーラになる。
    ん/ン、っ/ッ、ーはそれぞれ単独で1モーラとして数える。
    かな(ひらがな・カタカナ・長音符)以外の文字が含まれる場合はValueError。
    """
    moras: list[str] = []
    for char in text:
        if not _KANA_CHAR_RE.match(char):
            raise ValueError(f"かな以外の文字が含まれています: {char!r}")
        if char in _SMALL_KANA and moras:
            moras[-1] += char
        else:
            moras.append(char)
    return moras


def count_moras(text: str) -> int:
    return len(split_moras(text))


def normalize_lyric_text(text: str) -> str:
    """Gemini出力に混じりうる空白・句読点等を取り除き、モーラ判定できる形に整える。"""
    return _LYRIC_PUNCT_RE.sub("", text.strip())


# かな1文字 → 母音のひらがな。長音符「ー」をノート歌詞に変換するときに使う。
_VOWEL_BY_KANA: dict[str, str] = {}
for _vowel, _kana_chars in {
    "あ": "あかがさざただなはばぱまやらわゃぁアカガサザタダナハバパマヤラワャァヮゎ",
    "い": "いきぎしじちぢにひびぴみりぃイキギシジチヂニヒビピミリィ",
    "う": "うくぐすずつづぬふぶぷむゆるゅぅウクグスズツヅヌフブプムユルュゥヴ",
    "え": "えけげせぜてでねへべぺめれぇエケゲセゼテデネヘベペメレェ",
    "お": "おこごそぞとどのほぼぽもよろをょぉオコゴソゾトドノホボポモヨロヲョォ",
}.items():
    for _ch in _kana_chars:
        _VOWEL_BY_KANA[_ch] = _vowel


def _note_lyric(mora: str, prev_lyric: str | None) -> str:
    """ノートに載せる歌詞を返す。

    長音符「ー」はVOICEVOXの歌唱合成が歌詞として受け付けない
    (mora_kana_to_mora_phonemesに存在せず400になる)ため、
    直前ノートの歌詞の母音に置き換える(えー→ええ、ニュー→ニュう)。
    """
    if mora != "ー":
        return mora
    if prev_lyric:
        vowel = _VOWEL_BY_KANA.get(prev_lyric[-1])
        if vowel:
            return vowel
    # フレーズ先頭のーなど母音を解決できない場合の保険
    return "あ"


def assign_lyrics_to_notes(phrase_text: str, phrase: SongPhrase) -> list[dict]:
    """フレーズの歌詞テキストをノート列に割り当て、VOICEVOXのノート形式で返す。"""
    moras = split_moras(phrase_text)
    if len(moras) != len(phrase.notes):
        raise ValueError(
            f"モーラ数が一致しません: text={len(moras)}モーラ, notes={len(phrase.notes)}ノート"
        )
    lyrics: list[str] = []
    for mora in moras:
        lyrics.append(_note_lyric(mora, lyrics[-1] if lyrics else None))
    return [
        {"key": note.key, "frame_length": note.frame_length, "lyric": lyric}
        for note, lyric in zip(phrase.notes, lyrics)
    ]


def _rest_note(frame_length: int) -> dict:
    return {"key": None, "frame_length": frame_length, "lyric": ""}


def build_score(phrases: list[str]) -> dict:
    """フレーズごとの歌詞テキストからVOICEVOXのScore全体を組み立てる。"""
    if len(phrases) != len(MELODY_TEMPLATE):
        raise ValueError(
            f"フレーズ数が一致しません: phrases={len(phrases)}, template={len(MELODY_TEMPLATE)}"
        )

    notes: list[dict] = [_rest_note(LEADING_REST_FRAMES)]
    last_index = len(MELODY_TEMPLATE) - 1
    for i, (phrase_text, phrase) in enumerate(zip(phrases, MELODY_TEMPLATE)):
        notes.extend(assign_lyrics_to_notes(phrase_text, phrase))
        if i < last_index:
            notes.append(_rest_note(INTER_PHRASE_REST_FRAMES))
    notes.append(_rest_note(TRAILING_REST_FRAMES))
    return {"notes": notes}


def phrase_timings(phrases: list[str]) -> list[tuple[str, float]]:
    """字幕(SRT)用に、各フレーズの(テキスト, 秒数)を返す。

    先頭に歌い出し前の無音を("", 秒数)として含める。
    フレーズ間の無音はそれぞれ「直前のフレーズ」の秒数に合算し、
    歌い終わり後の無音は最後のフレーズの秒数に合算する
    (= エントリ数は 1(先頭無音) + フレーズ数 になる)。
    """
    if len(phrases) != len(MELODY_TEMPLATE):
        raise ValueError(
            f"フレーズ数が一致しません: phrases={len(phrases)}, template={len(MELODY_TEMPLATE)}"
        )

    timings: list[tuple[str, float]] = [("", LEADING_REST_FRAMES / FRAMES_PER_SECOND)]
    last_index = len(MELODY_TEMPLATE) - 1
    for i, (phrase_text, phrase) in enumerate(zip(phrases, MELODY_TEMPLATE)):
        frames = sum(note.frame_length for note in phrase.notes)
        frames += INTER_PHRASE_REST_FRAMES if i < last_index else TRAILING_REST_FRAMES
        timings.append((phrase_text, frames / FRAMES_PER_SECOND))
    return timings


def _build_lyrics_prompt(headlines: str, budgets: tuple[int, ...], feedback: str) -> str:
    return (
        "あなたは「ずんだもん」というキャラクターです。"
        "以下は今週のAIニュースの見出し一覧です。\n"
        f"{headlines}\n\n"
        "これらの内容を踏まえて、ずんだもんニュースソングの歌詞を4フレーズ作ってください。\n"
        "この曲は番組のオープニングテーマ曲のように、聞いた人が思わず口ずさみたくなる"
        "キャッチーで元気な曲にしてください。\n"
        "今週の実際のニュースに登場した製品名・サービス名・企業名を、できるだけかな表記"
        "(カタカナ読み)で歌詞に織り込み、聞くだけで今週何が話題だったか伝わるようにしてください。\n"
        "口調: 一人称は「ボク」、できるだけ語尾に「〜のだ」「〜なのだ」を使う、"
        "ずんだもんらしい明るく元気な口調を保つこと。\n"
        "表記: ひらがな・カタカナ・長音符(ー)のみを使うこと。漢字・英字・句読点は一切使わないこと。\n"
        f"モーラ数(拍数)は各フレーズ厳守: "
        f"1フレーズ目{budgets[0]}モーラ、2フレーズ目{budgets[1]}モーラ、"
        f"3フレーズ目{budgets[2]}モーラ、4フレーズ目{budgets[3]}モーラ。\n"
        "モーラの数え方: 拗音(ゃゅょぁぃぅぇぉゎ等)は直前の文字と合わせて1モーラ、"
        "「ん」「っ」「ー」(長音符)はそれぞれ単独で1モーラとして数えてください。\n"
        "各フレーズにつき、モーラ数条件を満たす言い回しの候補を3つずつ考えてください"
        "(内容が近くても構わないので、言い回しを変えてモーラ数を厳守しやすくしてください)。\n"
        f"{feedback}"
        '出力はJSONのみ: {"phrases": '
        '[["候補1","候補2","候補3"], ["候補1","候補2","候補3"], '
        '["候補1","候補2","候補3"], ["候補1","候補2","候補3"]]}\n'
        "説明は不要です。JSONのみ返してください。"
    )


def _select_candidate(raw_candidates: object, budget: int) -> tuple[str | None, list[str]]:
    """1スロット分の候補群から、モーラ数条件を満たす最初の候補を選ぶ。

    戻り値は (採用した歌詞 or None, 却下理由のリスト)。
    """
    if isinstance(raw_candidates, str):
        candidates = [raw_candidates]
    elif isinstance(raw_candidates, list):
        candidates = [str(c) for c in raw_candidates]
    else:
        return None, ["候補が配列/文字列のいずれでもありません。"]

    rejections: list[str] = []
    for candidate in candidates:
        cleaned = normalize_lyric_text(candidate)
        try:
            actual = count_moras(cleaned)
        except ValueError:
            rejections.append(f"「{cleaned}」はかな以外の文字を含んでいます。")
            continue
        if actual != budget:
            rejections.append(f"「{cleaned}」は{actual}モーラですが{budget}モーラ必要です。")
            continue
        return cleaned, rejections

    return None, rejections


async def generate_song_lyrics(draft: VideoPlanDraft) -> list[str]:
    """今週のAIニュース見出しから、ずんだもんニュースソングの歌詞をGeminiで生成する。

    フレーズごとに3つの候補をGeminiへ求め、モーラ数条件を満たす最初の候補を採用する。
    最大5回リトライし、未解決のスロットのみ次のリクエストで再挑戦する。
    5回試しても埋まらないスロットはFALLBACK_LYRICSの該当フレーズで個別に補い、
    Geminiから1つも得られなかった場合は従来通りFALLBACK_LYRICS全体を返す。
    GEMINI_PROJECT未設定時もFALLBACK_LYRICSを返す。
    """
    if not settings.GEMINI_PROJECT:
        return FALLBACK_LYRICS

    budgets = PHRASE_MORA_BUDGETS
    best: list[str | None] = [None] * len(budgets)

    try:
        vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
        model = GenerativeModel("gemini-2.5-flash")

        headlines = "\n".join(
            f"- {seg.title_ja or seg.headline}" for seg in draft.segments
        )

        feedback = ""
        for attempt in range(1, 6):
            unsolved = [i for i, v in enumerate(best) if v is None]
            if not unsolved:
                break

            prompt = _build_lyrics_prompt(headlines, budgets, feedback)

            try:
                response = await model.generate_content_async(prompt)
                text = response.text.strip()
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
                result = json.loads(text.strip())
            except Exception:
                logger.warning(
                    "song lyric generation: attempt %d request/parse failed", attempt,
                    exc_info=True,
                )
                feedback = "直前の出力はJSONとして解析できませんでした。JSONのみを返してください。\n"
                continue

            raw_phrases = result.get("phrases") if isinstance(result, dict) else None
            if not isinstance(raw_phrases, list) or len(raw_phrases) != len(budgets):
                feedback = (
                    "フレーズは4つのJSON配列(各要素はさらに候補文字列を3つ持つ配列)で"
                    "返してください。\n"
                )
                continue

            mismatches: list[str] = []
            for i, raw_candidates in enumerate(raw_phrases):
                if best[i] is not None:
                    continue
                selected, rejections = _select_candidate(raw_candidates, budgets[i])
                if selected is not None:
                    best[i] = selected
                    continue
                detail = " / ".join(rejections) if rejections else "候補が得られませんでした。"
                mismatches.append(f"フレーズ{i + 1}({budgets[i]}モーラ必要): {detail}")

            if not mismatches:
                break

            feedback = (
                "前回の出力でまだ解決できていないフレーズがあります。該当フレーズのみ、"
                "新しい候補を3つずつ考え直してください:\n" + "\n".join(mismatches) + "\n"
                "モーラの数え方(再掲): 拗音(ゃゅょぁぃぅぇぉゎ等)は直前の文字と合わせて"
                "1モーラ、「ん」「っ」「ー」はそれぞれ単独で1モーラです。\n"
            )

        solved_count = sum(1 for v in best if v is not None)
        if solved_count == 0:
            logger.warning("song lyric generation: no phrase resolved; using FALLBACK_LYRICS entirely")
            return FALLBACK_LYRICS

        fallback_slots = [i for i, v in enumerate(best) if v is None]
        final_lyrics = [
            v if v is not None else FALLBACK_LYRICS[i] for i, v in enumerate(best)
        ]
        if fallback_slots:
            logger.warning(
                "song lyric generation: slots %s fell back to FALLBACK_LYRICS", fallback_slots
            )
        logger.info(
            "song lyrics generated: %r (%d/%d phrases from Gemini)",
            final_lyrics,
            len(budgets) - len(fallback_slots),
            len(budgets),
        )
        return final_lyrics
    except Exception:
        logger.exception("song lyric generation failed; falling back to FALLBACK_LYRICS")
        return FALLBACK_LYRICS


# check_song_support()の結果をキャッシュするモジュール変数。
# 対応確認済み(True)のみ恒久的にキャッシュする。False・例外時はキャッシュせず、
# 次回呼び出しで再チェックする(コールドスタート直後の一時的な失敗で歌コーナーが
# 永久に無効化されるのを防ぐため)。
_song_support_cache: bool | None = None


async def check_song_support() -> bool:
    """VOICEVOXが歌唱合成(singing_teacher/frame_decode)に対応しているか確認する。"""
    global _song_support_cache
    if _song_support_cache:
        return _song_support_cache

    try:
        async with httpx.AsyncClient(base_url=settings.VOICEVOX_BASE_URL, timeout=10.0) as client:
            res = await client.get("/singers")
            res.raise_for_status()
            singers = res.json()

        has_teacher = False
        has_frame_decode = False
        for singer in singers:
            for style in singer.get("styles", []):
                style_type = style.get("type")
                if style_type in ("singing_teacher", "sing"):
                    has_teacher = True
                elif style_type == "frame_decode":
                    has_frame_decode = True

        supported = has_teacher and has_frame_decode
        if not supported:
            logger.warning(
                "VOICEVOX has no singing_teacher/frame_decode style; song corner disabled "
                "(singers=%r)",
                singers,
            )
        _song_support_cache = supported
    except Exception:
        logger.exception("check_song_support failed; will retry on next call")
        return False

    return _song_support_cache


async def _resolve_style_ids() -> tuple[int, int]:
    """歌唱教師(singing_teacher)とハミング(frame_decode)のスタイルIDを解決する。

    settingsの値が/singersに実在しかつ型が一致すればそれを優先し、
    そうでなければ最初に見つかった該当スタイルを採用する。
    """
    async with httpx.AsyncClient(base_url=settings.VOICEVOX_BASE_URL, timeout=10.0) as client:
        res = await client.get("/singers")
        res.raise_for_status()
        singers = res.json()

    teacher_id: int | None = None
    frame_decode_id: int | None = None
    for singer in singers:
        for style in singer.get("styles", []):
            style_type = style.get("type")
            style_id = style.get("id")
            if style_id is None:
                continue
            if style_type in ("singing_teacher", "sing"):
                if style_id == settings.VOICEVOX_SING_TEACHER_ID:
                    teacher_id = style_id
                elif teacher_id is None:
                    teacher_id = style_id
            elif style_type == "frame_decode":
                if style_id == settings.VOICEVOX_SING_SPEAKER_ID:
                    frame_decode_id = style_id
                elif frame_decode_id is None:
                    frame_decode_id = style_id

    if teacher_id is None or frame_decode_id is None:
        raise RuntimeError("VOICEVOXが歌唱合成(singing_teacher/frame_decode)に対応していません")
    logger.debug("Resolved song synthesis styles: teacher_id=%d, frame_decode_id=%d", teacher_id, frame_decode_id)
    return teacher_id, frame_decode_id


async def synthesize_song(phrases: list[str], out_path: Path) -> list[tuple[str, float]]:
    """歌詞フレーズからVOICEVOXの歌唱合成を呼び出し、wavをout_pathへ書き出す。

    通常のナレーション音声パイプライン(_synthesize_voice/_concat_wavs)と結合できるよう、
    サンプリングレート・チャンネル数を合わせる。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    teacher_id, frame_decode_id = await _resolve_style_ids()
    score = build_score(phrases)

    async with httpx.AsyncClient(base_url=settings.VOICEVOX_BASE_URL, timeout=60.0) as client:
        query_res = await client.post(
            "/sing_frame_audio_query",
            params={"speaker": teacher_id},
            json=score,
        )
        try:
            query_res.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "sing_frame_audio_query failed with status %d for speaker %d; response: %s",
                e.response.status_code,
                teacher_id,
                e.response.text[:500] if hasattr(e.response, "text") else str(e),
            )
            raise
        frame_query = query_res.json()

        # ナレーション音声パイプラインの出力(24000Hz・モノラル)に合わせる
        frame_query["outputSamplingRate"] = 24000
        if "outputStereo" in frame_query:
            frame_query["outputStereo"] = False

        synth_res = await client.post(
            "/frame_synthesis",
            params={"speaker": frame_decode_id},
            json=frame_query,
        )
        try:
            synth_res.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                "frame_synthesis failed with status %d for speaker %d; response: %s",
                e.response.status_code,
                frame_decode_id,
                e.response.text[:500] if hasattr(e.response, "text") else str(e),
            )
            raise

    out_path.write_bytes(synth_res.content)
    return phrase_timings(phrases)
