"""ずんだもんニュースソング(週次まとめの歌唱パート)を生成するサービス。

- MELODY_TEMPLATE: オリジナルのメロディ(4フレーズ、A-A'-B-A''構成)
- モーラ分割・歌詞割り当て・スコア組み立て(純粋関数、I/Oなし)
- Geminiによる歌詞生成(GEMINI_PROJECT未設定・失敗時はFALLBACK_LYRICSへフォールバック)
- VOICEVOXの歌唱合成(sing_frame_audio_query → frame_synthesis)呼び出し
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings
from ..schemas.draft import VideoPlanDraft

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


def assign_lyrics_to_notes(phrase_text: str, phrase: SongPhrase) -> list[dict]:
    """フレーズの歌詞テキストをノート列に割り当て、VOICEVOXのノート形式で返す。"""
    moras = split_moras(phrase_text)
    if len(moras) != len(phrase.notes):
        raise ValueError(
            f"モーラ数が一致しません: text={len(moras)}モーラ, notes={len(phrase.notes)}ノート"
        )
    return [
        {"key": note.key, "frame_length": note.frame_length, "lyric": mora}
        for note, mora in zip(phrase.notes, moras)
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


async def generate_song_lyrics(draft: VideoPlanDraft) -> list[str]:
    """今週のAIニュース見出しから、ずんだもんニュースソングの歌詞をGeminiで生成する。

    GEMINI_PROJECT未設定・生成失敗・最終的にモーラ数が合わない場合はFALLBACK_LYRICSを返す。
    """
    if not settings.GEMINI_PROJECT:
        return FALLBACK_LYRICS

    try:
        vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
        model = GenerativeModel("gemini-2.5-flash")

        headlines = "\n".join(
            f"- {seg.title_ja or seg.headline}" for seg in draft.segments
        )
        budgets = PHRASE_MORA_BUDGETS

        feedback = ""
        for _ in range(3):
            prompt = (
                "あなたは「ずんだもん」というキャラクターです。"
                "以下は今週のAIニュースの見出し一覧です。\n"
                f"{headlines}\n\n"
                "これらの内容を踏まえて、ずんだもんニュースソングの歌詞を4フレーズ作ってください。\n"
                "口調: 一人称は「ボク」、できるだけ語尾に「〜のだ」「〜なのだ」を使う、明るく元気な口調。\n"
                "表記: ひらがな・カタカナ・長音符(ー)のみを使うこと。漢字・英字・句読点は一切使わないこと。\n"
                f"モーラ数(拍数)は各フレーズ厳守: "
                f"1フレーズ目{budgets[0]}モーラ、2フレーズ目{budgets[1]}モーラ、"
                f"3フレーズ目{budgets[2]}モーラ、4フレーズ目{budgets[3]}モーラ。\n"
                "拗音(ゃゅょぁぃぅぇぉゎ等)は直前の文字と合わせて1モーラ、"
                "「ん」「っ」「ー」はそれぞれ1モーラとして数えてください。\n"
                f"{feedback}"
                '出力はJSONのみ: {"phrases": ["...", "...", "...", "..."]}\n'
                "説明は不要です。JSONのみ返してください。"
            )

            response = await model.generate_content_async(prompt)
            text = response.text.strip()
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            result = json.loads(text.strip())

            raw_phrases = result.get("phrases")
            if not isinstance(raw_phrases, list) or len(raw_phrases) != len(budgets):
                feedback = "フレーズは4つのJSON配列で返してください。\n"
                continue

            cleaned = [normalize_lyric_text(str(phrase)) for phrase in raw_phrases]

            mismatches: list[str] = []
            for i, (phrase_text, budget) in enumerate(zip(cleaned, budgets), start=1):
                try:
                    actual = count_moras(phrase_text)
                except ValueError:
                    mismatches.append(f"フレーズ{i}はかな以外の文字を含んでいます。")
                    continue
                if actual != budget:
                    mismatches.append(f"フレーズ{i}は{actual}モーラですが{budget}モーラ必要です。")

            if not mismatches:
                return cleaned

            feedback = "前回の出力の問題点:\n" + "\n".join(mismatches) + "\n"

        return FALLBACK_LYRICS
    except Exception:
        return FALLBACK_LYRICS


# check_song_support()の結果をキャッシュするモジュール変数
_song_support_cache: bool | None = None


async def check_song_support() -> bool:
    """VOICEVOXが歌唱合成(singing_teacher/frame_decode)に対応しているか確認する。"""
    global _song_support_cache
    if _song_support_cache is not None:
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
        _song_support_cache = has_teacher and has_frame_decode
    except Exception:
        _song_support_cache = False

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
        query_res.raise_for_status()
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
        synth_res.raise_for_status()

    out_path.write_bytes(synth_res.content)
    return phrase_timings(phrases)
