import asyncio
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

from ..core.config import settings
from ..schemas.draft import VideoPlanDraft, VideoSegment

BASE_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "images"
THUMBNAIL_IMAGE_MODEL = "gemini-3-pro-image"

# 旧スライド背景プロンプト。スライドはローカル描画のダーク固定デザインに統一したため
# 現在は未使用(ThemeImages.slide_bg は互換のため残し、常にNone)。
_SLIDE_BG_PROMPT = (
    "Minimal abstract background for a professional news presentation slide. "
    "Very light off-white to pale blue-gray vertical gradient, with subtle thin "
    "geometric tech lines and a faint circuit pattern only in the bottom right corner. "
    "Clean, elegant, broadcast news style, mostly empty space so dark text stays readable. "
    "Flat 2D design. No text, no letters, no logos, no watermark, no people."
)

# サムネイルは「背景のみ」を生成し、日本語テキストとキャラクターはローカルで合成する。
# モデルに日本語を描かせると誤字・配置崩れが運任せになるため、文字は一切描かせない。
# ずんだもんを右下に、見出しを左に載せる前提でレイアウトを指示している。
_THUMBNAIL_BG_PROMPT = (
    "Dramatic cinematic key visual for a Japanese tech-news YouTube thumbnail.\n\n"
    "This week's biggest AI news:\n{topic}\n\n"
    "Depict exactly ONE concrete, instantly recognizable hero subject that symbolizes "
    "this news — for example a glowing AI processor chip, a smartphone projecting a "
    "hologram, a robot hand, a server room, a soaring holographic chart. Choose the "
    "single object that best matches the news above. No collage, no split screen.\n\n"
    "Composition:\n"
    "Hero subject large in the upper-right two-thirds of the frame, slightly angled, "
    "dynamic diagonal energy. The left 40% of the frame and the bottom-right corner "
    "must fade smoothly and gradually into darkness (a soft, continuous dark gradient "
    "with no visible edge) because a bold headline and a mascot character will be "
    "overlaid there later. The darkening must blend seamlessly into the surrounding "
    "scene — never a distinct shape.\n\n"
    "Style and lighting:\n"
    "Photoreal 3D render, breaking-news urgency, strong rim light, vivid electric blue, "
    "cyan and orange accent lighting on a very dark navy background, high contrast, "
    "sharp focus on the subject, subtle glow particles and light streaks, premium and "
    "modern, readable at smartphone size.\n\n"
    "Strictly forbidden: any text, letters, numbers, typography, captions, subtitles, "
    "logos, watermarks, user interface, screenshots, people, faces, cartoon characters, "
    "solid black rectangles, hard-edged boxes or panels, borders, frames, letterboxing."
)


# 「infographic」「explaining」等の語はモデルにラベル文字の描画を誘発するため使わない。
# 見出しは「テーマのインスピレーション」として渡し、文字なしの象徴的イラストに徹させる。
_SEGMENT_ILLUSTRATION_PROMPT = (
    "A completely wordless, text-free symbolic illustration inspired by this theme: {topic}. "
    "Flat vector editorial illustration, clean modern style, flat 2D or subtle isometric, "
    "dark navy background with blue, cyan and white accents, one clear central visual metaphor "
    "built only from objects, shapes and scenery, generous empty space in the bottom third. "
    "Strictly no text of any kind: no words, no letters, no numbers, no typography, no captions, "
    "no labels, no logos, no watermarks, no user interface, no screenshots, no signage. "
    "Screens or displays, if any, must show only abstract glowing shapes. No real human faces."
)


