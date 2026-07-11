import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import drafts, health, news, used_news, videos
from .core.config import settings
from .core.security import verify_credentials
from .services.weekly_video_scheduler import weekly_video_scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop_event = asyncio.Event()
    scheduler_task: asyncio.Task[None] | None = None
    if settings.WEEKLY_VIDEO_SCHEDULE_ENABLED:
        scheduler_task = asyncio.create_task(weekly_video_scheduler_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="AI News Studio API", version="0.1.0", lifespan=lifespan)

if settings.APP_ENV == "development":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health.router, prefix="/api")
app.include_router(news.router, prefix="/api/news")
app.include_router(drafts.router, prefix="/api/drafts")
app.include_router(used_news.router, prefix="/api/used-news")
app.include_router(videos.router, prefix="/api/videos")

_dist = Path(settings.STATIC_FILES_DIR) if settings.STATIC_FILES_DIR else None

if _dist and _dist.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str, _: str = Depends(verify_credentials)):
        f = _dist / full_path
        return FileResponse(str(f) if f.is_file() else str(_dist / "index.html"))
