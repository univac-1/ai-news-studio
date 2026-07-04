"""動画生成直前にドラフトの品質を決定論的に保証する層。

週次ドラフトは古いまま再利用されたり、Gemini(polish_narration)の出力が
文字数指定やタイトルの日本語化を無視したまま latest_draft.json に保存されている
ことがある。ここでの検証・補完はドラフトの鮮度や Gemini 出力品質に依存せず、
動画生成のたびに決定論的なルールで最低限の品質(冒頭の尺、日本語タイトル、
文字数バジェット)を保証する。フレッシュで規約準拠のドラフトであれば
実質パススルーとなり、Gemini呼び出しも発生しない。
"""

import json
import re

import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings
from ..schemas.draft import SegmentVisual, VideoPlanDraft, VideoSegment
from .categorize import categorize_text
from .generate_weekly_video_plan import (
    HOOK_MAX_CHARS,
    OPENING_MAX_CHARS,
    TITLE_JA_MAX_CHARS,
    contains_japanese,
    shorten,
    template_hook,
    template_intro,
)
from .polish_narration import _parse_visual

# セキュリティカテゴリのニュースでも、防御ツールの紹介記事などには汎用攻撃フローを
# 付けたくない。攻撃・被害を示すキーワードを含む場合のみ図解を補う。
_ATTACK_KEYWORDS = (
    "攻撃", "attack", "exploit", "injection", "乗っ取", "漏えい", "漏洩", "フィッシング", "phishing",
)
_SECURITY_FALLBACK_FLOW = ["悪意あるWebサイト", "AIブラウザ/LLM", "ガードレール回避", "情報漏えいリスク"]


def _valid_title_ja(t: str) -> bool:
    return bool(t) and contains_japanese(t) and len(t) <= TITLE_JA_MAX_CHARS + 4


async def _fetch_meta_completions(segments: list[VideoSegment]) -> list[dict] | None:
    """title_ja等が不正なセグメントを1回のGemini呼び出しでまとめて補完する。

    失敗・件数不一致時はNoneを返し、呼び出し側は既存値のフォールバック処理に進む。
    """
    try:
        vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
        model = GenerativeModel("gemini-2.5-flash")

        segments_text = "\n\n".join(
            f"### セグメント{seg.number}\n見出し: {seg.headline}\n要約: {seg.summary}"
            for seg in segments
        )

        prompt = (
            "以下のAIニュース動画セグメントについて、各セグメントの表示用メタ情報を補完してください。\n\n"
            f"{segments_text}\n\n"
            "各セグメントについて次の3つを生成:\n"
            f"- title_ja: スライド表示用の短い日本語タイトル。{TITLE_JA_MAX_CHARS}文字以内。"
            "英語見出しは意味を保って日本語化する。誇張・事実改変は禁止。\n"
            "- visual: 画面を補足する図解データ。該当する場合のみ。\n"
            '  セキュリティ系: {"type":"flow","items":["攻撃の起点","経路・手口","ガードレール回避","影響・被害"]} '
            "のような3〜4ステップ(各14文字以内)。\n"
            '  開発ツール系: {"type":"command","items":["コマンド例や利用イメージ(各40文字以内、最大3行)"]}。\n'
            "  どちらにも該当しない場合は null。無理に作らない。\n"
            "- rank_reason: このニュースがなぜ重要かの一言理由。20文字以内。\n\n"
            "出力はJSONのみ:\n"
            '{"segments": [{"title_ja": "...", "visual": {"type": "flow", "items": ["...", "...", "..."]}, '
            '"rank_reason": "..."}, ...]}\n'
            f"segmentsはセグメントと同数・同順({len(segments)}件)で返してください。説明は不要です。"
        )

        response = await model.generate_content_async(prompt)
        text = response.text.strip()

        # Strip markdown code fences if present
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

        result = json.loads(text.strip())
        raw_segments = result.get("segments")
        if not isinstance(raw_segments, list) or len(raw_segments) != len(segments):
            return None
        return raw_segments
    except Exception:
        return None


