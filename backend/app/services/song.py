"""ずんだもんニュースソング(週次まとめの歌唱パート)の歌詞を生成するサービス。

- モーラ分割・候補選定(純粋関数、I/Oなし)
- Geminiによる歌詞生成(GEMINI_PROJECT未設定・失敗時はFALLBACK_LYRICSへフォールバック)

歌唱・伴奏の音声とMV映像はVeoが一括生成する(video_assets.generate_song_clip)。
"""

import json
import logging
import re

import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings
from ..schemas.draft import VideoPlanDraft

logger = logging.getLogger(__name__)

# 各フレーズのモーラ数バジェット。歌としてリズムよく歌える長さに揃えるための
# 目安で、A-A'-B-A''構成(12, 12, 12, 10)を踏襲している。
PHRASE_MORA_BUDGETS: tuple[int, ...] = (12, 12, 12, 10)

# フォールバック歌詞。GEMINI_PROJECT未設定時・生成失敗時に使う。
# 各フレーズのモーラ数はPHRASE_MORA_BUDGETS(12, 12, 12, 10)と厳密に一致させてある。
FALLBACK_LYRICS: list[str] = [
    "ずんずんエーアイニュースだ",  # ず・ん・ず・ん・エ・ー・ア・イ・ニュ・ー・ス・だ = 12モーラ
    "ホットなわだいをチェックだ",  # ホ・ッ・ト・な・わ・だ・い・を・チェ・ッ・ク・だ = 12モーラ
    "だいじなポイントおさえる",  # だ・い・じ・な・ポ・イ・ン・ト・お・さ・え・る = 12モーラ
    "いっしょにチェックなのだ",  # い・っ・しょ・に・チェ・ッ・ク・な・の・だ = 10モーラ
]

# 2・3フレーズ目はニュースの具体性が必要なため、抽象的すぎる候補は採用しない。
_GENERIC_NEWS_PHRASES = (
    "いろんなニュース",
    "たくさんある",
    "すごいはなし",
    "すごいニュース",
    "わだいのニュース",
    "ホットなわだい",
    "みんなできいて",
)

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


def _build_lyrics_prompt(headlines: str, budgets: tuple[int, ...], feedback: str) -> str:
    return (
        "あなたは「ずんだもん」というキャラクターです。"
        "以下は今週のAIニュースの見出しと概要の一覧です。\n"
        f"{headlines}\n\n"
        "これらの内容を踏まえて、ずんだもんニュースソングの歌詞を4フレーズ作ってください。\n"
        "この曲は番組のオープニングテーマ曲のように、聞いた人が思わず口ずさみたくなる"
        "キャッチーで元気な曲にしてください。短い反復語や勢いのある言葉を優先し、"
        "説明文ではなくサビとして歌える言い回しにしてください。\n"
        "今週の実際のニュースに登場した製品名・サービス名・企業名を、できるだけかな表記"
        "(カタカナ読み)で歌詞に織り込み、聞くだけで今週何が話題だったか伝わるようにしてください。\n"
        "各フレーズの役割は次の通りです。必ず守ってください:\n"
        "・1フレーズ目: 番組の挨拶とつかみ(視聴者への呼びかけ、今週も始まるよ、といった導入)。\n"
        "・2フレーズ目、3フレーズ目: 今週の目玉ニュースを具体的に歌う。見出し・概要にある"
        "製品名・サービス名・企業名をできるだけかな表記で入れること。"
        "「いろんなニュース」「たくさんある」「すごいはなし」「わだいのニュース」"
        "「ホットなわだい」のような、何の話か分からない抽象的な表現は禁止です。\n"
        "・4フレーズ目: 締めの一言。「いっしょにチェックしよう」のような視聴者への呼びかけと"
        "「〜のだ」口調で締めくくること。\n"
        "口調: 一人称は「ボク」、できるだけ語尾に「〜のだ」「〜なのだ」を使う、"
        "ずんだもんらしい明るく元気な口調を保つこと。\n"
        "表記: ひらがな・カタカナ・長音符(ー)のみを使うこと。漢字・英字・句読点は一切使わないこと。\n"
        f"モーラ数(拍数)は各フレーズ厳守: "
        f"1フレーズ目{budgets[0]}モーラ、2フレーズ目{budgets[1]}モーラ、"
        f"3フレーズ目{budgets[2]}モーラ、4フレーズ目{budgets[3]}モーラ。\n"
        "モーラの数え方: 拗音(ゃゅょぁぃぅぇぉゎ等)は直前の文字と合わせて1モーラ、"
        "「ん」「っ」「ー」(長音符)はそれぞれ単独で1モーラとして数えてください。\n"
        "数え方の例: 「エーアイニュース」は エ・ー・ア・イ・ニュ・ー・ス で7モーラです"
        "(拗音「ニュ」は直前の「ニ」と結合して1モーラ、長音符「ー」はそれぞれ単独で1モーラ)。\n"
        "各フレーズにつき、モーラ数条件を満たす言い回しの候補を3つずつ考えてください"
        "(内容が近くても構わないので、言い回しを変えてモーラ数を厳守しやすくしてください)。\n"
        f"{feedback}"
        '出力はJSONのみ: {"phrases": '
        '[["候補1","候補2","候補3"], ["候補1","候補2","候補3"], '
        '["候補1","候補2","候補3"], ["候補1","候補2","候補3"]]}\n'
        "説明は不要です。JSONのみ返してください。"
    )


def _select_candidate(
    raw_candidates: object, budget: int, phrase_index: int | None = None
) -> tuple[str | None, list[str]]:
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
        if phrase_index in (1, 2) and any(
            generic in cleaned for generic in _GENERIC_NEWS_PHRASES
        ):
            rejections.append(f"「{cleaned}」はニュース内容が抽象的すぎます。")
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
            f"- 見出し: {seg.title_ja or seg.headline} / 概要: {seg.summary[:60]}"
            for seg in draft.segments
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
                selected, rejections = _select_candidate(raw_candidates, budgets[i], i)
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
