import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from google import genai
from google.genai import types

from ..core.config import settings
from ..schemas.news import NewsItem


@dataclass
class NewsSearchRefreshResult:
    items: list[NewsItem]
    reference_urls: list[str]


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> dict | None:
    text = _strip_json_fences(text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start >= end:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _clean_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value
    return None


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def _grounding_urls(response: object) -> list[str]:
    urls: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        metadata = getattr(candidate, "grounding_metadata", None) or getattr(
            candidate, "groundingMetadata", None
        )
        if metadata is None:
            continue
        chunks = getattr(metadata, "grounding_chunks", None) or getattr(
            metadata, "groundingChunks", None
        ) or []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            url = _clean_url(getattr(web, "uri", None) if web is not None else None)
            if url:
                urls.append(url)
    return _dedupe_urls(urls)


def _build_prompt(items: list[NewsItem]) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    items_json = [
        {
            "id": item.id,
            "title": item.title,
            "url": item.url,
            "source": item.source,
            "published_at": item.published_at,
            "summary": item.summary,
            "impact": item.impact,
            "action": item.action,
            "provider": item.provider,
            "model_name": item.model_name,
            "tags": item.tags,
        }
        for item in items
    ]
    return (
        "You update AI news items for a Japanese weekly video script.\n"
        f"Today is {today} UTC. Use Google Search to verify whether each item has newer "
        "or corrected information after the article/feed was collected.\n\n"
        "Rules:\n"
        "- Keep the same item ids and item order.\n"
        "- Update facts only when search results support the update.\n"
        "- If no newer information is found, keep the original meaning.\n"
        "- Do not add speculation, hype, or uncited claims.\n"
        "- Write title, summary, impact, and action in concise Japanese.\n"
        "- summary should be one sentence, impact should explain why viewers care, "
        "and action should say what viewers should watch or do next.\n"
        "- Return JSON only.\n\n"
        "Input items:\n"
        f"{json.dumps(items_json, ensure_ascii=False)}\n\n"
        "Output schema:\n"
        "{"
        '"items":[{"id":"...","title":"...","summary":"...","impact":"...",'
        '"action":"...","reference_urls":["https://..."]}]'
        "}"
    )


def _refresh_news_with_search_sync(items: list[NewsItem]) -> NewsSearchRefreshResult:
    client = genai.Client(
        vertexai=True,
        project=settings.GEMINI_PROJECT,
        location=settings.GEMINI_LOCATION,
    )
    response = client.models.generate_content(
        model=settings.NEWS_SEARCH_MODEL,
        contents=_build_prompt(items),
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=6000,
            tools=[types.Tool(googleSearch=types.GoogleSearch())],
        ),
    )

    parsed = _extract_json_object(response.text or "")
    if parsed is None:
        return NewsSearchRefreshResult(items=items, reference_urls=_grounding_urls(response))

    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        return NewsSearchRefreshResult(items=items, reference_urls=_grounding_urls(response))

    by_id = {item.id: item for item in items}
    updates_by_id = {
        raw.get("id"): raw
        for raw in raw_items
        if isinstance(raw, dict) and raw.get("id") in by_id
    }

    refreshed: list[NewsItem] = []
    urls = _grounding_urls(response)
    for item in items:
        raw = updates_by_id.get(item.id)
        if not isinstance(raw, dict):
            refreshed.append(item)
            continue

        update: dict[str, str] = {}
        for field in ("title", "summary", "impact", "action"):
            value = raw.get(field)
            if isinstance(value, str) and value.strip():
                update[field] = value.strip()

        raw_urls = raw.get("reference_urls")
        if isinstance(raw_urls, list):
            urls.extend(url for raw_url in raw_urls if (url := _clean_url(raw_url)))

        refreshed.append(item.model_copy(update=update) if update else item)

    return NewsSearchRefreshResult(items=refreshed, reference_urls=_dedupe_urls(urls))


async def refresh_news_with_search(items: list[NewsItem]) -> NewsSearchRefreshResult:
    if not items or not settings.NEWS_SEARCH_REFRESH_ENABLED or not settings.GEMINI_PROJECT:
        return NewsSearchRefreshResult(items=items, reference_urls=[])

    try:
        return await asyncio.to_thread(_refresh_news_with_search_sync, items)
    except Exception:
        return NewsSearchRefreshResult(items=items, reference_urls=[])
