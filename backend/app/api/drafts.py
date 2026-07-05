from fastapi import APIRouter, Depends, HTTPException

from ..core.security import verify_credentials
from ..schemas.draft import VideoPlanDraft
from ..services.draft_store import get_latest_draft
from ..services.weekly_draft import NoPriorityNewsError, generate_new_weekly_draft

router = APIRouter()


@router.post("/generate-weekly", response_model=VideoPlanDraft)
async def generate_weekly(_: str = Depends(verify_credentials)):
    try:
        return await generate_new_weekly_draft()
    except NoPriorityNewsError as exc:
        raise HTTPException(
            status_code=422,
            detail="優先度Aのニュースが見つかりません（使用済み除外後）",
        ) from exc


@router.get("/latest", response_model=VideoPlanDraft | None)
async def latest_draft(_: str = Depends(verify_credentials)):
    return get_latest_draft()