async def prepare_draft_for_video(draft: VideoPlanDraft) -> VideoPlanDraft:
    segments = list(draft.segments)

    # 1. category補完: 未設定なら見出しから決定論的に判定する
    segments = [
        seg if seg.category else seg.model_copy(update={"category": categorize_text(seg.headline)})
        for seg in segments
    ]

    # 2. title_ja検証。無効なセグメントが1件でもあり、GEMINI_PROJECTが設定されていれば
    # メタ補完のGemini呼び出しを1回だけ実行する(件数不一致・例外時は無視して続行)。
    needs_meta = any(not _valid_title_ja(seg.title_ja) for seg in segments)
    if needs_meta and settings.GEMINI_PROJECT:
        completions = await _fetch_meta_completions(segments)
        if completions is not None:
            updated_segments = []
            for seg, meta in zip(segments, completions):
                if not isinstance(meta, dict):
                    updated_segments.append(seg)
                    continue
                update: dict = {}

                raw_title_ja = meta.get("title_ja")
                if isinstance(raw_title_ja, str) and _valid_title_ja(raw_title_ja.strip()):
                    title_ja = raw_title_ja.strip()
                    update["title_ja"] = title_ja
                    update["slide_title"] = f"#{seg.number} {title_ja}"

                visual = _parse_visual(meta.get("visual"))
                if visual is not None:
                    update["visual"] = visual

                raw_rank_reason = meta.get("rank_reason")
                if (
                    isinstance(raw_rank_reason, str)
                    and raw_rank_reason.strip()
                    and len(raw_rank_reason.strip()) <= 20
                ):
                    update["rank_reason"] = raw_rank_reason.strip()

                updated_segments.append(seg.model_copy(update=update) if update else seg)
            segments = updated_segments

    # 3. title_ja最終フォールバック。まだ無効ならheadlineを機械的に短縮する
    fallback_applied = []
    for seg in segments:
        if not _valid_title_ja(seg.title_ja):
            title_ja = shorten(seg.headline, TITLE_JA_MAX_CHARS)
            seg = seg.model_copy(
                update={"title_ja": title_ja, "slide_title": f"#{seg.number} {title_ja}"}
            )
        fallback_applied.append(seg)
    segments = fallback_applied

    # 4. フック/イントロの文字数強制。空・超過なら決定論的なテンプレートに置き換える
    hook = draft.hook
    if not hook or len(hook) > HOOK_MAX_CHARS + 10:
        hook = template_hook(segments[0].title_ja) if segments else template_hook("")
    intro = draft.intro
    if len(intro) > OPENING_MAX_CHARS + 15:
        intro = template_intro(len(segments))

    # 5. セキュリティ図解の保険。防御ツールの紹介ニュースにまで汎用攻撃フローを
    # 付けないよう、攻撃・被害系キーワードを含む場合のみ付与する。
    with_security_visual = []
    for seg in segments:
        if seg.category == "security" and seg.visual is None:
            haystack = f"{seg.headline} {seg.summary}".lower()
            if any(keyword in haystack for keyword in _ATTACK_KEYWORDS):
                seg = seg.model_copy(
                    update={"visual": SegmentVisual(type="flow", items=_SECURITY_FALLBACK_FLOW)}
                )
        with_security_visual.append(seg)
    segments = with_security_visual

    # 6. rank_reason補完。空ならimpactの先頭文から一言理由を作る
    with_rank_reason = []
    for seg in segments:
        if not seg.rank_reason:
            reason = shorten(seg.impact.split("。")[0], 22) if seg.impact else ""
            seg = seg.model_copy(update={"rank_reason": reason})
        with_rank_reason.append(seg)
    segments = with_rank_reason

    return draft.model_copy(update={"segments": segments, "hook": hook, "intro": intro})
