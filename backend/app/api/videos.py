import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..core.security import verify_credentials
from ..schemas.video import VideoArtifact, VideoArtifactList
from ..services.draft_store import get_latest_draft
from ..services.video_generator import (
    generate_video_from_draft,
    get_video_artifact,
    get_video_file,
    list_video_artifacts,
)

router = APIRouter()


@router.post("/generate-from-latest", response_model=VideoArtifact)
async def generate_from_latest(_: str = Depends(verify_credentials)):
    draft = get_latest_draft()
    if draft is None:
        raise HTTPException(
            status_code=422,
            detail="最新ドラフトがありません。先に週次ドラフトを生成してください。",
        )
    try:
        return await generate_video_from_draft(draft)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"VOICEVOX Engine に接続できません: {exc}",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"FFmpeg の実行に失敗しました: {exc}",
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
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=f"ai-news-studio-{video_id}.mp4",
    )
