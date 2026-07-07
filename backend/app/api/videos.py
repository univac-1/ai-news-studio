import asyncio
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from googleapiclient.errors import HttpError

from ..core.config import settings
from ..core.security import verify_credentials
from ..schemas.video import VideoArtifact, VideoArtifactList, VideoGenerationResult
from ..services.draft_store import get_latest_draft
from ..services.prepare_video_draft import prepare_draft_for_video
from ..services.video_generator import (
    ThumbnailGenerationError,
    generate_video_from_draft,
    get_video_artifact,
    get_video_file,
    get_video_thumbnail,
    list_video_artifacts,
)
from ..services.weekly_draft import NoPriorityNewsError, generate_new_weekly_draft
from ..services.youtube_uploader import (
    YouTubeAlreadyPublishedError,
    YouTubeAlreadyUploadedError,
    YouTubeConfigError,
    YouTubeNotUploadedError,
    publish_video,
    upload_video,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _maybe_auto_upload_to_youtube(video: VideoArtifact) -> VideoArtifact:
    """Feature D: 週次完全自律運転。設定が有効な場合、生成直後の動画を自動で
    YouTubeへ限定公開アップロードする。アップロードが失敗しても動画生成自体は
    成功として扱い、レスポンスは失敗させない(ログに記録するのみ)。"""
    if not settings.YOUTUBE_UPLOAD_ENABLED:
        return video
    try:
        await asyncio.to_thread(upload_video, video.id)
    except Exception:
        logger.exception("YouTube自動アップロードに失敗しました: video_id=%s", video.id)
        return video
    refreshed = get_video_artifact(video.id)
    return refreshed if refreshed is not None else video


@router.post("/generate-from-latest", response_model=VideoArtifact)
async def generate_from_latest(_: str = Depends(verify_credentials)):
    draft = get_latest_draft()
    if draft is None:
        raise HTTPException(
            status_code=422,
            detail="最新ドラフトがありません。先に週次ドラフトを生成してください。",
        )
    try:
        draft = await prepare_draft_for_video(draft)
        video = await generate_video_from_draft(draft)
        return await _maybe_auto_upload_to_youtube(video)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"VOICEVOX Engine に接続できません: {exc}",
        ) from exc
    except ThumbnailGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"FFmpeg の実行に失敗しました: {exc}",
        ) from exc


@router.post("/generate-weekly-from-new-draft", response_model=VideoGenerationResult)
async def generate_weekly_from_new_draft(_: str = Depends(verify_credentials)):
    try:
        draft = await generate_new_weekly_draft()
    except NoPriorityNewsError as exc:
        raise HTTPException(
            status_code=422,
            detail="台本生成に失敗しました: 優先度Aのニュースが見つかりません（使用済み除外後）",
        ) from exc

    try:
        video_draft = await prepare_draft_for_video(draft)
        video = await generate_video_from_draft(video_draft)
        video = await _maybe_auto_upload_to_youtube(video)
        return VideoGenerationResult(draft=video_draft, video=video)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"動画生成に失敗しました: VOICEVOX Engine に接続できません: {exc}",
        ) from exc
    except ThumbnailGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"動画生成に失敗しました: FFmpeg の実行に失敗しました: {exc}",
        ) from exc


@router.get("", response_model=VideoArtifactList)
async def list_videos(_: str = Depends(verify_credentials)):
    return VideoArtifactList(items=list_video_artifacts())


@router.get("/{video_id}", response_model=VideoArtifact)
async def get_video(video_id: str, _: str = Depends(verify_credentials)):
    artifact = get_video_artifact(video_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="動画が見つかりません。")
    return artifact


@router.get("/{video_id}/download")
async def download_video(video_id: str, _: str = Depends(verify_credentials)):
    path = get_video_file(video_id)
    if path is None:
        raise HTTPException(status_code=404, detail="動画ファイルが見つかりません。")

    def _iter():
        try:
            with open(path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        except OSError as exc:
            raise RuntimeError(f"動画ファイルの読み込みに失敗しました: {exc}") from exc

    return StreamingResponse(
        _iter(),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="ai-news-studio-{video_id}.mp4"'},
    )


@router.get("/{video_id}/thumbnail")
async def get_thumbnail(video_id: str, _: str = Depends(verify_credentials)):
    path = get_video_thumbnail(video_id)
    if path is None:
        raise HTTPException(status_code=404, detail="サムネイルが見つかりません。")

    def _iter():
        try:
            with open(path, "rb") as f:
                while chunk := f.read(256 * 1024):
                    yield chunk
        except OSError as exc:
            raise RuntimeError(f"サムネイルの読み込みに失敗しました: {exc}") from exc

    return StreamingResponse(
        _iter(),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="ai-news-studio-{video_id}.png"'},
    )


@router.post("/{video_id}/upload-youtube")
async def upload_video_to_youtube(video_id: str, _: str = Depends(verify_credentials)):
    if get_video_artifact(video_id) is None:
        raise HTTPException(status_code=404, detail="動画が見つかりません。")
    try:
        return await asyncio.to_thread(upload_video, video_id)
    except YouTubeAlreadyUploadedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except YouTubeConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HttpError as exc:
        raise HTTPException(status_code=502, detail=f"YouTube APIエラー: {exc}") from exc


@router.post("/{video_id}/publish")
async def publish_video_to_youtube(video_id: str, _: str = Depends(verify_credentials)):
    if get_video_artifact(video_id) is None:
        raise HTTPException(status_code=404, detail="動画が見つかりません。")
    try:
        return await asyncio.to_thread(publish_video, video_id)
    except (YouTubeNotUploadedError, YouTubeAlreadyPublishedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except YouTubeConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except HttpError as exc:
        raise HTTPException(status_code=502, detail=f"YouTube APIエラー: {exc}") from exc
