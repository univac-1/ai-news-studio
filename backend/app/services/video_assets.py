"""セグメント導入イラストをVeo(Vertex AI)のimage-to-videoで動かすサービス。

- 生成済みのセグメントイラスト(image_assets)を先頭フレームとしてVeoに渡し、
  構図・絵柄を保ったまま緩やかに動くクリップを生成する
- 生成は長時間オペレーション。全セグメントを並行で投げ、ポーリングで完了を待つ
- モデル・プロンプト・入力画像のハッシュでmp4をキャッシュし、再生成コストを抑える
- VIDEO_GEN_ENABLED=False・GEMINI_PROJECT未設定・生成失敗時は該当セグメントを
  辞書に含めず、呼び出し側(video_generator)が静止画スライドへフォールバックする
"""

import asyncio
import hashlib
import io
import logging
import math
import time
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

from ..core.config import settings
from ..schemas.draft import VideoSegment

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
CACHE_DIR = BASE_DIR / "data" / "cache" / "clips"

# Veoの生成は通常1〜3分で完了する。異常時に生成パイプライン全体を
# 塞がないよう、ポーリングには上限を設ける
_POLL_INTERVAL_SECONDS = 10.0
_POLL_TIMEOUT_SECONDS = 600.0
# Veoの同時実行クォータを食い潰さないための並行数上限
_MAX_CONCURRENT_GENERATIONS = 4

# 静止イラストの構図・配色を保ったまま「動きだけ」を加えさせる。
# 新しいオブジェクトや文字が出るとスライドの情報設計が崩れるため強く禁止する。
_MOTION_PROMPT = (
    "Animate this illustration with subtle, smooth, seamless motion: a very slow "
    "camera push-in, gently floating particles, soft pulsing glows, drifting light "
    "streaks. Keep the composition, colors, style and every existing object exactly "
    "as in the original image. Calm, premium, loopable motion. "
    "Strictly no text, no letters, no logos, no new objects, no people, no faces, "
    "no scene change, no camera cuts."
)

# 歌コーナーのMV背景用。歌に合わせたリズミカルな光の演出を加えるが、
# 上に歌詞テキストを重ねるため構図の激変・文字の出現は禁止する。
_SONG_MOTION_PROMPT = (
    "Animate this illustration as a lively music video background: rhythmic pulsing "
    "stage lights, floating glowing particles, gentle sweeping light beams, a slow "
    "camera drift. Keep the composition, colors, style and every existing object from "
    "the original image. Upbeat but smooth, seamless, loopable motion. "
    "Strictly no text, no letters, no logos, no new objects, no people, no faces, "
    "no scene change, no camera cuts."
)

_OPENING_BASE_PROMPT = (
    "Create a premium cinematic 16:9 opening sequence for a weekly Japanese AI news "
    "show, designed as a continuous broadcast intro background. Start with a dark, "
    "high-tech virtual newsroom seen from a dynamic low angle, then move forward "
    "through layered glass panels, holographic data ribbons, luminous category-color "
    "light trails, floating abstract UI panels, and fast but elegant parallax motion. "
    "Make it exciting and polished, like a major tech conference keynote opener: "
    "strong depth, sweeping camera motion, crisp reflections, warm key lights, cool "
    "monitor glow, energetic particles, and a clear sense of anticipation. Keep the "
    "left-center area relatively clean for overlaid lineup text, and keep the lower "
    "right readable for a presenter character overlay. No readable text, no letters, "
    "no numbers, no logos, no subtitles, no watermarks, no people, no faces."
)

_OPENING_EXTENSION_PROMPTS = [
    (
        "Continue the same opening sequence with rising energy. The camera accelerates "
        "smoothly through luminous data corridors and layered glass panels; category "
        "color streaks orbit around the scene, abstract AI-network nodes connect, and "
        "the lighting builds toward a reveal. Add a satisfying broadcast-intro climax "
        "without hard cuts. Keep the left-center area clean for overlaid lineup text "
        "and avoid busy motion behind it. No readable text, no letters, no numbers, "
        "no logos, no subtitles, no watermarks, no people, no faces."
    ),
    (
        "Continue from the previous shot and resolve the motion into a confident "
        "lineup-ready composition. The camera slows, the data ribbons settle into "
        "elegant arcs, the virtual newsroom gains depth and sparkle, and the final "
        "seconds feel like a clean title-card hold with subtle motion still alive. "
        "Leave the left-center area calm and readable for overlaid text, with visual "
        "interest concentrated around the edges and background depth. No readable "
        "text, no letters, no numbers, no logos, no subtitles, no watermarks, no "
        "people, no faces."
    ),
]


def _cache_path(model: str, prompt: str, image_bytes: bytes) -> Path:
    digest = hashlib.sha256(
        model.encode("utf-8") + b"\n" + prompt.encode("utf-8") + b"\n" + image_bytes
    ).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.mp4"


