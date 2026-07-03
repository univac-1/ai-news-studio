import json
import re

import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings
from ..schemas.draft import VideoPlanDraft


async def polish_narration(draft: VideoPlanDraft) -> VideoPlanDraft:
    if not settings.GEMINI_PROJECT:
        return draft

    try:
        vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
        model = GenerativeModel("gemini-2.5-flash")

        segments_text = "\n".join(
            f"見出し: {seg.headline}\nナレーション: {seg.narration}"
            for seg in draft.segments
        )

        prompt = (
            f"以下のYouTube動画ドラフトのナレーション原稿を、自然な話し言葉に書き換えてください。\n\n"
            f"動画タイトル: {draft.title}\n\n"
            f"【イントロ】\n{draft.intro}\n\n"
            f"【セグメント一覧】\n{segments_text}\n\n"
            f"【アウトロ】\n{draft.outro}\n\n"
            "書き換えの指示:\n"
            "- intro・各セグメントのnarration・outroをYouTubeニュース動画向けの自然な話し言葉に書き換える\n"
            "- 「インパクト:」「あなたへのアクション:」のようなラベルの読み上げをやめ、内容を文章に織り込む\n"
            "- セグメント間に自然なつなぎ（「続いては〜」など）を入れる\n"
            "- 事実・固有名詞・数値は変えない。要約の内容（インパクト・アクションの情報）は削らず盛り込む\n"
            "- 音声合成で読み上げるため、記号・URL・英語の羅列を避け、読みやすい日本語にする\n\n"
            f'出力はJSONのみ: {{"intro": "...", "narrations": ["...", ...], "outro": "..."}}\n'
            f"narrationsはセグメントと同数・同順（{len(draft.segments)}件）で返してください。\n"
            "説明は不要です。JSONのみ返してください。"
        )

        response = await model.generate_content_async(prompt)
        text = response.text.strip()

        # Strip markdown code fences if present
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

        result = json.loads(text.strip())

        narrations: list[str] = result["narrations"]
        if len(narrations) != len(draft.segments):
            return draft

        new_intro: str = result["intro"]
        new_outro: str = result["outro"]

        new_segments = [
            seg.model_copy(update={"narration": narrations[i]})
            for i, seg in enumerate(draft.segments)
        ]

        narration_script = f"【イントロ】\n{new_intro}\n\n"
        for seg in new_segments:
            narration_script += f"{seg.narration}\n\n"
        narration_script += f"【アウトロ】\n{new_outro}"

        return draft.model_copy(
            update={
                "intro": new_intro,
                "outro": new_outro,
                "segments": new_segments,
                "narration_script": narration_script,
            }
        )

    except Exception:
        return draft
