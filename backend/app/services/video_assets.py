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
import time
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

from ..core.config import settings

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


def _cache_path(model: str, prompt: str, image_bytes: bytes) -> Path:
    digest = hashlib.sha256(
        model.encode("utf-8") + b"\n" + prompt.encode("utf-8") + b"\n" + image_bytes
    ).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.mp4"


def _generate_clip_sync(model: str, prompt: str, image_bytes: bytes) -> bytes | None:
    client = genai.Client(
        vertexai=True,
        project=settings.GEMINI_PROJECT,
        location=settings.VIDEO_GEN_LOCATION,
    )
    operation = client.models.generate_videos(
        model=model,
        prompt=prompt,
        image=types.Image(image_bytes=image_bytes, mime_type="image/png"),
        config=types.GenerateVideosConfig(
            aspect_ratio="16:9",
            duration_seconds=settings.VIDEO_GEN_DURATION_SECONDS,
            # 音声はナレーション・BGM側で付けるため生成しない(コストも下がる)
            generate_audio=False,
            resolution="1080p",
        ),
    )
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
    return videos[0].video.video_bytes


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
) -> dict[int, Path]:
    """セグメント番号→動くクリップ(mp4)のパスを返す。失敗したセグメントは含めない。"""
    if not settings.VIDEO_GEN_ENABLED or not settings.GEMINI_PROJECT:
        return {}

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_GENERATIONS)
    numbers = list(segment_images)
    results = await asyncio.gather(
        *(
            _fetch_clip(settings.VIDEO_GEN_MODEL, _MOTION_PROMPT, segment_images[n], semaphore)
            for n in numbers
        )
    )
    return {number: clip for number, clip in zip(numbers, results) if clip is not None}