def _text_cache_path(model: str, prompt: str, target_seconds: int, resolution: str) -> Path:
    digest = hashlib.sha256(
        model.encode("utf-8")
        + b"\n"
        + resolution.encode("utf-8")
        + b"\n"
        + str(target_seconds).encode("ascii")
        + b"\n"
        + prompt.encode("utf-8")
    ).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.mp4"


def _brief_text(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def _segment_context(segment: VideoSegment) -> str:
    parts = [
        f"#{segment.number}",
        f"category={segment.category}" if segment.category else "",
        segment.title_ja or segment.headline,
        segment.summary,
        f"impact: {segment.impact}" if segment.impact else "",
        f"why it matters: {segment.rank_reason}" if segment.rank_reason else "",
    ]
    return _brief_text(". ".join(part for part in parts if part), 360)


def _news_contexts(segments: list[VideoSegment]) -> list[str]:
    return [_segment_context(segment) for segment in segments if segment.headline or segment.summary]


def _segment_motion_prompt(segment: VideoSegment | None) -> str:
    if segment is None:
        return _MOTION_PROMPT
    return (
        _MOTION_PROMPT
        + " News context for the motion style: "
        + _segment_context(segment)
        + " Let the movement subtly echo this news theme through abstract visual "
        "metaphors only, such as scanning light, network pulses, chip-like glows, "
        "cloud-depth parallax, media waves, or security sweeps when relevant. Do not "
        "add literal screenshots, labels, readable interfaces, extra objects that "
        "change the slide meaning, or any text."
    )


def _song_motion_prompt(
    lyrics: list[str] | None = None,
    news_contexts: list[str] | None = None,
) -> str:
    prompt = _SONG_MOTION_PROMPT
    lyric_hint = " / ".join(_brief_text(line, 80) for line in (lyrics or []) if line)
    news_hint = " / ".join(_brief_text(item, 120) for item in (news_contexts or [])[:5] if item)
    if lyric_hint:
        prompt += (
            " Lyric mood and rhythm inspiration: "
            + lyric_hint
            + ". Time the pulsing lights and camera drift to feel playful, catchy, and "
            "in sync with this song's phrases, without rendering the lyrics as text."
        )
    if news_hint:
        prompt += (
            " AI news context for abstract stage motifs: "
            + news_hint
            + ". Reflect these topics through color, rhythm, holographic shapes, and "
            "symbolic tech motion only."
        )
    return prompt


def _opening_context_suffix(
    week_label: str,
    lineup_labels: list[str],
    news_contexts: list[str] | None = None,
) -> str:
    lineup_hint = " / ".join(label for label in lineup_labels[:7] if label)
    detail_hint = " / ".join(_brief_text(item, 160) for item in (news_contexts or [])[:7] if item)
    if not week_label and not lineup_hint and not detail_hint:
        return ""
    return (
        f" Episode context: {week_label or 'this week'}."
        f" News topics include: {lineup_hint}."
        f" Detailed story cues: {detail_hint}."
        " Use those topics only as abstract inspiration for color, rhythm, and mood; "
        "do not render topic names, readable UI labels, or any text."
    )


def _poll_video_operation(client: genai.Client, operation: types.GenerateVideosOperation) -> types.Video | None:
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while not operation.done:
        if time.monotonic() > deadline:
            raise TimeoutError("Veoの動画生成オペレーションがタイムアウトしました")
        time.sleep(_POLL_INTERVAL_SECONDS)
        operation = client.operations.get(operation)
    if operation.error:
        raise RuntimeError(f"Veoの動画生成に失敗しました: {operation.error}")
    videos = operation.response.generated_videos if operation.response else None
    if not videos or videos[0].video is None:
        return None
    video = videos[0].video
    if not video.video_bytes:
        client.files.download(file=video)
    return video


def _generate_video_sync(
    model: str,
    prompt: str,
    *,
    image_bytes: bytes | None = None,
    video: types.Video | None = None,
    resolution: str = "1080p",
    duration_seconds: int | None = None,
    generate_audio: bool = False,
) -> types.Video | None:
    client = genai.Client(
        vertexai=True,
        project=settings.GEMINI_PROJECT,
        location=settings.VIDEO_GEN_LOCATION,
    )
    config_kwargs = {
        "number_of_videos": 1,
        "aspect_ratio": "16:9",
        "generate_audio": generate_audio,
        "resolution": resolution,
    }
    if duration_seconds is not None:
        config_kwargs["duration_seconds"] = duration_seconds
    image = (
        types.Image(image_bytes=image_bytes, mime_type="image/png")
        if image_bytes is not None
        else None
    )
    operation = client.models.generate_videos(
        model=model,
        prompt=prompt,
        image=image,
        video=video,
        config=types.GenerateVideosConfig(**config_kwargs),
    )
    return _poll_video_operation(client, operation)


def _generate_clip_sync(model: str, prompt: str, image_bytes: bytes) -> bytes | None:
    video = _generate_video_sync(
        model,
        prompt,
        image_bytes=image_bytes,
        resolution="1080p",
        duration_seconds=settings.VIDEO_GEN_DURATION_SECONDS,
        # 音声はナレーション・BGM側で付けるため生成しない(コストも下がる)
        generate_audio=False,
    )
    if video is None:
        return None
    return video.video_bytes


def _generate_extended_opening_sync(
    model: str, prompts: list[str], target_seconds: int
) -> bytes | None:
    if "lite" in model.lower():
        logger.warning("Veo video extension is not available for Lite models: %s", model)
        return None
    if not prompts:
        return None

    base_seconds = max(settings.VIDEO_GEN_DURATION_SECONDS, 1)
    extension_count = max(0, math.ceil((target_seconds - base_seconds) / 7))
    extension_count = min(extension_count, 20)

    video = _generate_video_sync(
        model,
        prompts[0],
        resolution="720p",
        duration_seconds=base_seconds,
        generate_audio=False,
    )
    for index in range(extension_count):
        if video is None:
            return None
        prompt = prompts[min(index + 1, len(prompts) - 1)]
        video = _generate_video_sync(
            model,
            prompt,
            video=video,
            resolution="720p",
            generate_audio=False,
        )
    return video.video_bytes if video is not None else None


async def _fetch_clip(
    model: str, prompt: str, image: Image.Image, semaphore: asyncio.Semaphore
) -> Path | None:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    cache_path = _cache_path(model, prompt, image_bytes)
    if cache_path.exists():
        return cache_path

    try:
        async with semaphore:
            clip_bytes = await asyncio.to_thread(
                _generate_clip_sync, model, prompt, image_bytes
            )
    except Exception:
        logger.exception("segment clip generation failed")
        return None
    if not clip_bytes:
        return None

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(clip_bytes)
    return cache_path


async def generate_segment_clips(
    segment_images: dict[int, Image.Image],
    segments: list[VideoSegment] | None = None,
) -> dict[int, Path]:
    """セグメント番号→動くクリップ(mp4)のパスを返す。失敗したセグメントは含めない。"""
    if not settings.VIDEO_GEN_ENABLED or not settings.GEMINI_PROJECT:
        return {}

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_GENERATIONS)
    numbers = list(segment_images)
    segments_by_number = {segment.number: segment for segment in segments or []}
    results = await asyncio.gather(
        *(
            _fetch_clip(
                settings.VIDEO_GEN_MODEL,
                _segment_motion_prompt(segments_by_number.get(n)),
                segment_images[n],
                semaphore,
            )
            for n in numbers
        )
    )
    return {number: clip for number, clip in zip(numbers, results) if clip is not None}


