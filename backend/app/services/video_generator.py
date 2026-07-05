import json
import re
import subprocess
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..core.config import settings
from ..schemas.draft import SegmentVisual, VideoPlanDraft, VideoSegment
from ..schemas.video import VideoArtifact
from .categorize import CategoryStyle, category_style
from .generate_weekly_video_plan import TITLE_JA_MAX_CHARS, contains_japanese, shorten
from .image_assets import ThemeImages, generate_segment_images, generate_theme_images
from .kana_reading import build_reading_map, to_voice_text

BASE_DIR = Path(__file__).parent.parent.parent
GENERATED_DIR = BASE_DIR / "data" / "generated"
CHARACTER_ASSETS_DIR = BASE_DIR / "app" / "assets" / "characters"
WIDTH = 1920
HEIGHT = 1080
# ニュース間の区切りスライドは無音・固定尺(テンポ優先で2秒未満)
DIVIDER_DURATION = 1.8
# YouTube向けラウドネス目標(最終2パスloudnormで保証する)
LOUDNESS_I = -16.0
LOUDNESS_TP = -1.5
LOUDNESS_LRA = 11.0
SUBTITLE_MARGIN_V = 52
SUBTITLE_MARGIN_H = 30


class ThumbnailGenerationError(RuntimeError):
    pass


@dataclass
class SlideEntry:
    number: int
    label: str
    category: str
    reason: str = ""


@dataclass
class SlideSpec:
    kind: str
    title: str
    body: str
    narration: str
    source: str = ""
    impact: str = ""
    action: str = ""
    number: int = 0
    category: str = ""
    headline: str = ""
    visual: SegmentVisual | None = None
    entries: list[SlideEntry] = field(default_factory=list)
    image: Image.Image | None = None
    week_label: str = ""
    narrator: str = "zundamon"
    reaction_line: str = ""


def _now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


_DATE_RANGE_RE = re.compile(r"[ \t]*[（(]?\d{1,2}月\d{1,2}日[〜～-]\d{1,2}月\d{1,2}日[）)]?[ \t]*")


