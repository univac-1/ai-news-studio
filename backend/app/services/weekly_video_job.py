import asyncio
import logging

from ..core.config import settings
from ..schemas.video import VideoGenerationResult
from .email_notifier import EmailNotificationConfigError, send_email_notification
from .prepare_video_draft import prepare_draft_for_video
from .video_generator import generate_video_from_draft, get_video_artifact
from .weekly_draft import generate_new_weekly_draft
from .youtube_uploader import upload_video

logger = logging.getLogger(__name__)


async def maybe_auto_upload_to_youtube(video_id: str) -> None:
    if not settings.YOUTUBE_UPLOAD_ENABLED:
        return
    await asyncio.to_thread(upload_video, video_id)


async def generate_weekly_video_from_new_draft() -> VideoGenerationResult:
    draft = await generate_new_weekly_draft()
    video_draft = await prepare_draft_for_video(draft)
    video = await generate_video_from_draft(video_draft)
    try:
        await maybe_auto_upload_to_youtube(video.id)
    except Exception:
        logger.exception("YouTube auto upload failed: video_id=%s", video.id)
    refreshed = get_video_artifact(video.id)
    return VideoGenerationResult(draft=video_draft, video=refreshed or video)


async def notify_weekly_video_success(result: VideoGenerationResult) -> None:
    video = result.video
    lines = [
        "AI News Studio weekly video generation completed.",
        "",
        f"Title: {video.title}",
        f"Video ID: {video.id}",
        f"Created at: {video.created_at}",
        f"Duration: {video.duration_seconds} seconds",
    ]
    if video.youtube_url:
        lines.append(f"YouTube: {video.youtube_url}")
    try:
        await asyncio.to_thread(
            send_email_notification,
            f"[AI News Studio] Weekly video generated: {video.title}",
            "\n".join(lines),
        )
    except EmailNotificationConfigError:
        logger.warning("Weekly video notification is not configured; skipping success email")
    except Exception:
        logger.exception("Weekly video success notification failed")


async def notify_weekly_video_failure(exc: BaseException) -> None:
    try:
        await asyncio.to_thread(
            send_email_notification,
            "[AI News Studio] Weekly video generation failed",
            f"AI News Studio weekly video generation failed.\n\n{type(exc).__name__}: {exc}",
        )
    except EmailNotificationConfigError:
        logger.warning("Weekly video notification is not configured; skipping failure email")
    except Exception:
        logger.exception("Weekly video failure notification failed")
