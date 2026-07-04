import json
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
    source: str = ""
    impact: str = ""
    action: str = ""


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


def _subtitle_font() -> str:
    if Path("C:/Windows/Fonts/meiryo.ttc").exists():
        return "Meiryo"
    return "Noto Sans CJK JP"


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


def _split_voice_text(text: str, max_chars: int = 160) -> list[str]:
    normalized = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if not normalized:
        return [""]

    chunks: list[str] = []
    current = ""
    # 半角ピリオドは「Claude 3.5」等のバージョン表記を分断するため文境界に含めない
    break_chars = "。！？!?"
    for char in normalized:
        current += char
        if char in break_chars:
            if current.strip():
                chunks.append(current.strip())
            current = ""

    if current.strip():
        chunks.append(current.strip())

    split_chunks: list[str] = []
    for chunk in chunks:
        while len(chunk) > max_chars:
            split_chunks.append(chunk[:max_chars].strip())
            chunk = chunk[max_chars:].strip()
        if chunk:
            split_chunks.append(chunk)

    return split_chunks or [normalized]


def _concat_wavs(paths: list[Path], output_path: Path) -> None:
    audio_format = None
    frames: list[bytes] = []
    for path in paths:
        with wave.open(str(path), "rb") as wav:
            if audio_format is None:
                audio_format = (
                    wav.getnchannels(),
                    wav.getsampwidth(),
                    wav.getframerate(),
                    wav.getcomptype(),
                    wav.getcompname(),
                )
            elif (
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getframerate(),
                wav.getcomptype(),
                wav.getcompname(),
            ) != audio_format:
                raise RuntimeError("VOICEVOX returned inconsistent WAV parameters")
            frames.append(wav.readframes(wav.getnframes()))

    if audio_format is None:
        raise RuntimeError("VOICEVOX returned no audio")

    with wave.open(str(output_path), "wb") as output:
        channels, sample_width, frame_rate, compression_type, compression_name = audio_format
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(frame_rate)
        output.setcomptype(compression_type, compression_name)
        for frame in frames:
            output.writeframes(frame)


def _format_chapter_time(seconds: float) -> str:
    total_secs = int(seconds)
    h = total_secs // 3600
    m = (total_secs % 3600) // 60
    s = total_secs % 60
    if h > 0:
        return f"{h}:{m:02}:{s:02}"
    return f"{m}:{s:02}"


def _build_chapters(slides: list[SlideSpec], slide_offsets: list[float]) -> str:
    lines: list[str] = ["0:00 オープニング"]
    outro_line: str | None = None
    for slide, offset in zip(slides, slide_offsets):
        if slide.kind == "segment":
            lines.append(f"{_format_chapter_time(offset)} {slide.title}")
        elif slide.kind == "outro":
            outro_line = f"{_format_chapter_time(offset)} まとめ"
    if outro_line:
        lines.append(outro_line)
    return "\n".join(lines)


