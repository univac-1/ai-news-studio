from typing import Literal

from pydantic import BaseModel


class SegmentVisual(BaseModel):
    # flow: 攻撃フロー等の3ステップ図 / command: コマンド・利用イメージのコードブロック
    type: Literal["flow", "command"]
    items: list[str]


class VideoSegment(BaseModel):
    number: int
    headline: str
    summary: str
    impact: str
    action: str
    slide_title: str
    narration: str
    source: str = ""
    title_ja: str = ""
    category: str = ""
    visual: SegmentVisual | None = None


class VideoPlanDraft(BaseModel):
    title: str
    title_candidates: list[str] = []
    week_label: str
    thumbnail_text: str
    thumbnail_text_candidates: list[str] = []
    hook: str = ""
    intro: str
    segments: list[VideoSegment]
    outro: str
    slide_outline: list[str]
    narration_script: str
    description: str
    hashtags: list[str]
    reference_urls: list[str]
    total_items: int
    generated_at: str
