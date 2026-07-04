import asyncio
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

from ..core.config import settings
from ..schemas.draft import VideoPlanDraft

BASE_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "images"

# スライド背景はトピック非依存の固定プロンプトにして、キャッシュを恒久的に使い回す
_SLIDE_BG_PROMPT = (
    "Minimal abstract background for a professional news presentation slide. "
    "Very light off-white to pale blue-gray vertical gradient, with subtle thin "
    "geometric tech lines and a faint circuit pattern only in the bottom right corner. "
    "Clean, elegant, broadcast news style, mostly empty space so dark text stays readable. "
    "Flat 2D design. No text, no letters, no logos, no watermark, no people."
)

_THUMBNAIL_BG_PROMPT = (
    "Abstract futuristic technology background for an AI news YouTube thumbnail "
    "about this topic: {topic}. "
    "Dark navy to electric blue gradient, glowing neural network nodes and circuit "
    "patterns concentrated on the right side, left half darker and simpler for text overlay. "
    "High contrast, cinematic lighting, energetic. "
    "No text, no letters, no logos, no watermark, no people."
)


@dataclass
class ThemeImages:
    thumbnail_bg: Image.Image | None = None
    slide_bg: Image.Image | None = None


def _cache_path(model: str, prompt: str) -> Path:
    digest = hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.png"


def _generate_image_sync(model: str, prompt: str) -> Image.Image | None:
    client = genai.Client(
        vertexai=True,
        project=settings.GEMINI_PROJECT,
        location=settings.IMAGE_GEN_LOCATION,
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="16:9"),
        ),
    )
    for candidate in response.candidates or []:
        if candidate.content is None:
            continue
        for part in candidate.content.parts or []:
            if part.inline_data and part.inline_data.data:
                return Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
    return None


async def _fetch_image(model: str, prompt: str) -> Image.Image | None:
    cache_path = _cache_path(model, prompt)
    if cache_path.exists():
        try:
            return Image.open(cache_path).convert("RGB")
        except Exception:
            pass
    try:
        image = await asyncio.to_thread(_generate_image_sync, model, prompt)
    except Exception:
        return None
    if image is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(cache_path)
    return image


async def generate_theme_images(draft: VideoPlanDraft) -> ThemeImages:
    """サムネイル用・スライド共通の背景画像を生成する。

    GEMINI_PROJECT未設定・IMAGE_GEN_ENABLED=False・生成失敗時はNoneを返し、
    呼び出し側は従来のグラデーション描画にフォールバックする。
    """
    if not settings.IMAGE_GEN_ENABLED or not settings.GEMINI_PROJECT:
        return ThemeImages()

    topic = draft.segments[0].headline if draft.segments else "artificial intelligence"
    thumbnail_bg = await _fetch_image(
        settings.IMAGE_GEN_THUMBNAIL_MODEL, _THUMBNAIL_BG_PROMPT.format(topic=topic)
    )
    slide_bg = await _fetch_image(settings.IMAGE_GEN_SLIDE_MODEL, _SLIDE_BG_PROMPT)
    return ThemeImages(thumbnail_bg=thumbnail_bg, slide_bg=slide_bg)
