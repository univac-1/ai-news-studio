from datetime import datetime, timedelta, timezone

from ..schemas.news import NewsItem
from .fetch_news import fetch_news
from .used_news_store import get_used_ids


def _parse_dt(date_str: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _sort_key(item: NewsItem) -> tuple[int, float]:
    importance_order = 0 if item.importance == "A" else 1
    dt = _parse_dt(item.published_at)
    ts = -dt.timestamp() if dt else 0.0
    return (importance_order, ts)


async def get_weekly_news() -> list[NewsItem]:
    all_news = await fetch_news()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    weekly = [
        item
        for item in all_news
        if (dt := _parse_dt(item.published_at)) is not None and dt >= cutoff
    ]
    weekly.sort(key=_sort_key)
    return weekly


async def get_priority_a_news(exclude_used: bool = True) -> list[NewsItem]:
    weekly = await get_weekly_news()
    priority_a = [item for item in weekly if item.importance == "A"]
    if exclude_used:
        used = get_used_ids()
        priority_a = [item for item in priority_a if item.id not in used]
    return priority_a
