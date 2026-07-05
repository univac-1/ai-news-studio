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
    ACTION_MAX_CHARS,
    HOOK_MAX_CHARS,
    IMPACT_MAX_CHARS,
    OPENING_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    TITLE_JA_MAX_CHARS,
    contains_japanese,
    shorten,
    template_hook,
    template_intro,
    template_intro_line,
    template_reaction_line,
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


def _over_budget(seg: VideoSegment) -> bool:
    """1行要約・Impact・Actionのいずれかが文字数バジェットを超過しているか。"""
    return (
        len(seg.summary) > SUMMARY_MAX_CHARS
        or len(seg.impact) > IMPACT_MAX_CHARS
        or len(seg.action) > ACTION_MAX_CHARS
    )


async def _fetch_meta_completions(segments: list[VideoSegment]) -> list[dict] | None:
    """title_ja等が不正なセグメントを1回のGemini呼び出しでまとめて補完する。

    失敗・件数不一致時はNoneを返し、呼び出し側は既存値のフォールバック処理に進む。
    """
    try:
        vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
        model = GenerativeModel("gemini-2.5-flash")

        segments_text = "\n\n".join(
            f"### セグメント{seg.number}\n見出し: {seg.headline}\n要約: {seg.summary}\n"
            f"インパクト: {seg.impact}\nアクション: {seg.action}"
            for seg in segments
        )

        prompt = (
            "以下のAIニュース動画セグメントについて、各セグメントの表示用メタ情報を補完してください。\n\n"
            f"{segments_text}\n\n"
            "各セグメントについて次の6つを生成:\n"
            f"- title_ja: スライド表示用の短い日本語タイトル。{TITLE_JA_MAX_CHARS}文字以内。"
            "英語見出しは意味を保って日本語化する。誇張・事実改変は禁止。\n"
            "- visual: 画面を補足する図解データ。該当する場合のみ。\n"
            '  セキュリティ系: {"type":"flow","items":["攻撃の起点","経路・手口","ガードレール回避","影響・被害"]} '
            "のような3〜4ステップ(各14文字以内)。\n"
            '  開発ツール系: {"type":"command","items":["コマンド例や利用イメージ(各40文字以内、最大3行)"]}。\n'
            "  どちらにも該当しない場合は null。無理に作らない。\n"
            "- rank_reason: このニュースがなぜ重要かの一言理由。20文字以内。\n"
            f"- summary: 元の要約を一行要約にした版。{SUMMARY_MAX_CHARS}文字以内。\n"
            f"- impact: 元のインパクトを要約した版。視聴者への影響。{IMPACT_MAX_CHARS}文字以内。\n"
            f"- action: 元のアクションを要約した版。視聴者が次にやること。{ACTION_MAX_CHARS}文字以内。\n"
            "summary/impact/actionは元の文の事実を改変せず短く要約すること。誇張は禁止。\n\n"
            "出力はJSONのみ:\n"
            '{"segments": [{"title_ja": "...", "visual": {"type": "flow", "items": ["...", "...", "..."]}, '
            '"rank_reason": "...", "summary": "...", "impact": "...", "action": "..."}, ...]}\n'
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

    # 2. title_ja検証 + 文字数バジェット検証。無効・超過なセグメントが1件でもあり、
    # GEMINI_PROJECTが設定されていればメタ補完のGemini呼び出しを1回だけ実行する
    # (件数不一致・例外時は無視して続行)。
    needs_meta = any(not _valid_title_ja(seg.title_ja) or _over_budget(seg) for seg in segments)
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

                # summary/impact/actionは、元の値がバジェット超過のフィールドについてのみ、
                # meta側が非空・バジェット+5文字以内なら採用する。バジェット内の元の値は上書きしない。
                if len(seg.summary) > SUMMARY_MAX_CHARS:
                    raw_summary = meta.get("summary")
                    if (
                        isinstance(raw_summary, str)
                        and raw_summary.strip()
                        and len(raw_summary.strip()) <= SUMMARY_MAX_CHARS + 5
                    ):
                        update["summary"] = raw_summary.strip()

                if len(seg.impact) > IMPACT_MAX_CHARS:
                    raw_impact = meta.get("impact")
                    if (
                        isinstance(raw_impact, str)
                        and raw_impact.strip()
                        and len(raw_impact.strip()) <= IMPACT_MAX_CHARS + 5
                    ):
                        update["impact"] = raw_impact.strip()

                if len(seg.action) > ACTION_MAX_CHARS:
                    raw_action = meta.get("action")
                    if (
                        isinstance(raw_action, str)
                        and raw_action.strip()
                        and len(raw_action.strip()) <= ACTION_MAX_CHARS + 5
                    ):
                        update["action"] = raw_action.strip()

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

    # 4. フック/イントロの文字数強制。空・超過・非日本語なら決定論的なテンプレートに置き換える。
    # 日本語チェックがないと、上流でGeminiの翻訳前フックが残っていても
    # (文字数だけは規約内のため)素通りしてしまう。
    hook = draft.hook
    if not hook or len(hook) > HOOK_MAX_CHARS + 10 or not contains_japanese(hook):
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
            # 理由行は1行(約40字)まで描けるため、切り詰めは保険程度にとどめる
            reason = shorten(seg.impact.split("。")[0], 40) if seg.impact else ""
            seg = seg.model_copy(update={"rank_reason": reason})
        with_rank_reason.append(seg)
    segments = with_rank_reason

    # 7. intro_line/reaction_line補完。空なら決定論的テンプレートでずんだもんの発話を作る
    with_intro_line = []
    for seg in segments:
        updates: dict = {}
        if not seg.intro_line:
            updates["intro_line"] = template_intro_line(seg.number, seg.title_ja)
        if not seg.reaction_line:
            updates["reaction_line"] = template_reaction_line(seg.number)
        if updates:
            seg = seg.model_copy(update=updates)
        with_intro_line.append(seg)
    segments = with_intro_line

    return draft.model_copy(update={"segments": segments, "hook": hook, "intro": intro})
