import json
import re

import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings
from ..schemas.news import NewsItem

MAX_ITEMS = 7


async def select_news_for_video(items: list[NewsItem]) -> list[NewsItem]:
    if len(items) <= MAX_ITEMS:
        return items

    if not settings.GEMINI_PROJECT:
        return items[:MAX_ITEMS]

    vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
    model = GenerativeModel("gemini-2.5-flash")

    items_text = "\n".join(
        f"ID:{item.id} | {item.title} | インパクト:{item.impact}"
        for item in items
    )

    prompt = (
        f"以下のAIニュース一覧から、YouTube動画（約10分）に最適な{MAX_ITEMS}件を選んでください。\n"
        "選定基準：\n"
        "- 読者への影響度が高い\n"
        "- モデルリリース・研究・ビジネス・規制など多様なトピック\n"
        "- 類似・重複するニュースは最も重要な1件に絞る\n\n"
        f"ニュース一覧：\n{items_text}\n\n"
        f'選んだニュースのIDだけを JSON 配列で返してください。例: ["id1","id2"]\n'
        "説明は不要です。JSONのみ返してください。"
    )

    response = await model.generate_content_async(prompt)
    text = response.text.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)

    selected_ids: list[str] = json.loads(text.strip())

    id_to_item = {item.id: item for item in items}
    result = [id_to_item[sid] for sid in selected_ids if sid in id_to_item]
    return result if result else items[:MAX_ITEMS]
