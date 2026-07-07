from pydantic import BaseModel

from .draft import VideoPlanDraft


class ReviewFinding(BaseModel):
    part: int
    code: str
    description: str
    fixable: bool


class ReviewReport(BaseModel):
    status: str = ""  # "passed" | "passed_with_warnings" | "skipped"
    rounds: int = 0
    findings: list[ReviewFinding] = []


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
    review_status: str = ""
    review_findings: list[ReviewFinding] = []
    hashtags: list[str] = []
    youtube_video_id: str = ""
    youtube_privacy: str = ""
    youtube_url: str = ""


class VideoArtifactList(BaseModel):
    items: list[VideoArtifact]


class VideoGenerationResult(BaseModel):
    draft: VideoPlanDraft
    video: VideoArtifact
