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
            "加えて、以下3つも生成してください:\n"
            "- hook: 動画の一番最初に流す10〜15秒のフック原稿。最もインパクトの大きいニュースを1つ選び、"
            "視聴者が「見ないと損」と感じる形でティザーする。煽りすぎない・事実は変えない。"
            "最後は「今週も重要ニュースをまとめてお届けします」のように本編へつなぐ。\n"
            "- title_candidates: YouTubeのタイトル案を3つ。最重要ニュースの具体的な内容を軸に、"
            "数字・ベネフィット・意外性のいずれかを含める。40文字以内。"
            "釣りタイトル（内容と乖離した誇張）は禁止。"
            "例: 「OpenAIがついに○○を発表、開発者の仕事はこう変わる【今週のAIニュース7選】」\n"
            "- thumbnail_text: サムネイル用の文言。1行目=最大8文字程度のパワーワード（例「GPT-5来た」）、"
            "2行目・3行目=補足（各12文字以内）。改行区切りで最大3行。\n\n"
            f'出力はJSONのみ: {{"intro": "...", "narrations": ["...", ...], "outro": "...", '
            f'"hook": "...", "title_candidates": ["...", "...", "..."], "thumbnail_text": "..."}}\n'
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

        # Defensively adopt hook, title_candidates, thumbnail_text from Gemini output.
        # Each field is only updated when Gemini returns a valid, non-empty value.
        raw_hook = result.get("hook")
        new_hook = raw_hook if isinstance(raw_hook, str) and raw_hook.strip() else draft.hook

        raw_candidates = result.get("title_candidates")
        if (
            isinstance(raw_candidates, list)
            and len(raw_candidates) >= 1
            and all(isinstance(c, str) for c in raw_candidates)
        ):
            new_title_candidates = raw_candidates
            new_title = raw_candidates[0]
        else:
            new_title_candidates = draft.title_candidates
            new_title = draft.title

        raw_thumbnail = result.get("thumbnail_text")
        new_thumbnail_text = (
            raw_thumbnail
            if isinstance(raw_thumbnail, str) and raw_thumbnail.strip()
            else draft.thumbnail_text
        )

        return draft.model_copy(
            update={
                "title": new_title,
                "title_candidates": new_title_candidates,
                "intro": new_intro,
                "outro": new_outro,
                "segments": new_segments,
                "narration_script": narration_script,
                "hook": new_hook,
                "thumbnail_text": new_thumbnail_text,
            }
        )

    except Exception:
        return draft
