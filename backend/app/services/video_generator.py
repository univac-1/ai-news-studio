import json
import math
import subprocess
import textwrap
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from ..core.config import settings
from ..schemas.draft import VideoPlanDraft
from ..schemas.video import VideoArtifact

BASE_DIR = Path(__file__).parent.parent.parent
GENERATED_DIR = BASE_DIR / "data" / "generated"
WIDTH = 1920
HEIGHT = 1080


@dataclass
class SlideSpec:
    kind: str
    title: str
    body: str
    narration: str


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/meiryob.ttc" if bold else "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothB.ttc" if bold else "C:/Windows/Fonts/YuGothR.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _wrap_by_pixels(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines() or [""]:
        if not raw_line:
            lines.append("")
            continue
        line = ""
        for char in raw_line:
            trial = f"{line}{char}"
            bbox = font.getbbox(trial)
            if bbox[2] - bbox[0] <= max_width or not line:
                line = trial
            else:
                lines.append(line)
                line = char
        if line:
            lines.append(line)
    return lines


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    line_gap: int,
    max_lines: int | None = None,
) -> int:
    x, y = xy
    lines = _wrap_by_pixels(text, font, max_width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = f"{lines[-1].rstrip()}..."
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = font.getbbox(line or " ")
        y += bbox[3] - bbox[1] + line_gap
    return y


def _render_slide(spec: SlideSpec, index: int, total: int, path: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#f8fafc")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(68, bold=True)
    body_font = _load_font(40)
    meta_font = _load_font(28)
    badge_font = _load_font(30, bold=True)

    draw.rectangle((0, 0, WIDTH, 120), fill="#111827")
    draw.text((80, 40), "AI News Studio", font=badge_font, fill="#ffffff")
    draw.text((WIDTH - 260, 44), f"{index}/{total}", font=meta_font, fill="#d1d5db")

    accent = "#2563eb" if spec.kind in {"cover", "intro", "outro"} else "#f59e0b"
    draw.rectangle((80, 180, 96, 880), fill=accent)
    _draw_wrapped(draw, (140, 180), spec.title, title_font, "#111827", 1580, 18, max_lines=4)
    _draw_wrapped(draw, (140, 520), spec.body, body_font, "#374151", 1580, 18, max_lines=8)

    draw.rectangle((80, 925, WIDTH - 80, 928), fill="#e5e7eb")
    draw.text((80, 955), "Generated from weekly AI news draft", font=meta_font, fill="#6b7280")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


async def _synthesize_voice(text: str, path: Path) -> None:
    async with httpx.AsyncClient(base_url=settings.VOICEVOX_BASE_URL, timeout=120.0) as client:
        query_res = await client.post(
            "/audio_query",
            params={"text": text, "speaker": settings.VOICEVOX_SPEAKER_ID},
        )
        query_res.raise_for_status()
        audio_res = await client.post(
            "/synthesis",
            params={"speaker": settings.VOICEVOX_SPEAKER_ID},
            json=query_res.json(),
        )
        audio_res.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio_res.content)


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def _run_ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] or "ffmpeg failed")


def _format_srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours = millis // 3_600_000
    millis %= 3_600_000
    minutes = millis // 60_000
    millis %= 60_000
    secs = millis // 1000
    millis %= 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _write_srt(slides: list[SlideSpec], durations: list[float], path: Path) -> None:
    cursor = 0.0
    chunks: list[str] = []
    for i, (slide, duration) in enumerate(zip(slides, durations), 1):
        end = cursor + max(duration, 1.0)
        subtitle = "\n".join(textwrap.wrap(slide.narration.replace("\n", " "), width=42))
        chunks.append(
            f"{i}\n{_format_srt_time(cursor)} --> {_format_srt_time(end)}\n{subtitle}\n"
        )
        cursor = end
    path.write_text("\n".join(chunks), encoding="utf-8")