# ずんだもんニュースソング(歌コーナー)スライドの背景。歌詞は下半分中心の帯に
# 描画されるため、中央帯〜下部は暗めでうるさくない構図を求めている。
_SONG_BG_PROMPT = (
    "A vibrant, completely wordless music-video key visual for a Japanese tech-news "
    "YouTube segment's concert-stage opening theme song.\n\n"
    "This week's AI news theme for subtle inspiration:\n{topic}\n\n"
    "Depict an energetic pop-concert / stage-live atmosphere: a dark navy base, sweeping "
    "neon spotlights, glowing music notes drifting in the air, laser beams, light-particle "
    "confetti, and futuristic tech accents (glowing circuit trails, holographic panels) "
    "that hint at the AI news theme above without depicting any literal news scene.\n\n"
    "Composition: keep the vertical center band and the lower third of the frame relatively "
    "dark, empty and uncluttered (soft dark gradient, no bright hotspots or busy detail) "
    "because large lyric text will be overlaid there later. Concentrate the brightest "
    "lights and detail toward the top and side edges of the frame. 16:9 aspect ratio.\n\n"
    "Style and lighting: photoreal/3D-render concert lighting, vivid electric blue, magenta, "
    "cyan and pink neon accents on a very dark navy background, high contrast, dynamic "
    "energy, premium and modern.\n\n"
    "Strictly forbidden: any text, letters, numbers, typography, captions, subtitles, logos, "
    "watermarks, user interface, screenshots, people, faces, cartoon characters, mascots."
)


@dataclass
class ThemeImages:
    thumbnail: Image.Image | None = None
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


def _thumbnail_topic(draft: VideoPlanDraft) -> str:
    # 被写体を1つに絞らせるため、最重要ニュース(#1)だけを渡す。
    # 複数見出しを混ぜると寄せ集めのぼやけた画になりやすい。
    for seg in draft.segments:
        headline = seg.headline.strip()
        if headline:
            topic = headline[:160]
            summary = seg.summary.strip()
            if summary:
                topic += ". " + summary[:160]
            return topic
    return "Important weekly artificial intelligence news."


async def generate_theme_images(draft: VideoPlanDraft) -> ThemeImages:
    """サムネイル用の背景画像(文字なし)を生成する。

    GEMINI_PROJECT未設定・IMAGE_GEN_ENABLED=False・生成失敗時はNoneを返し、
    呼び出し側でエラーにするかフォールバックするかを判断する。
    見出しテキストとずんだもんは video_generator 側でローカル合成するため、
    ここでは文字・キャラクターを含まない背景のみを生成する。
    スライド背景はローカル描画のダーク固定デザインに統一したため生成しない
    (slide_bgは常にNone)。
    """
    if not settings.IMAGE_GEN_ENABLED or not settings.GEMINI_PROJECT:
        return ThemeImages()

    thumbnail_bg = await _fetch_image(
        THUMBNAIL_IMAGE_MODEL,
        _THUMBNAIL_BG_PROMPT.format(topic=_thumbnail_topic(draft)),
    )
    return ThemeImages(thumbnail_bg=thumbnail_bg)


async def generate_song_background(draft: VideoPlanDraft) -> Image.Image | None:
    """ずんだもんニュースソング(歌コーナー)スライド用のミュージックビデオ風背景を生成する。

    GEMINI_PROJECT未設定・IMAGE_GEN_ENABLED=False・生成失敗時はNoneを返し、
    呼び出し側はローカルのダーク固定背景にフォールバックする。
    """
    if not settings.IMAGE_GEN_ENABLED or not settings.GEMINI_PROJECT:
        return None

    return await _fetch_image(
        settings.IMAGE_GEN_SLIDE_MODEL,
        _SONG_BG_PROMPT.format(topic=_thumbnail_topic(draft)),
    )


async def generate_segment_images(segments: list[VideoSegment]) -> dict[int, Image.Image]:
    """ニュースごとの解説イラストを生成する。失敗したセグメントは辞書に含めない。"""
    if not settings.IMAGE_GEN_ENABLED or not settings.GEMINI_PROJECT:
        return {}

    images: dict[int, Image.Image] = {}
    for segment in segments:
        topic = f"{segment.headline}. {segment.summary}"[:300]
        image = await _fetch_image(
            settings.IMAGE_GEN_SLIDE_MODEL, _SEGMENT_ILLUSTRATION_PROMPT.format(topic=topic)
        )
        if image is not None:
            images[segment.number] = image
    return images