def _render_thumbnail(draft: VideoPlanDraft, path: Path) -> None:
    thumb_width, thumb_height = 1280, 720

    # Background gradient (#111827 → #1e3a8a), working in RGBA for compositing
    image = Image.new("RGBA", (thumb_width, thumb_height))
    draw = ImageDraw.Draw(image)
    start_color = (17, 24, 39)    # #111827
    end_color = (30, 58, 138)     # #1e3a8a
    for y in range(thumb_height):
        ratio = y / thumb_height
        r = int(start_color[0] + (end_color[0] - start_color[0]) * ratio)
        g = int(start_color[1] + (end_color[1] - start_color[1]) * ratio)
        b = int(start_color[2] + (end_color[2] - start_color[2]) * ratio)
        draw.line(((0, y), (thumb_width, y)), fill=(r, g, b, 255))

    # Semi-transparent accent shapes on separate overlay
    overlay = Image.new("RGBA", (thumb_width, thumb_height), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    # Large circle anchored to bottom-right corner
    cx, cy, cr = thumb_width - 80, thumb_height + 30, 360
    ov_draw.ellipse((cx - cr, cy - cr, cx + cr, cy + cr), fill=(59, 130, 246, 55))
    # Small diagonal triangle accent top-right
    ov_draw.polygon(
        [(thumb_width - 260, 0), (thumb_width, 0), (thumb_width, 220)],
        fill=(250, 204, 21, 40),
    )
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    # Red badge: "今週のAI速報"
    badge_font = _load_font(34, bold=True)
    badge_text = "今週のAI速報"
    bb = badge_font.getbbox(badge_text)
    btw, bth = bb[2] - bb[0], bb[3] - bb[1]
    bpad_x, bpad_y = 22, 10
    bx1, by1 = 56, 26
    bx2, by2 = bx1 + btw + bpad_x * 2, by1 + bth + bpad_y * 2
    draw.rounded_rectangle((bx1, by1, bx2, by2), radius=8, fill="#dc2626")
    draw.text((bx1 + bpad_x, by1 + bpad_y), badge_text, font=badge_font, fill="#ffffff")

    # Parse thumbnail_text: first line → main, rest → sub
    text_lines = draft.thumbnail_text.split("\n")
    main_line = text_lines[0] if text_lines else ""
    sub_lines = [ln for ln in text_lines[1:] if ln]

    # Auto-fit main font (start 200, min 90, step -10) to fit 1150 px wide
    main_size = 200
    while main_size > 90:
        mf = _load_font(main_size, bold=True)
        mb = mf.getbbox(main_line or " ")
        if (mb[2] - mb[0]) <= 1150:
            break
        main_size -= 10
    main_font = _load_font(main_size, bold=True)
    main_bbox = main_font.getbbox(main_line or " ")
    main_h = main_bbox[3] - main_bbox[1]

    # Auto-fit sub font (start at half of main, min 50, step -10)
    sub_size = max(main_size // 2, 50)
    if sub_lines:
        while sub_size > 40:
            sf_test = _load_font(sub_size, bold=True)
            max_sw = max(
                sf_test.getbbox(sl or " ")[2] - sf_test.getbbox(sl or " ")[0]
                for sl in sub_lines
            )
            if max_sw <= 1150:
                break
            sub_size -= 10
    sub_font = _load_font(sub_size, bold=True)

    # Measure sub-line heights
    line_gap = 20
    sub_heights: list[int] = []
    for sl in sub_lines:
        sb = sub_font.getbbox(sl or " ")
        sub_heights.append(sb[3] - sb[1])

    total_text_h = main_h + sum(h + line_gap for h in sub_heights)

    # Vertically center text block between badge bottom and footer area
    footer_top = thumb_height - 70
    content_top = by2 + 20
    center_y = (content_top + footer_top) // 2
    y_cur = center_y - total_text_h // 2

    # Draw main line (yellow, heavy stroke)
    main_w = main_bbox[2] - main_bbox[0]
    draw.text(
        ((thumb_width - main_w) // 2, y_cur),
        main_line,
        font=main_font,
        fill="#facc15",
        stroke_width=10,
        stroke_fill="#111827",
    )
    y_cur += main_h + line_gap

    # Draw sub lines (white, lighter stroke)
    for sl, sh in zip(sub_lines, sub_heights):
        sb = sub_font.getbbox(sl or " ")
        sw = sb[2] - sb[0]
        draw.text(
            ((thumb_width - sw) // 2, y_cur),
            sl,
            font=sub_font,
            fill="#ffffff",
            stroke_width=6,
            stroke_fill="#111827",
        )
        y_cur += sh + line_gap

    # Footer label
    footer_font = _load_font(28)
    draw.text((40, thumb_height - 52), "AI News Studio", font=footer_font, fill="#9ca3af")

    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path)


def _render_slide(spec: SlideSpec, index: int, total: int, path: Path) -> None:
    # Create image with gradient background
    image = Image.new("RGB", (WIDTH, HEIGHT), "#f8fafc")
    draw = ImageDraw.Draw(image)

    # Draw vertical gradient from #f8fafc to #e2e8f0
    start_color = (248, 250, 252)  # #f8fafc
    end_color = (226, 232, 240)    # #e2e8f0
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(start_color[0] + (end_color[0] - start_color[0]) * ratio)
        g = int(start_color[1] + (end_color[1] - start_color[1]) * ratio)
        b = int(start_color[2] + (end_color[2] - start_color[2]) * ratio)
        draw.line(((0, y), (WIDTH, y)), fill=(r, g, b))

    title_font = _load_font(68, bold=True)
    body_font = _load_font(40)
    meta_font = _load_font(28)
    badge_font = _load_font(30, bold=True)
    label_font = _load_font(24, bold=True)
    box_font = _load_font(32)

    draw.rectangle((0, 0, WIDTH, 120), fill="#111827")
    draw.text((80, 40), "AI News Studio", font=badge_font, fill="#ffffff")
    draw.text((WIDTH - 260, 44), f"{index}/{total}", font=meta_font, fill="#d1d5db")

    if spec.kind in {"cover", "intro", "outro"}:
        accent = "#2563eb"
    elif spec.kind == "hook":
        accent = "#dc2626"
    else:
        accent = "#f59e0b"
    draw.rectangle((80, 180, 96, 880), fill=accent)
    _draw_wrapped(draw, (140, 180), spec.title, title_font, "#111827", 1580, 18, max_lines=4)

    if spec.kind == "segment":
        # For segment slides: reduced body text, then Impact/Action boxes
        _draw_wrapped(draw, (140, 520), spec.body, body_font, "#374151", 1580, 18, max_lines=3)

        # Impact and Action boxes
        box_y_start = 720
        box_width = 700
        box_height = 150
        box_x_left = 140
        box_x_right = box_x_left + box_width + 60

        # Impact box
        impact_bg = "#fef3c7"
        impact_label_color = "#92400e"
        draw.rounded_rectangle(
            (box_x_left, box_y_start, box_x_left + box_width, box_y_start + box_height),
            radius=12, fill=impact_bg, outline="#f59e0b", width=2
        )
        draw.text((box_x_left + 16, box_y_start + 12), "Impact:", font=label_font, fill=impact_label_color)
        _draw_wrapped(draw, (box_x_left + 16, box_y_start + 50), spec.impact, box_font, "#1f2937", box_width - 32, 12, max_lines=2)

        # Action box
        action_bg = "#dbeafe"
        action_label_color = "#1e40af"
        draw.rounded_rectangle(
            (box_x_right, box_y_start, box_x_right + box_width, box_y_start + box_height),
            radius=12, fill=action_bg, outline="#3b82f6", width=2
        )
        draw.text((box_x_right + 16, box_y_start + 12), "Action:", font=label_font, fill=action_label_color)
        _draw_wrapped(draw, (box_x_right + 16, box_y_start + 50), spec.action, box_font, "#1f2937", box_width - 32, 12, max_lines=2)
    else:
        _draw_wrapped(draw, (140, 520), spec.body, body_font, "#374151", 1580, 18, max_lines=8)

    draw.rectangle((80, 925, WIDTH - 80, 928), fill="#e5e7eb")
    footer_text = "Generated from weekly AI news draft"
    if spec.kind == "segment" and spec.source:
        footer_text += f" | 出典: {spec.source}"
    draw.text((80, 955), footer_text, font=meta_font, fill="#6b7280")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


async def _synthesize_voice(text: str, path: Path) -> list[tuple[str, float]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks = _split_voice_text(text)
    chunk_dir = path.parent / f"{path.stem}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_paths: list[Path] = []
    chunk_durations: list[tuple[str, float]] = []

    async with httpx.AsyncClient(base_url=settings.VOICEVOX_BASE_URL, timeout=120.0) as client:
        for index, chunk in enumerate(chunks, 1):
            chunk_path = chunk_dir / f"{path.stem}_{index:03}.wav"
            query_res = await client.post(
                "/audio_query",
                params={"text": chunk, "speaker": settings.VOICEVOX_SPEAKER_ID},
            )
            query_res.raise_for_status()
            query_json = query_res.json()
            query_json["speedScale"] = settings.VOICEVOX_SPEED_SCALE
            query_json["postPhonemeLength"] = settings.VOICEVOX_POST_PHONEME_LENGTH
            audio_res = await client.post(
                "/synthesis",
                params={"speaker": settings.VOICEVOX_SPEAKER_ID},
                json=query_json,
            )
            audio_res.raise_for_status()
            chunk_path.write_bytes(audio_res.content)
            chunk_paths.append(chunk_path)
            chunk_durations.append((chunk, _wav_duration(chunk_path)))

    _concat_wavs(chunk_paths, path)
    return chunk_durations


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


def _write_part_srt(chunk_durations: list[tuple[str, float]], path: Path) -> None:
    cursor = 0.0
    lines: list[str] = []
    for i, (text, dur) in enumerate(chunk_durations, 1):
        end = cursor + dur
        wrapped = "\n".join(textwrap.wrap(text.replace("\n", " "), width=42))
        lines.append(f"{i}\n{_format_srt_time(cursor)} --> {_format_srt_time(end)}\n{wrapped}\n")
        cursor = end
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_srt(
    all_chunk_durations: list[list[tuple[str, float]]],
    slide_offsets: list[float],
    path: Path,
) -> None:
    cue_index = 1
    lines: list[str] = []
    for slide_offset, chunk_durations in zip(slide_offsets, all_chunk_durations):
        cursor = slide_offset
        for text, dur in chunk_durations:
            end = cursor + dur
            wrapped = "\n".join(textwrap.wrap(text.replace("\n", " "), width=42))
            lines.append(
                f"{cue_index}\n{_format_srt_time(cursor)} --> {_format_srt_time(end)}\n{wrapped}\n"
            )
            cue_index += 1
            cursor = end
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_slides(draft: VideoPlanDraft) -> list[SlideSpec]:
    slides: list[SlideSpec] = []
    if draft.hook:
        slides.append(
            SlideSpec(kind="hook", title="今週の注目", body=draft.hook, narration=draft.hook)
        )
    slides.extend([
        SlideSpec(
            kind="cover",
            title=draft.title,
            body=draft.thumbnail_text.replace("\n", " / "),
            narration=draft.title,
        ),
        SlideSpec(kind="intro", title="今週のハイライト", body=draft.intro, narration=draft.intro),
    ])
    for segment in draft.segments:
        slides.append(
            SlideSpec(
                kind="segment",
                title=f"#{segment.number} {segment.headline}",
                body=segment.summary,
                narration=segment.narration,
                source=segment.source,
                impact=segment.impact,
                action=segment.action,
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

    # Generate thumbnail
    _render_thumbnail(draft, work_dir / "thumbnail.png")

    slides = _build_slides(draft)
    padded_durations: list[float] = []
    all_chunk_durations: list[list[tuple[str, float]]] = []
    font_name = _subtitle_font()

    for index, slide in enumerate(slides, 1):
        slide_path = slides_dir / f"slide_{index:03}.png"
        audio_path = audio_dir / f"audio_{index:03}.wav"
        part_srt_rel = f"parts/part_{index:03}.srt"
        part_srt_path = work_dir / part_srt_rel

        _render_slide(slide, index, len(slides), slide_path)
        chunk_durations = await _synthesize_voice(slide.narration, audio_path)

        audio_duration = max(sum(dur for _, dur in chunk_durations), 1.0)
        part_duration = audio_duration + 0.4
        padded_durations.append(part_duration)
        all_chunk_durations.append(chunk_durations)

        _write_part_srt(chunk_durations, part_srt_path)

        fade_out_start = part_duration - 0.4
        vf = (
            f"setsar=1,fps=30"
            f",subtitles={part_srt_rel}"
            f":force_style='FontName={font_name},FontSize=20,Outline=2,MarginV=40'"
            f",fade=t=in:st=0:d=0.4"
            f",fade=t=out:st={fade_out_start:.3f}:d=0.4"
        )
        _run_ffmpeg(
            [
                "-loop",
                "1",
                "-i",
                f"slides/slide_{index:03}.png",
                "-i",
                f"audio/audio_{index:03}.wav",
                "-vf",
                vf,
                "-af",
                "apad=pad_dur=0.4,loudnorm=I=-14:TP=-1.5:LRA=11",
                "-t",
                f"{part_duration:.3f}",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "18",
                "-preset",
                "medium",
                f"parts/part_{index:03}.mp4",
            ],
            cwd=work_dir,
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

    slide_offsets: list[float] = []
    cursor = 0.0
    for dur in padded_durations:
        slide_offsets.append(cursor)
        cursor += dur

    subtitles_path = work_dir / "subtitles.srt"
    _write_srt(all_chunk_durations, slide_offsets, subtitles_path)

    chapters = _build_chapters(slides, slide_offsets)
    youtube_description = draft.description + "\n\n▼ チャプター\n" + chapters

    artifact = VideoArtifact(
        id=video_id,
        title=draft.title,
        created_at=datetime.now(timezone.utc).isoformat(),
        draft_generated_at=draft.generated_at,
        total_items=draft.total_items,
        duration_seconds=round(sum(padded_durations), 3),
        video_path=video_path.name,
        subtitles_path=subtitles_path.name,
        slide_count=len(slides),
        thumbnail_path="thumbnail.png",
        chapters=chapters,
        youtube_description=youtube_description,
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


def get_video_thumbnail(video_id: str) -> Path | None:
    path = GENERATED_DIR / video_id / "thumbnail.png"
    if not path.exists():
        return None
    return path
