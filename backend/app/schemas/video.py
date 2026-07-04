from pydantic import BaseModel


class VideoArtifact(BaseModel):
    id: str
    title: str
    created_at: str
    draft_generated_at: str
    total_items: int
    duration_seconds: float
    video_path: str
    subtitles_path: str
    slide_count: int
    thumbnail_path: str = ""
    chapters: str = ""
    youtube_description: str = ""
    title_candidates: list[str] = []
    thumbnail_text_candidates: list[str] = []


class VideoArtifactList(BaseModel):
    items: list[VideoArtifact]
