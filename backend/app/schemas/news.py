from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    id: str
    title: str
    url: str = ""
    source: str = ""
    published_at: str = ""
    collected_at: str = ""
    image_url: str | None = None
    is_model_related: bool = False
    model_relevance_reason: str = ""
    model_name: str = "Unknown"
    provider: str = "Unknown"
    model_type: str = ""
    summary: str = ""
    impact: str = ""
    action: str = ""
    importance: str = "B"
    tags: list[str] = Field(default_factory=list)
    language: str = "ja"
    image_generated_url: str | None = None
    image_generated_alt: str | None = None
    image_generated_prompt: str | None = None

    model_config = {"extra": "ignore"}