def _build_slides(draft: VideoPlanDraft) -> list[SlideSpec]:
    slides = [
        SlideSpec(
            kind="cover",
            title=draft.title,
            body=draft.thumbnail_text.replace("\n", " / "),
            narration=draft.title,
        ),
        SlideSpec(kind="intro", title="今週のハイライト", body=draft.intro, narration=draft.intro),
    ]
    for segment in draft.segments:
        body = f"{segment.summary}\n\nImpact: {segment.impact}\nAction: {segment.action}"
        slides.append(
            SlideSpec(
                kind="segment",
                title=f"#{segment.number} {segment.headline}",
                body=body,
                narration=segment.narration,
            )
        )
    slides.append(SlideSpec(kind="outro", title="まとめ", body=draft.outro, narration=draft.outro))
    return slides


async def generate_video_from_draft(draft: VideoPlanDraft) -> VideoArtifact:
    video_id = _now_id()
    work_dir = GENERATED_DIR / video_id
    slides_dir = work_dir / "slides"
    audio_dir = work_dir / "audio"
    parts_dir = work_dir / "parts"
    work_dir.mkdir(parents=True, exist_ok=False)
    parts_dir.mkdir(parents=True, exist_ok=True)

    slides = _build_slides(draft)
    durations: list[float] = []
    for index, slide in enumerate(slides, 1):
        slide_path = slides_dir / f"slide_{index:03}.png"
        audio_path = audio_dir / f"audio_{index:03}.wav"
        part_path = parts_dir / f"part_{index:03}.mp4"
        _render_slide(slide, index, len(slides), slide_path)
        await _synthesize_voice(slide.narration, audio_path)
        duration = max(_wav_duration(audio_path), 1.0)
        durations.append(duration)
        _run_ffmpeg(
            [
                "-loop",
                "1",
                "-framerate",
                "30",
                "-i",
                str(slide_path),
                "-i",
                str(audio_path),
                "-t",
                f"{math.ceil(duration * 1000) / 1000:.3f}",
                "-c:v",
                "libx264",
                "-tune",
                "stillimage",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-pix_fmt",
                "yuv420p",
                "-shortest",
                str(part_path),
            ]
        )

    concat_file = work_dir / "concat.txt"
    concat_file.write_text(
        "".join(f"file 'parts/part_{i:03}.mp4'\n" for i in range(1, len(slides) + 1)),
        encoding="utf-8",
    )
    video_path = work_dir / "video.mp4"
    _run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            "concat.txt",
            "-c",
            "copy",
            "video.mp4",
        ],
        cwd=work_dir,
    )

    subtitles_path = work_dir / "subtitles.srt"
    _write_srt(slides, durations, subtitles_path)

    artifact = VideoArtifact(
        id=video_id,
        title=draft.title,
        created_at=datetime.now(timezone.utc).isoformat(),
        draft_generated_at=draft.generated_at,
        total_items=draft.total_items,
        duration_seconds=round(sum(durations), 3),
        video_path=video_path.name,
        subtitles_path=subtitles_path.name,
        slide_count=len(slides),
    )
    (work_dir / "metadata.json").write_text(
        artifact.model_dump_json(indent=2), encoding="utf-8"
    )
    return artifact


def list_video_artifacts() -> list[VideoArtifact]:
    if not GENERATED_DIR.exists():
        return []
    artifacts: list[VideoArtifact] = []
    for metadata_path in GENERATED_DIR.glob("*/metadata.json"):
        try:
            artifacts.append(VideoArtifact(**json.loads(metadata_path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return sorted(artifacts, key=lambda item: item.created_at, reverse=True)


def get_video_artifact(video_id: str) -> VideoArtifact | None:
    metadata_path = GENERATED_DIR / video_id / "metadata.json"
    if not metadata_path.exists():
        return None
    return VideoArtifact(**json.loads(metadata_path.read_text(encoding="utf-8")))


def get_video_file(video_id: str) -> Path | None:
    path = GENERATED_DIR / video_id / "video.mp4"
    if not path.exists():
        return None
    return path
