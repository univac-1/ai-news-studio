import ssl

import certifi
import httpx

from ..core.config import settings
from ..schemas.news import NewsItem

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.load_verify_locations(certifi.where())


async def fetch_news() -> list[NewsItem]:
    async with httpx.AsyncClient(timeout=30.0, verify=_ssl_ctx) as client:
        response = await client.get(settings.NEWS_DATA_URL)
        response.raise_for_status()
        raw_data: list[dict] = response.json()

    items: list[NewsItem] = []
    for raw in raw_data:
        try:
            items.append(NewsItem(**raw))
        except Exception:
            pass
    return items