def _strip_date_range(text: str) -> str:
    cleaned = _DATE_RANGE_RE.sub(" ", text).replace("（）", "").replace("()", "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"[ \t]*—[ \t]*", " — ", cleaned)
    return (
        cleaned.replace(" 】", "】")
        .replace("【 ", "【")
        .strip()
    )


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


def _load_mono_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # コマンド例の描画用。日本語を含むため、CJK対応フォントを先に使う。
    candidates = [
        "C:/Windows/Fonts/BIZ-UDGothicR.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/NotoSansJP-VF.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansMonoCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/CascadiaMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return _load_font(size)


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


def _draw_text_vcentered(
    draw: ImageDraw.ImageDraw,
    x: int,
    top: int,
    height: int,
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    """高さ height のボックス内にテキストを垂直センタリングして描く。
    getbbox の上余白(bb[1])を打ち消して光学的な中央に合わせる。"""
    bb = font.getbbox(text or " ")
    draw.text((x, top + (height - (bb[3] - bb[1])) // 2 - bb[1]), text, font=font, fill=fill)


def _draw_fitted(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    base_size: int,
    min_size: int,
    fill: str,
    max_width: int,
    line_gap: int,
    max_lines: int,
    bold: bool = False,
    allow_ellipsis: bool = False,
) -> int:
    """base_size から2px刻みで縮小し、max_lines以内に全文が収まる最大サイズで描く。

    min_sizeでも収まらない場合はmin_sizeで全行描く(省略記号は付けない)。
    allow_ellipsis=Trueの場合のみ、min_sizeでも収まらない残り文を「...」で省略する
    (英語原題の補助表示など、内容よりレイアウト優先の箇所向け)。
    描き終えた次のyを返す。
    """
    x, y = xy
    size = base_size
    font = _load_font(size, bold)
    lines = _wrap_by_pixels(text, font, max_width)
    while len(lines) > max_lines and size > min_size:
        size = max(size - 2, min_size)
        font = _load_font(size, bold)
        lines = _wrap_by_pixels(text, font, max_width)
    if len(lines) > max_lines and allow_ellipsis:
        lines = lines[:max_lines]
        lines[-1] = f"{lines[-1].rstrip()}..."
    # allow_ellipsis=Falseの場合、min_sizeでも max_lines に収まらないときは
    # 省略せず全行描く(内容を欠落させない)
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


def _wav_params(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as wav:
        return wav.getnchannels(), wav.getsampwidth(), wav.getframerate()


def _write_silent_wav(
    path: Path,
    duration: float,
    channels: int,
    sample_width: int,
    frame_rate: int,
) -> None:
    # 区切りスライド用の無音音声。VOICEVOX出力と同一パラメータで作ることで、
    # パートごとのAACエンコード条件を揃え concat -c copy を成立させる
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = int(round(duration * frame_rate))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(frame_rate)
        output.writeframes(b"\x00" * frame_count * channels * sample_width)


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
    pending_divider: float | None = None
    for slide, offset in zip(slides, slide_offsets):
        if slide.kind in {"divider", "illustration"}:
            # ニュースのチャプターは直前の区切り(イラスト)スライドの頭から始める
            pending_divider = offset
        elif slide.kind == "segment":
            start = pending_divider if pending_divider is not None else offset
            lines.append(f"{_format_chapter_time(start)} {slide.title}")
            pending_divider = None
        elif slide.kind in {"outro", "ranking"}:
            outro_line = f"{_format_chapter_time(offset)} まとめ（今週の重要度ランキング）"
    if outro_line:
        lines.append(outro_line)
    return "\n".join(lines)


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)))
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _render_thumbnail(
    draft: VideoPlanDraft, path: Path, background: Image.Image | None = None
) -> None:
    thumb_width, thumb_height = 1280, 720

    if background is not None:
        image = _cover_crop(background, thumb_width, thumb_height).convert("RGBA")
        # 生成背景の明るさに依存せず文字が読めるよう、下半分に黒のグラデーションスクリムを重ねる
        scrim = Image.new("RGBA", (thumb_width, thumb_height), (0, 0, 0, 0))
        scrim_draw = ImageDraw.Draw(scrim)
        for y in range(thumb_height // 2, thumb_height):
            ratio = (y - thumb_height // 2) / (thumb_height / 2)
            scrim_draw.line(((0, y), (thumb_width, y)), fill=(0, 0, 0, int(160 * ratio)))
        image = Image.alpha_composite(image, scrim)
        draw = ImageDraw.Draw(image)
    else:
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


def _save_generated_thumbnail(image: Image.Image, path: Path) -> None:
    thumb_width, thumb_height = 1280, 720
    path.parent.mkdir(parents=True, exist_ok=True)
    _cover_crop(image, thumb_width, thumb_height).convert("RGB").save(path)


def _draw_category_icon(
    draw: ImageDraw.ImageDraw, x: int, y: int, size: int, icon: str, color: str
) -> None:
    """カテゴリチップ内の簡易アイコン。(x, y) は左上、size は正方形の一辺。"""
    cx = x + size / 2
    cy = y + size / 2
    if icon == "shield":
        draw.polygon(
            [
                (cx, y),
                (x + size, y + size * 0.25),
                (x + size * 0.85, y + size * 0.75),
                (cx, y + size),
                (x + size * 0.15, y + size * 0.75),
                (x, y + size * 0.25),
            ],
            fill=color,
        )
    elif icon == "cloud":
        draw.ellipse((x, cy - size * 0.15, x + size * 0.6, cy + size * 0.45), fill=color)
        draw.ellipse((x + size * 0.25, y, x + size * 0.85, cy + size * 0.3), fill=color)
        draw.ellipse((x + size * 0.5, cy - size * 0.2, x + size, cy + size * 0.45), fill=color)
    elif icon == "chip":
        pad = size * 0.2
        draw.rectangle((x + pad, y + pad, x + size - pad, y + size - pad), fill=color)
        for offset in (size * 0.3, size * 0.5, size * 0.7):
            draw.line((x + offset, y, x + offset, y + pad), fill=color, width=2)
            draw.line((x + offset, y + size - pad, x + offset, y + size), fill=color, width=2)
            draw.line((x, y + offset, x + pad, y + offset), fill=color, width=2)
            draw.line((x + size - pad, y + offset, x + size, y + offset), fill=color, width=2)
    elif icon == "wrench":
        draw.ellipse((x, y, x + size * 0.55, y + size * 0.55), fill=color)
        draw.ellipse(
            (x + size * 0.14, y + size * 0.14, x + size * 0.41, y + size * 0.41),
            fill="#ffffff",
        )
        draw.line(
            (cx - size * 0.05, cy - size * 0.05, x + size, y + size), fill=color, width=int(size * 0.22)
        )
    elif icon == "building":
        draw.rectangle((x + size * 0.15, y, x + size * 0.85, y + size), fill=color)
        for wy in (0.2, 0.45, 0.7):
            draw.rectangle(
                (x + size * 0.3, y + size * wy, x + size * 0.45, y + size * (wy + 0.12)),
                fill="#ffffff",
            )
            draw.rectangle(
                (x + size * 0.55, y + size * wy, x + size * 0.7, y + size * (wy + 0.12)),
                fill="#ffffff",
            )
    else:  # spark
        draw.polygon(
            [
                (cx, y),
                (cx + size * 0.18, cy - size * 0.18),
                (x + size, cy),
                (cx + size * 0.18, cy + size * 0.18),
                (cx, y + size),
                (cx - size * 0.18, cy + size * 0.18),
                (x, cy),
                (cx - size * 0.18, cy - size * 0.18),
            ],
            fill=color,
        )


def _draw_category_chip(
    draw: ImageDraw.ImageDraw,
    right_x: int,
    y: int,
    style: CategoryStyle,
    font: ImageFont.ImageFont,
) -> None:
    """カテゴリ名のチップを右端 right_x に合わせて描画する。"""
    label = style.label
    bbox = font.getbbox(label)
    text_w = bbox[2] - bbox[0]
    icon_size = 26
    pad_x = 16
    chip_h = 44
    chip_w = pad_x + icon_size + 10 + text_w + pad_x
    x1 = right_x - chip_w
    draw.rounded_rectangle((x1, y, right_x, y + chip_h), radius=chip_h // 2, fill=style.color)
    _draw_category_icon(draw, x1 + pad_x, y + (chip_h - icon_size) // 2, icon_size, style.icon, "#ffffff")
    _draw_text_vcentered(draw, x1 + pad_x + icon_size + 10, y, chip_h, label, font, "#ffffff")


def _text_width(font: ImageFont.ImageFont, text: str) -> int:
    bbox = font.getbbox(text or " ")
    return bbox[2] - bbox[0]


# 全スライド共通のダークテーマ配色
_DARK_TITLE = "#f8fafc"
_DARK_BODY = "#e2e8f0"
_DARK_MUTED = "#94a3b8"
_DARK_FAINT = "#64748b"
_DARK_LINE = "#1e293b"
_HEADER_ACCENT = "#f59e0b"


def _character_expression(spec: SlideSpec) -> str:
    if spec.kind == "hook":
        return "surprise"
    if spec.kind in {"opening", "ranking"}:
        return "happy"
    if spec.kind == "illustration":
        return "point"
    if spec.kind == "divider":
        return "point"
    if spec.kind == "segment":
        if spec.category == "security":
            return "worried"
        if spec.category == "devtools":
            return "point"
        if spec.category == "business":
            return "thinking"
        if spec.category in {"hardware", "aws"}:
            return "thinking"
        if spec.category == "media":
            return "happy"
        return "talk"
    return "normal"


@lru_cache(maxsize=16)
def _load_character_image(name: str, expression: str) -> Image.Image | None:
    base = CHARACTER_ASSETS_DIR / name
    for filename in (f"{expression}.png", "normal.png"):
        path = base / filename
        if path.exists():
            return Image.open(path).convert("RGBA")
    return None


def _character_image(spec: SlideSpec) -> Image.Image | None:
    if spec.narrator == "expert":
        return None
    if not settings.CHARACTER_OVERLAY_ENABLED or not settings.CHARACTER_OVERLAY_NAME:
        return None
    return _load_character_image(settings.CHARACTER_OVERLAY_NAME, _character_expression(spec))


def _character_reserve_width(spec: SlideSpec) -> int:
    if _character_image(spec) is None:
        return 0
    if spec.kind in {"hook", "opening", "ranking"}:
        return 420
    if spec.kind == "segment":
        return 260
    return 0


def _paste_character_overlay(image: Image.Image, spec: SlideSpec) -> Image.Image:
    character = _character_image(spec)
    if character is None:
        return image

    if spec.kind == "segment":
        target_h = 330
        right = WIDTH - 56
        bottom = 826
    elif spec.kind == "illustration":
        target_h = 560
        right = WIDTH - 90
        bottom = 840
    elif spec.kind == "divider":
        target_h = 560
        right = WIDTH - 130
        bottom = 960
    else:
        target_h = 500
        right = WIDTH - 90
        bottom = 836

    target_w = round(character.width * target_h / character.height)
    resized = character.resize((target_w, target_h), Image.Resampling.LANCZOS)
    x = max(0, right - target_w)
    y = max(0, bottom - target_h)

    base = image.convert("RGBA")
    shadow = Image.new("RGBA", resized.size, (0, 0, 0, 0))
    alpha = resized.getchannel("A").point(lambda value: int(value * 0.42))
    shadow.putalpha(alpha)
    base.alpha_composite(shadow, (x + 14, y + 16))
    base.alpha_composite(resized, (x, y))
    return base


def _draw_dark_background() -> Image.Image:
    """ブランド統一のダーク背景(RGBA)。紺→紫の縦グラデーションに、
    右下の橙グローと左上の青グローを重ねて単調さを消す。"""
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    start_color = (11, 16, 38)
    end_color = (49, 35, 95)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(start_color[0] + (end_color[0] - start_color[0]) * ratio)
        g = int(start_color[1] + (end_color[1] - start_color[1]) * ratio)
        b = int(start_color[2] + (end_color[2] - start_color[2]) * ratio)
        draw.line(((0, y), (WIDTH, y)), fill=(r, g, b))

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    # 右下角付近の橙グロー
    cx, cy, radius = WIDTH - 120, HEIGHT + 60, 600
    ov_draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(249, 115, 22, 35))
    # 左上の青グロー
    cx, cy, radius = 80, -40, 520
    ov_draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(59, 130, 246, 28))
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def _draw_header(draw: ImageDraw.ImageDraw, spec: SlideSpec) -> None:
    """共通ヘッダー: ダークバンド + 橙アクセント + 番組名 + 週バッジ(+segmentはカテゴリチップ)。"""
    draw.rectangle((0, 0, WIDTH, 120), fill="#0b1120")
    draw.rectangle((0, 118, WIDTH, 122), fill=_HEADER_ACCENT)
    draw.rectangle((80, 44, 104, 68), fill=_HEADER_ACCENT)
    draw.text((120, 40), "今週のAIニュース", font=_load_font(34, bold=True), fill="#ffffff")

    badge_left = WIDTH - 80
    if spec.kind == "segment" and spec.category:
        chip_font = _load_font(30, bold=True)
        _draw_category_chip(draw, badge_left - 20, 38, category_style(spec.category), chip_font)


def _render_divider_slide(spec: SlideSpec, path: Path) -> None:
    # 区切りスライドは背景画像を使わず、濃色フルブリードで場面転換を強調する
    style = category_style(spec.category)
    image = Image.new("RGB", (WIDTH, HEIGHT), "#111827")
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, WIDTH, 14), fill=style.color)
    draw.rectangle((0, HEIGHT - 14, WIDTH, HEIGHT), fill=style.color)

    number_font = _load_font(210, bold=True)
    title_font = _load_font(64, bold=True)
    chip_font = _load_font(30, bold=True)

    number_text = f"#{spec.number}"
    number_w = _text_width(number_font, number_text)
    draw.text(((WIDTH - number_w) / 2, 240), number_text, font=number_font, fill=style.color)

    lines = _wrap_by_pixels(spec.title, title_font, 1500)[:2]
    y = 560
    for line in lines:
        line_w = _text_width(title_font, line)
        draw.text(((WIDTH - line_w) / 2, y), line, font=title_font, fill="#ffffff")
        bbox = title_font.getbbox(line or " ")
        y += bbox[3] - bbox[1] + 18

    chip_label_w = _text_width(chip_font, style.label)
    chip_w = 16 + 26 + 10 + chip_label_w + 16
    _draw_category_chip(draw, int((WIDTH + chip_w) / 2), y + 40, style, chip_font)

    path.parent.mkdir(parents=True, exist_ok=True)
    image = _paste_character_overlay(image.convert("RGBA"), spec)
    image.convert("RGB").save(path)


def _render_illustration_slide(spec: SlideSpec, path: Path) -> None:
    """ニュースごとのAI解説イラストスライド(区切りを兼ねる)。
    全画面イラスト(なければダーク背景) + 左下に巨大#N・カテゴリチップ・日本語タイトル。"""
    style = category_style(spec.category)
    if spec.image is not None:
        image = _cover_crop(spec.image, WIDTH, HEIGHT).convert("RGBA")
    else:
        image = _draw_dark_background()

    # 下半分に黒のグラデーションスクリムを重ねる(_render_thumbnailと同じ手法。強めのアルファで可読性確保)
    scrim = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    scrim_draw = ImageDraw.Draw(scrim)
    for y in range(HEIGHT // 2, HEIGHT):
        ratio = (y - HEIGHT // 2) / (HEIGHT / 2)
        scrim_draw.line(((0, y), (WIDTH, y)), fill=(0, 0, 0, int(200 * ratio)))
    image = Image.alpha_composite(image, scrim)
    draw = ImageDraw.Draw(image)

    number_text = f"#{spec.number}"
    label = spec.title.split(" ", 1)[1] if " " in spec.title else spec.title

    number_font = _load_font(130, bold=True)
    title_font = _load_font(60, bold=True)
    chip_font = _load_font(30, bold=True)

    # 巨大#N(カテゴリ色) + 右横にカテゴリチップ
    draw.text(
        (140, 620), number_text, font=number_font, fill=style.color,
        stroke_width=4, stroke_fill="#0b1120",
    )
    number_w = _text_width(number_font, number_text)
    chip_label_w = _text_width(chip_font, style.label)
    chip_w = 16 + 26 + 10 + chip_label_w + 16
    _draw_category_chip(draw, 140 + number_w + 40 + chip_w, 670, style, chip_font)

    # 日本語タイトル(1行)。字幕領域(y=880以降)にかからない位置に置く
    title_line = _wrap_by_pixels(label, title_font, WIDTH - 280)[0]
    draw.text(
        (140, 770), title_line, font=title_font, fill="#ffffff",
        stroke_width=3, stroke_fill="#111827",
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    image = _paste_character_overlay(image, spec)
    image.convert("RGB").save(path)


def _render_visual_panel(
    draw: ImageDraw.ImageDraw, visual: SegmentVisual, accent: str
) -> None:
    """セグメントスライドの図解パネル(y=495〜640)。flow=3〜4ステップ図 / command=コード風。"""
    top, bottom = 495, 640
    if visual.type == "flow":
        steps = visual.items[:4]
        n = len(steps)
        gap = 70
        box_w = (1580 - (n - 1) * gap) // n
        box_font = _load_font(30 if n == 3 else 26, bold=True)
        arrow_font = _load_font(44, bold=True)
        for i, step in enumerate(steps):
            x1 = 140 + i * (box_w + gap)
            draw.rounded_rectangle(
                (x1, top + 10, x1 + box_w, bottom - 10),
                radius=12,
                fill="#1e293b",
                outline=accent,
                width=3,
            )
            _draw_wrapped(
                draw, (x1 + 20, top + 40), step, box_font, "#f1f5f9", box_w - 40, 10, max_lines=2
            )
            if i < n - 1:
                arrow_x = x1 + box_w + 14
                draw.text((arrow_x, (top + bottom) / 2 - 30), "→", font=arrow_font, fill=accent)
    else:  # command
        mono_font = _load_mono_font(28)
        draw.rounded_rectangle(
            (140, top, 1720, bottom), radius=12, fill="#0f172a", outline="#334155", width=2
        )
        y = top + 20
        for line in visual.items[:3]:
            draw.text((172, y), line, font=mono_font, fill="#e2e8f0")
            y += 40


def _render_slide(spec: SlideSpec, path: Path) -> None:
    if spec.kind == "divider":
        _render_divider_slide(spec, path)
        return

    if spec.kind == "illustration":
        _render_illustration_slide(spec, path)
        return

    # 全スライド共通のダーク背景(生成背景は使わない)
    image = _draw_dark_background()
    draw = ImageDraw.Draw(image)
    character_reserve = _character_reserve_width(spec)
    content_width = WIDTH - 280 - character_reserve

    title_font = _load_font(68, bold=True)
    body_font = _load_font(40)
    meta_font = _load_font(28)

    _draw_header(draw, spec)

    if spec.kind == "segment" and spec.category:
        accent = category_style(spec.category).color
    elif spec.kind in {"cover", "intro", "outro", "opening", "ranking"}:
        accent = "#2563eb"
    elif spec.kind == "hook":
        accent = "#dc2626"
    else:
        accent = "#f59e0b"
    if spec.kind != "segment":
        # segmentは巨大番号透かし+箇条書き構成のため縦バーは描かない
        draw.rectangle((80, 180, 96, 810), fill=accent)

    if spec.kind == "segment":
        # 巨大番号透かし(本文より先に描き、本文を上に載せる)
        wm_font = _load_font(440, bold=True)
        wm_text = f"{spec.number:02}"
        wm_overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        wm_draw = ImageDraw.Draw(wm_overlay)
        wm_draw.text(
            (WIDTH - 60 - _text_width(wm_font, wm_text), 240),
            wm_text,
            font=wm_font,
            fill=(255, 255, 255, 18),
        )
        image = Image.alpha_composite(image, wm_overlay)
        draw = ImageDraw.Draw(image)

    if spec.kind == "hook":
        # 冒頭0〜5秒: ラベル + 大きな一言のみ(読ませない、聞かせる)
        hook_label_font = _load_font(36, bold=True)
        hook_body_font = _load_font(64, bold=True)
        draw.text((140, 200), "今週の注目ニュース", font=hook_label_font, fill="#f87171")
        _draw_wrapped(
            draw, (140, 320), spec.body, hook_body_font, _DARK_TITLE,
            content_width, 24, max_lines=3,
        )
    elif spec.kind == "opening":
        # 5〜20秒: 価値提示 + ラインナップ一覧
        _draw_wrapped(draw, (140, 180), spec.title, title_font, _DARK_TITLE, content_width, 18, max_lines=1)
        _draw_wrapped(draw, (140, 280), spec.body, _load_font(32), _DARK_MUTED, content_width, 12, max_lines=1)
        num_font = _load_font(26, bold=True)
        y = 350
        shown = spec.entries[:7]
        for entry in shown:
            style = category_style(entry.category)
            draw.rounded_rectangle((140, y, 216, y + 44), radius=10, fill=style.color)
            num_text = f"#{entry.number}"
            _draw_text_vcentered(
                draw,
                int(140 + (76 - _text_width(num_font, num_text)) / 2),
                y,
                44,
                num_text,
                num_font,
                "#ffffff",
            )
            _draw_fitted(
                draw, (240, y + 2), entry.label, 36, 26, "#f1f5f9",
                max(720, content_width - 100), 0, max_lines=1, bold=True,
            )
            y += 62
        if len(spec.entries) > len(shown):
            draw.text(
                (240, y + 2),
                f"…ほか {len(spec.entries) - len(shown)} 本",
                font=_load_font(32),
                fill=_DARK_FAINT,
            )
    elif spec.kind == "ranking":
        _draw_wrapped(draw, (140, 180), spec.title, title_font, _DARK_TITLE, content_width, 18, max_lines=1)
        medal_colors = ["#eab308", "#9ca3af", "#b45309"]
        rank_num_font = _load_font(34, bold=True)
        y = 310
        for i, entry in enumerate(spec.entries[:3]):
            color = medal_colors[i]
            cx, cy = 178, y + 38
            draw.ellipse((cx - 36, cy - 36, cx + 36, cy + 36), fill=color)
            rank_text = f"{i + 1}"
            _draw_text_vcentered(
                draw,
                int(cx - _text_width(rank_num_font, rank_text) / 2),
                cy - 36,
                72,
                rank_text,
                rank_num_font,
                "#ffffff",
            )
            _draw_fitted(
                draw, (250, y), f"{entry.label}", 40, 28, _DARK_TITLE,
                max(760, content_width - 110), 0, max_lines=1, bold=True,
            )
            if entry.reason:
                _draw_fitted(
                    draw, (250, y + 52), entry.reason, 26, 22, _DARK_MUTED,
                    max(760, content_width - 110), 0, max_lines=1,
                )
            y += 110
        rest = spec.entries[3:7]
        rest_font = _load_font(30)
        y = 636
        for entry in rest:
            draw.text((160, y), f"{entry.number}位  {entry.label}", font=rest_font, fill="#cbd5e1")
            y += 44
        if len(spec.entries) > 7:
            draw.text((160, y), f"…ほか {len(spec.entries) - 7} 本", font=rest_font, fill=_DARK_FAINT)
    elif spec.kind == "segment":
        # タイトル → 一言ベネフィット → 英語原題 → (図解) → 箇条書き の縦積み構成。
        # タイトルが2行になった場合は後続の基準yを押し下げて重なりを防ぐ
        y_after_title = _draw_fitted(
            draw, (140, 180), spec.title, 64, 48, _DARK_TITLE, content_width, 18, max_lines=2, bold=True
        )
        benefit_y = max(330, y_after_title + 6)
        y_after_benefit = _draw_fitted(
            draw, (140, benefit_y), spec.body, 34, 26, "#fbbf24", content_width, 8, max_lines=1, bold=True
        )
        # 元の見出し(英語など)は事実の原典として小さく併記する。補助情報なので、
        # min_sizeまで縮小してもなお収まらない場合に限り「...」省略を許容する
        if spec.headline and spec.headline not in spec.title:
            _draw_fitted(
                draw, (140, max(395, y_after_benefit + 10)), spec.headline, 24, 20, _DARK_FAINT,
                content_width, 0, max_lines=1, allow_ellipsis=True,
            )

        if spec.visual:
            _render_visual_panel(draw, spec.visual, accent)

        # 箇条書き(旧Impact/Actionボックスの代替)。図解の有無で開始yを変える。
        # 図解ありは残り高さが少ない(665〜820)ため max_lines=1 で先に縮小させ、
        # バジェット超過の長文だけが最小サイズの折り返しになるようにする
        bullet_label_font = _load_font(26, bold=True)
        bullet_max_lines = 1 if spec.visual else 2
        y = 665 if spec.visual else 500
        for bullet_label, bullet_body in (("何が変わるか", spec.impact), ("次にやること", spec.action)):
            # アクセント色の正方形ビュレット + ラベル + 同じ行から始まる本文
            draw.rectangle((140, y + 11, 152, y + 23), fill=accent)
            draw.text((164, y + 2), bullet_label, font=bullet_label_font, fill="#93c5fd")
            body_x = 164 + _text_width(bullet_label_font, bullet_label) + 28
            y = _draw_fitted(
                draw, (body_x, y), bullet_body, 30, 22, _DARK_BODY,
                max(520, WIDTH - 200 - character_reserve - body_x), 8, max_lines=bullet_max_lines,
            )
            y += 40
    else:
        _draw_wrapped(draw, (140, 180), spec.title, title_font, _DARK_TITLE, content_width, 18, max_lines=4)
        _draw_wrapped(draw, (140, 490), spec.body, body_font, _DARK_BODY, content_width, 18, max_lines=4)

    # Keep everything above y=860; the area below is reserved for burned-in subtitles
    draw.rectangle((80, 830, WIDTH - 80, 833), fill=_DARK_LINE)
    if spec.kind == "segment" and spec.source:
        draw.text((80, 845), f"出典: {spec.source}", font=meta_font, fill=_DARK_FAINT)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = _paste_character_overlay(image, spec)
    image.convert("RGB").save(path)


async def _synthesize_voice(
    text: str,
    path: Path,
    reading_map: dict[str, str] | None = None,
    speaker_id: int = settings.VOICEVOX_SPEAKER_ID,
) -> list[tuple[str, float]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # チャンク分割は原文に対して行い、返り値のtextも原文を保つ(字幕は英語表記のまま)。
    # VOICEVOXへはカナ変換後のテキストだけを渡す。
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
                params={
                    "text": to_voice_text(chunk, reading_map),
                    "speaker": speaker_id,
                },
            )
            query_res.raise_for_status()
            query_json = query_res.json()
            query_json["speedScale"] = settings.VOICEVOX_SPEED_SCALE
            query_json["postPhonemeLength"] = settings.VOICEVOX_POST_PHONEME_LENGTH
            audio_res = await client.post(
                "/synthesis",
                params={"speaker": speaker_id},
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


def _run_ffmpeg(args: list[str], cwd: Path | None = None) -> str:
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
    return result.stderr


def _loudnorm_target() -> str:
    return f"loudnorm=I={LOUDNESS_I:g}:TP={LOUDNESS_TP:g}:LRA={LOUDNESS_LRA:g}"


def _normalize_final_loudness(work_dir: Path, video_name: str) -> None:
    """concat後の動画全体を2パスloudnormで -16 LUFS / TP -1.5 dBTP に揃える。

    音声のみ再エンコード(-c:v copy)なので安価。計測や適用に失敗した場合は
    パート単位の正規化済み音声のまま(目標値に近い)とし、動画生成は落とさない。
    """
    try:
        stderr = _run_ffmpeg(
            [
                "-i",
                video_name,
                "-af",
                f"{_loudnorm_target()}:print_format=json",
                "-f",
                "null",
                "-",
            ],
            cwd=work_dir,
        )
        start = stderr.rfind("{")
        end = stderr.rfind("}")
        if start < 0 or end <= start:
            return
        measured = json.loads(stderr[start : end + 1])
        af = (
            f"{_loudnorm_target()}"
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}"
            f":linear=true"
        )
        normalized_name = "video_normalized.mp4"
        _run_ffmpeg(
            [
                "-i",
                video_name,
                "-c:v",
                "copy",
                "-af",
                af,
                "-ar",
                "48000",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                normalized_name,
            ],
            cwd=work_dir,
        )
        (work_dir / normalized_name).replace(work_dir / video_name)
    except Exception:
        return


def _format_srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours = millis // 3_600_000
    millis %= 3_600_000
    minutes = millis // 60_000
    millis %= 60_000
    secs = millis // 1000
    millis %= 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


# 話者ごとの字幕色(淡色。ずんだもん=淡緑、AI専門家=淡青)。行ごとに<font color>で
# 埋め込むため、force_styleのPrimaryColour(ASSのBGR形式)ではなく通常のRRGGBB表記を使う
_SUBTITLE_COLOR_ZUNDAMON = "#8BE58B"
_SUBTITLE_COLOR_EXPERT = "#8BC7F0"


def _subtitle_color(narrator: str) -> str:
    return _SUBTITLE_COLOR_EXPERT if narrator == "expert" else _SUBTITLE_COLOR_ZUNDAMON


_SUBTITLE_BREAK_CHARS = "、。！？!?　 "


def _split_subtitle_cues(
    text: str,
    duration: float,
    max_line_chars: int = 24,
    max_lines: int = 2,
) -> list[tuple[str, float]]:
    normalized = text.replace("\n", " ").strip()
    max_cue_chars = max_line_chars * max_lines
    if not normalized or len(normalized) <= max_cue_chars:
        return [(normalized, duration)]

    cues: list[str] = []
    remaining = normalized
    while len(remaining) > max_cue_chars:
        window = remaining[:max_cue_chars]
        split_at = max(window.rfind(ch) for ch in _SUBTITLE_BREAK_CHARS)
        if split_at < max_cue_chars // 2:
            split_at = max_cue_chars - 1
        cues.append(remaining[: split_at + 1].strip())
        remaining = remaining[split_at + 1 :].strip()
    if remaining:
        cues.append(remaining)

    total_chars = sum(len(cue) for cue in cues) or 1
    return [(cue, duration * len(cue) / total_chars) for cue in cues]


def _wrap_cue_lines(text: str, max_line_chars: int) -> list[str]:
    # textwrap は英単語・ハイフン優先で折って3行以上になり得るため、2行保証の自前分割を使う
    if len(text) <= max_line_chars:
        return [text]
    min_split = len(text) - max_line_chars
    window = text[:max_line_chars]
    split_at = max(window.rfind(ch) for ch in _SUBTITLE_BREAK_CHARS)
    if split_at + 1 < min_split:
        split_at = max_line_chars - 1
    first = text[: split_at + 1].rstrip()
    second = text[split_at + 1 :].strip()
    return [first, second] if second else [first]


def _srt_cues(
    chunk_durations: list[tuple[str, float, str]],
    offset: float,
    start_index: int,
    max_line_chars: int = 24,
) -> tuple[list[str], int]:
    cursor = offset
    index = start_index
    lines: list[str] = []
    for text, dur, narrator in chunk_durations:
        color = _subtitle_color(narrator)
        for cue_text, cue_dur in _split_subtitle_cues(text, dur, max_line_chars):
            end = cursor + cue_dur
            wrapped = "\n".join(_wrap_cue_lines(cue_text, max_line_chars))
            colored = f'<font color="{color}">{wrapped}</font>'
            lines.append(
                f"{index}\n{_format_srt_time(cursor)} --> {_format_srt_time(end)}\n{colored}\n"
            )
            index += 1
            cursor = end
    return lines, index


def _write_part_srt(chunk_durations: list[tuple[str, float, str]], path: Path) -> None:
    lines, _ = _srt_cues(chunk_durations, 0.0, 1)
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_srt(
    all_chunk_durations: list[list[tuple[str, float, str]]],
    slide_offsets: list[float],
    path: Path,
) -> None:
    cue_index = 1
    lines: list[str] = []
    for slide_offset, chunk_durations in zip(slide_offsets, all_chunk_durations):
        slide_lines, cue_index = _srt_cues(chunk_durations, slide_offset, cue_index)
        lines.extend(slide_lines)
    path.write_text("\n".join(lines), encoding="utf-8")


def _save_theme_assets(theme: ThemeImages, assets_dir: Path) -> None:
    # 使用した生成背景を成果物と一緒に保存する(再現・デバッグ用)
    if theme.thumbnail is None and theme.thumbnail_bg is None and theme.slide_bg is None:
        return
    assets_dir.mkdir(parents=True, exist_ok=True)
    if theme.thumbnail is not None:
        theme.thumbnail.save(assets_dir / "thumbnail_generated.png")
    if theme.thumbnail_bg is not None:
        theme.thumbnail_bg.save(assets_dir / "thumbnail_bg.png")
    if theme.slide_bg is not None:
        theme.slide_bg.save(assets_dir / "slide_bg.png")


def _save_segment_image_assets(segment_images: dict[int, Image.Image], assets_dir: Path) -> None:
    # 使用したセグメント解説イラストを成果物と一緒に保存する(再現・デバッグ用)
    if not segment_images:
        return
    assets_dir.mkdir(parents=True, exist_ok=True)
    for number, image in segment_images.items():
        image.save(assets_dir / f"segment_{number:02}.png")


def _display_label(segment: VideoSegment) -> str:
    """スライドに出す表示ラベル。title_ja が規約(日本語・短尺)を満たさない限り、
    フル英語見出しをそのまま主タイトル・区切り・ラインナップ・ランキングに出さない。"""
    title_ja = segment.title_ja
    if title_ja and contains_japanese(title_ja) and len(title_ja) <= TITLE_JA_MAX_CHARS + 4:
        return title_ja
    return shorten(segment.headline, 24)


def _build_slides(
    draft: VideoPlanDraft, segment_images: dict[int, Image.Image] | None = None
) -> list[SlideSpec]:
    segment_images = segment_images or {}
    entries = [
        SlideEntry(
            number=segment.number,
            label=_display_label(segment),
            category=segment.category,
            reason=segment.rank_reason,
        )
        for segment in draft.segments
    ]

    slides: list[SlideSpec] = []
    # 冒頭は20秒以内: フック(〜5秒) + オープニング(〜15秒)の2枚のみ。
    # 旧構成の cover(タイトル読み上げ)と intro は廃止し、ラインナップ一覧に統合した。
    if draft.hook:
        slides.append(
            SlideSpec(
                kind="hook",
                title="今週の注目",
                body=draft.hook,
                narration=draft.hook,
                week_label=draft.week_label,
                narrator="zundamon",
            )
        )
    slides.append(
        SlideSpec(
            kind="opening",
            title="今週のAIニュースラインナップ",
            body=f"重要ニュース{len(draft.segments)}本を短時間でキャッチアップ",
            narration=draft.intro,
            entries=entries,
            narrator="zundamon",
        )
    )
    for segment in draft.segments:
        label = _display_label(segment)

        # イラストスライドはずんだもんの一言導入(intro_line)を読み上げる区切り兼用スライド。
        # intro_lineは保証層(prepare_draft_for_video)により常に非空なので、画像の有無に
        # かかわらず常に挿入する(画像がない場合はダーク背景フォールバックで描画される)。
        slides.append(
            SlideSpec(
                kind="illustration",
                title=f"#{segment.number} {label}",
                body="",
                narration=segment.intro_line,
                number=segment.number,
                category=segment.category,
                image=segment_images.get(segment.number),
                week_label=draft.week_label,
                narrator="zundamon",
            )
        )

        slides.append(
            SlideSpec(
                kind="segment",
                title=f"#{segment.number} {label}",
                body=segment.summary,
                narration=segment.narration,
                source=segment.source,
                impact=segment.impact,
                action=segment.action,
                number=segment.number,
                category=segment.category,
                headline=segment.headline,
                visual=segment.visual,
                week_label=draft.week_label,
                narrator="expert",
                reaction_line=segment.reaction_line,
            )
        )
    slides.append(
        SlideSpec(
            kind="ranking",
            title="今週の重要度ランキング",
            body="",
            narration=draft.outro,
            entries=entries,
            week_label=draft.week_label,
            narrator="zundamon",
        )
    )
    return slides


async def generate_video_from_draft(draft: VideoPlanDraft) -> VideoArtifact:
    video_id = _now_id()
    work_dir = GENERATED_DIR / video_id
    slides_dir = work_dir / "slides"
    audio_dir = work_dir / "audio"
    parts_dir = work_dir / "parts"
    work_dir.mkdir(parents=True, exist_ok=False)
    parts_dir.mkdir(parents=True, exist_ok=True)

    # 背景画像を生成(未設定・失敗時は None で従来デザインにフォールバック)
    theme = await generate_theme_images(draft)
    _save_theme_assets(theme, work_dir / "assets")

    # ニュースごとのAI解説イラストを生成(未設定・失敗したセグメントは辞書に含まれない)
    segment_images = await generate_segment_images(draft.segments)
    _save_segment_image_assets(segment_images, work_dir / "assets")

    if theme.thumbnail is None:
        raise ThumbnailGenerationError(
            "サムネイル画像の生成に失敗しました。IMAGE_GEN_ENABLED と GEMINI_PROJECT、"
            "画像生成モデルの権限・クォータを確認してください。"
        )
    _save_generated_thumbnail(theme.thumbnail, work_dir / "thumbnail.png")

    slides = _build_slides(draft, segment_images)
    reading_map = await build_reading_map(
        [slide.narration for slide in slides if slide.narration]
        + [slide.reaction_line for slide in slides if slide.reaction_line]
    )
    padded_durations: list[float] = []
    all_chunk_durations: list[list[tuple[str, float, str]]] = []
    font_name = _subtitle_font()
    voice_wav_params: tuple[int, int, int] | None = None
    REACTION_GAP = 0.3

    for index, slide in enumerate(slides, 1):
        slide_path = slides_dir / f"slide_{index:03}.png"
        audio_path = audio_dir / f"audio_{index:03}.wav"
        part_srt_rel = f"parts/part_{index:03}.srt"
        part_srt_path = work_dir / part_srt_rel

        _render_slide(slide, slide_path)

        if slide.kind == "divider":
            # 区切りは無音・固定尺・字幕なし。WAVパラメータはVOICEVOX出力に揃える
            params = voice_wav_params or (1, 2, 24000)
            _write_silent_wav(audio_path, DIVIDER_DURATION, *params)
            chunk_durations = []
            part_duration = DIVIDER_DURATION
            fade_duration = 0.3
        else:
            speaker_id = (
                settings.VOICEVOX_SPEAKER_ID_EXPERT
                if slide.narrator == "expert"
                else settings.VOICEVOX_SPEAKER_ID
            )
            primary_durations = await _synthesize_voice(
                slide.narration, audio_path, reading_map, speaker_id
            )
            chunk_durations = [(text, dur, slide.narrator) for text, dur in primary_durations]
            if voice_wav_params is None:
                voice_wav_params = _wav_params(audio_path)

            if slide.reaction_line:
                # AI専門家の解説の直後、画面はそのままでずんだもんの感想を続けて話す。
                # 画像を差し替えず音声だけを継ぎ足すので、無音の間を挟んでから
                # 別話者で合成した音声を同じWAVに連結する
                reaction_audio_path = audio_dir / f"audio_{index:03}_reaction.wav"
                reaction_durations = await _synthesize_voice(
                    slide.reaction_line,
                    reaction_audio_path,
                    reading_map,
                    settings.VOICEVOX_SPEAKER_ID,
                )
                gap_path = audio_dir / f"audio_{index:03}_gap.wav"
                _write_silent_wav(gap_path, REACTION_GAP, *voice_wav_params)
                _concat_wavs([audio_path, gap_path, reaction_audio_path], audio_path)
                chunk_durations.append(("", REACTION_GAP, slide.narrator))
                chunk_durations += [
                    (text, dur, "zundamon") for text, dur in reaction_durations
                ]

            audio_duration = max(sum(dur for _, dur, _ in chunk_durations), 1.0)
            part_duration = audio_duration + 0.4
            fade_duration = 0.4

        padded_durations.append(part_duration)
        all_chunk_durations.append(chunk_durations)

        fade_out_start = part_duration - fade_duration
        vf_filters = ["setsar=1,fps=30"]
        if chunk_durations:
            _write_part_srt(chunk_durations, part_srt_path)
            vf_filters.append(
                f"subtitles={part_srt_rel}"
                f":force_style='FontName={font_name},FontSize=17,BorderStyle=3,Outline=1,Shadow=0"
                f",BackColour=&H60000000,MarginV={SUBTITLE_MARGIN_V}"
                f",MarginL={SUBTITLE_MARGIN_H},MarginR={SUBTITLE_MARGIN_H}'"
            )
        vf_filters.append(f"fade=t=in:st=0:d={fade_duration}")
        vf_filters.append(f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration}")
        vf = ",".join(vf_filters)

        args = [
            "-loop",
            "1",
            "-i",
            f"slides/slide_{index:03}.png",
            "-i",
            f"audio/audio_{index:03}.wav",
            "-vf",
            vf,
        ]
        if slide.kind != "divider":
            # 無音区切りへの loudnorm は不安定なため音声付きパートのみ正規化する
            args += [
                "-af",
                f"apad=pad_dur=0.4,loudnorm=I={LOUDNESS_I:g}:TP={LOUDNESS_TP:g}:LRA={LOUDNESS_LRA:g}",
            ]
        args += [
            "-t",
            f"{part_duration:.3f}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            # loudnorm の内部リサンプリング有無に関わらず全パートのAAC条件を揃え、
            # concat -c copy を成立させるため出力サンプルレート/チャンネルを固定する
            "-ar",
            "48000",
            "-ac",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-preset",
            "medium",
            f"parts/part_{index:03}.mp4",
        ]
        _run_ffmpeg(args, cwd=work_dir)

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

    # YouTube向けラウドネス(-16 LUFS / TP -1.5 dBTP)を動画全体で保証する
    _normalize_final_loudness(work_dir, "video.mp4")

    slide_offsets: list[float] = []
    cursor = 0.0
    for dur in padded_durations:
        slide_offsets.append(cursor)
        cursor += dur

    subtitles_path = work_dir / "subtitles.srt"
    _write_srt(all_chunk_durations, slide_offsets, subtitles_path)

    # 字幕原文と読み上げテキストの対応(カナ変換の検証用)
    voice_texts = [
        {
            "slide": slide_index,
            "chunks": [
                {"text": chunk, "voice": to_voice_text(chunk, reading_map)}
                for chunk, _, _ in chunk_durations
                if chunk
            ],
        }
        for slide_index, chunk_durations in enumerate(all_chunk_durations, 1)
    ]
    (work_dir / "voice_texts.json").write_text(
        json.dumps(voice_texts, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    chapters = _build_chapters(slides, slide_offsets)
    youtube_description = _strip_date_range(draft.description) + "\n\n▼ チャプター\n" + chapters
    title = _strip_date_range(draft.title)
    title_candidates = [_strip_date_range(candidate) for candidate in draft.title_candidates]

    artifact = VideoArtifact(
        id=video_id,
        title=title,
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
        title_candidates=title_candidates,
        thumbnail_text_candidates=draft.thumbnail_text_candidates,
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
