from fastapi import APIRouter, Depends

from ..core.security import verify_credentials
from ..schemas.news import NewsItem
from ..services.filter_priority import get_priority_a_news, get_weekly_news

router = APIRouter()


@router.get("/weekly", response_model=list[NewsItem])
async def weekly_news(_: str = Depends(verify_credentials)):
    return await get_weekly_news()


@router.get("/priority-a", response_model=list[NewsItem])
async def priority_a_news(_: str = Depends(verify_credentials)):
    return await get_priority_a_news(exclude_used=True)
