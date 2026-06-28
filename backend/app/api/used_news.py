from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.security import verify_credentials
from ..services.used_news_store import get_used_news, mark_as_used

router = APIRouter()


class MarkUsedRequest(BaseModel):
    news_id: str


@router.get("")
async def list_used_news(_: str = Depends(verify_credentials)):
    return get_used_news()


@router.post("")
async def mark_news_used(req: MarkUsedRequest, _: str = Depends(verify_credentials)):
    mark_as_used(req.news_id)
    return {"success": True, "news_id": req.news_id}