async def generate_song_clip(
    song_bg: Image.Image,
    lyrics: list[str] | None = None,
    news_contexts: list[str] | None = None,
) -> Path | None:
    """歌コーナーのMV背景画像を動くクリップ(mp4)にする。失敗時はNone(静止画のまま)。"""
    if not settings.VIDEO_GEN_ENABLED or not settings.GEMINI_PROJECT:
        return None
    return await _fetch_clip(
        settings.VIDEO_GEN_MODEL,
        _song_motion_prompt(lyrics, news_contexts),
        song_bg,
        asyncio.Semaphore(1),
    )


async def generate_opening_clip(
    week_label: str,
    lineup_labels: list[str],
    news_contexts: list[str] | None = None,
) -> Path | None:
    """オープニング用の非ループ長尺背景クリップをVeo拡張で生成する。

    文字・キャラクターはvideo_generator側で静的オーバーレイするため、
    Veoには文字なし背景だけを作らせる。
    """
    if (
        not settings.VIDEO_GEN_ENABLED
        or not settings.VIDEO_GEN_OPENING_ENABLED
        or not settings.GEMINI_PROJECT
    ):
        return None

    target_seconds = max(8, min(settings.VIDEO_GEN_OPENING_TARGET_SECONDS, 148))
    context_suffix = _opening_context_suffix(week_label, lineup_labels, news_contexts)
    prompts = [
        _OPENING_BASE_PROMPT + context_suffix,
        *(prompt + context_suffix for prompt in _OPENING_EXTENSION_PROMPTS),
    ]

    cache_key_prompt = "\n--- extension ---\n".join(prompts)
    cache_path = _text_cache_path(settings.VIDEO_GEN_MODEL, cache_key_prompt, target_seconds, "720p")
    if cache_path.exists():
        return cache_path

    try:
        clip_bytes = await asyncio.to_thread(
            _generate_extended_opening_sync,
            settings.VIDEO_GEN_MODEL,
            prompts,
            target_seconds,
        )
    except Exception:
        logger.exception("opening clip generation failed")
        return None
    if not clip_bytes:
        return None

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(clip_bytes)
    return cache_path
