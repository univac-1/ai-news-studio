from fastapi import APIRouter, Depends

from ..core.security import verify_credentials

router = APIRouter()


@router.get("/health")
async def health_check(_: str = Depends(verify_credentials)):
    return {"status": "ok", "service": "ai-news-studio-backend"}
