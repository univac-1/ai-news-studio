from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import drafts, health, news, used_news
from .core.config import settings

app = FastAPI(title="AI News Studio API", version="0.1.0")

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
