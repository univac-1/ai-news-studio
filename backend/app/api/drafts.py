from fastapi import APIRouter, Depends, HTTPException

from ..core.security import verify_credentials
from ..schemas.draft import VideoPlanDraft
from ..services.draft_store import get_latest_draft, save_draft
from ..services.filter_priority import get_priority_a_news
from ..services.generate_weekly_video_plan import generate_weekly_video_plan
from ..services.select_news_for_video import select_news_for_video

router = APIRouter()


@router.post("/generate-weekly", response_model=VideoPlanDraft)
async def generate_weekly(_: str = Depends(verify_credentials)):
    items = await get_priority_a_news(exclude_used=True)
    if not items:
        raise HTTPException(
            status_code=422,
            detail="優先度Aのニュースが見つかりません（使用済み除外後）",
        )
    items = await select_news_for_video(items)
    draft = generate_weekly_video_plan(items)
    save_draft(draft)
    return draft


@router.get("/latest", response_model=VideoPlanDraft | None)
async def latest_draft(_: str = Depends(verify_credentials)):
    return get_latest_draft()
