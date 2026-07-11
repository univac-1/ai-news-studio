import json
import logging
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..core.config import settings
from ..schemas.draft import SegmentVisual, VideoPlanDraft, VideoSegment
from ..schemas.video import ReviewReport, VideoArtifact
from .categorize import CategoryStyle, category_style
from .generate_weekly_video_plan import contains_japanese
from .image_assets import (
    ThemeImages,
    generate_segment_images,
    generate_song_background,
    generate_theme_images,
)
from .kana_reading import build_reading_map, to_voice_text
from .song import check_song_support, generate_song_lyrics, synthesize_song
from .video_assets import generate_opening_clip, generate_segment_clips, generate_song_clip
from .video_review import review_and_retake

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
GENERATED_DIR = BASE_DIR / "data" / "generated"
CHARACTER_ASSETS_DIR = BASE_DIR / "app" / "assets" / "characters"
BGM_ASSETS_DIR = BASE_DIR / "app" / "assets" / "bgm"
WIDTH = 1920
HEIGHT = 1080
# ニュース間の区切りスライドは無音・固定尺(テンポ優先で2秒未満)
DIVIDER_DURATION = 1.8
# AI専門家の解説とずんだもんの感想の間の無音ギャップ
REACTION_GAP = 0.3
# YouTube向けラウドネス目標(最終2パスloudnormで保証する)
LOUDNESS_I = -16.0
LOUDNESS_TP = -1.5
LOUDNESS_LRA = 11.0
SUBTITLE_MARGIN_V = 40
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
    # illustrationスライド用のVeo製背景クリップ(work_dir/assets/clips配下のmp4)。
    # Noneならimage(静止イラスト)またはダーク背景で描画する
    clip: Path | None = None
    week_label: str = ""
    narrator: str = "zundamon"
    reaction_line: str = ""
    lyrics: list[str] = field(default_factory=list)


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


def _compact_base_size(base_size: int, compact: bool) -> int:
    """compact=True時、レビューでのはみ出し指摘に対する決定的な救済策としてベース
    フォントサイズを約15%縮小する(_draw_fitted等の開始サイズにのみ影響し、
    min_sizeは変えない)。"""
    return max(round(base_size * 0.85), 1) if compact else base_size


def _compact_max_lines(max_lines: int, compact: bool) -> int:
    """compact=True時、折り返し許容行数を1行増やす(縮小だけで収まらない長文向け)。"""
    return max_lines + 1 if compact else max_lines


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
    # スライド列(=時系列順)をそのまま辿って追記するだけで、チャプターは常に
    # 時刻昇順になる(song は現在フックの直後・オープニングの前にあるため、
    # 末尾に別枠で追記していた旧実装だと時刻が逆転してしまう)。
    entries: list[tuple[float, str]] = [(0.0, "オープニング")]
    pending_divider: float | None = None
    for slide, offset in zip(slides, slide_offsets):
        if slide.kind in {"divider", "illustration"}:
            # ニュースのチャプターは直前の区切り(イラスト)スライドの頭から始める
            pending_divider = offset
        elif slide.kind == "segment":
            start = pending_divider if pending_divider is not None else offset
            entries.append((start, slide.title))
            pending_divider = None
        elif slide.kind in {"outro", "ranking"}:
            entries.append((offset, "まとめ（今週の重要度ランキング）"))
        elif slide.kind == "song":
            entries.append((offset, "ずんだもんニュースソング"))

    # YouTubeのチャプターは各10秒以上必要で、10秒未満の区間が1つでもあると
    # 全チャプターが無効化される。直前に採用したチャプターから10秒未満で始まる
    # エントリはスキップする(例: 〜5秒のフック直後に始まる歌)。
    lines: list[str] = []
    last_emitted: float | None = None
    for seconds, title in entries:
        if last_emitted is not None and seconds - last_emitted < 10.0:
            continue
        lines.append(f"{_format_chapter_time(seconds)} {title}")
        last_emitted = seconds
    return "\n".join(lines)


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)))
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _headline_text_layer(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    gradient_top: tuple[int, int, int],
    gradient_bottom: tuple[int, int, int],
    outer_stroke: int,
    inner_stroke: int,
) -> Image.Image:
    """黒の極太縁 + 白の中縁 + 縦グラデーション塗りの見出しテキストレイヤーを返す。

    PILのstrokeは1回の描画で1色しか使えないため、外縁→内縁→本体の3回に分けて
    重ね描きし、本体はテキスト形状をマスクにしたグラデーションで塗る。
    """
    bbox = font.getbbox(text or " ")
    pad = outer_stroke + 10
    width = bbox[2] - bbox[0] + pad * 2
    height = bbox[3] - bbox[1] + pad * 2
    origin = (pad - bbox[0], pad - bbox[1])

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.text(origin, text, font=font, fill="#0b0f1a", stroke_width=outer_stroke, stroke_fill="#0b0f1a")
    if inner_stroke > 0:
        draw.text(origin, text, font=font, fill="#ffffff", stroke_width=inner_stroke, stroke_fill="#ffffff")

    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).text(origin, text, font=font, fill=255)
    gradient = Image.new("RGB", (width, height))
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(gradient_top[0] + (gradient_bottom[0] - gradient_top[0]) * ratio)
        g = int(gradient_top[1] + (gradient_bottom[1] - gradient_top[1]) * ratio)
        b = int(gradient_top[2] + (gradient_bottom[2] - gradient_top[2]) * ratio)
        gradient_draw.line(((0, y), (width, y)), fill=(r, g, b))
    layer.paste(gradient, (0, 0), mask)
    return layer


def _paste_with_drop_shadow(
    base: Image.Image,
    layer: Image.Image,
    pos: tuple[int, int],
    offset: tuple[int, int] = (7, 9),
    blur: int = 6,
    opacity: float = 0.55,
) -> None:
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow_alpha = layer.getchannel("A").point(lambda v: int(v * opacity))
    shadow.paste(Image.new("RGBA", layer.size, (5, 5, 10, 255)), (0, 0), shadow_alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(shadow, (pos[0] + offset[0], pos[1] + offset[1]))
    base.alpha_composite(layer, pos)


# モデル名等の連なり判定。「GPT-6」「v2.5」を分断しないよう記号も含める
_ASCII_RE = re.compile(r"[0-9A-Za-z\-+._]")


def _split_headline(text: str) -> list[str]:
    """長いメイン見出しを2行に割る。英数字の連なり(モデル名など)は分断しない。"""
    if len(text) < 6:
        return [text]
    middle = len(text) / 2
    best: int | None = None
    for i in range(1, len(text)):
        # 英数字の途中(「GPT-6」の中など)では改行しない
        if _ASCII_RE.match(text[i - 1]) and _ASCII_RE.match(text[i]):
            continue
        if best is None or abs(i - middle) < abs(best - middle):
            best = i
    if best is None:
        return [text]
    return [text[:best], text[best:]]


def _fit_font_size(lines: list[str], max_width: int, start: int, minimum: int) -> int:
    size = start
    while True:
        font = _load_font(size, bold=True)
        if max(_text_width(font, line) for line in lines) <= max_width:
            return size
        if size <= minimum:
            return minimum
        size = max(size - 8, minimum)


def _thumbnail_character_layer(target_height: int) -> Image.Image | None:
    """驚き顔ずんだもんのバストアップに白フチを付けたレイヤー。無効時はNone。"""
    if not settings.CHARACTER_OVERLAY_ENABLED or not settings.CHARACTER_OVERLAY_NAME:
        return None
    character = _load_character_image(settings.CHARACTER_OVERLAY_NAME, "surprise")
    if character is None:
        return None

    # 全身立ち絵の上半分(顔+挙げた両手)だけ使い、顔を大きく見せる
    bust = character.crop((0, 0, character.width, round(character.height * 0.56)))
    target_w = round(bust.width * target_height / bust.height)
    resized = bust.resize((target_w, target_height), Image.Resampling.LANCZOS)

    # 白フチはアルファ膨張で作る。切断した下端にも付くが、下端は画面外に
    # 落として配置するため見えない
    outline_pad = 8
    padded = Image.new(
        "RGBA", (target_w + outline_pad * 2, target_height + outline_pad * 2), (0, 0, 0, 0)
    )
    padded.alpha_composite(resized, (outline_pad, outline_pad))
    outline_alpha = padded.getchannel("A").filter(ImageFilter.MaxFilter(outline_pad * 2 + 1))
    layer = Image.new("RGBA", padded.size, (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", padded.size, (255, 255, 255, 255)), (0, 0), outline_alpha)
    layer.alpha_composite(padded)
    return layer


def _render_thumbnail(
    draft: VideoPlanDraft, path: Path, background: Image.Image | None = None
) -> None:
    """バズ寄せの定番レイアウトでサムネイルを描画する。

    生成背景(文字なし)の上に、左に黄グラデ+黒縁の巨大見出し、
    その下に赤帯のサブコピー、右下に白フチ付きずんだもん(驚き顔)を重ねる。
    テキストとキャラをローカル合成することで、日本語の誤字や配置崩れを防ぐ。
    """
    thumb_width, thumb_height = 1280, 720

    if background is not None:
        image = _cover_crop(background, thumb_width, thumb_height).convert("RGBA")
    else:
        # フォールバック: 紺→藍の縦グラデーション背景
        image = Image.new("RGBA", (thumb_width, thumb_height))
        draw = ImageDraw.Draw(image)
        start_color = (13, 18, 38)
        end_color = (30, 58, 138)
        for y in range(thumb_height):
            ratio = y / thumb_height
            r = int(start_color[0] + (end_color[0] - start_color[0]) * ratio)
            g = int(start_color[1] + (end_color[1] - start_color[1]) * ratio)
            b = int(start_color[2] + (end_color[2] - start_color[2]) * ratio)
            draw.line(((0, y), (thumb_width, y)), fill=(r, g, b, 255))

    # 背景の明るさに依存せず文字が読めるよう、テキストが載る左側を横グラデーションで暗くする
    scrim = Image.new("RGBA", (thumb_width, thumb_height), (0, 0, 0, 0))
    scrim_draw = ImageDraw.Draw(scrim)
    scrim_end_x = round(thumb_width * 0.62)
    for x in range(scrim_end_x):
        ratio = 1.0 - x / scrim_end_x
        scrim_draw.line(((x, 0), (x, thumb_height)), fill=(2, 4, 12, int(150 * ratio)))
    image = Image.alpha_composite(image, scrim)

    # thumbnail_text: 1行目=メイン(パワーワード)、2行目以降=サブコピー
    text_lines = [line.strip() for line in draft.thumbnail_text.split("\n")]
    main_line = text_lines[0] if text_lines and text_lines[0] else "AI速報"
    sub_line = " ".join(line for line in text_lines[1:] if line)

    # メイン見出し: まず1行で試し、小さくなりすぎるなら2行に割って各行を大きくする
    badge_text = "今週のAI速報"
    badge_visible = badge_text not in main_line
    text_max_width = 740
    main_min_size = 52
    sub_min_size = 22
    main_size = _fit_font_size([main_line], text_max_width, start=250, minimum=main_min_size)
    main_lines = [main_line]
    if main_size < 150:
        split_lines = _split_headline(main_line)
        if len(split_lines) == 2:
            split_size = _fit_font_size(split_lines, text_max_width, start=210, minimum=main_min_size)
            if split_size > main_size:
                main_lines = split_lines
                main_size = split_size
    main_font = _load_font(main_size, bold=True)
    if max(_text_width(main_font, line) for line in main_lines) > text_max_width:
        main_lines = _wrap_by_pixels(main_line, main_font, text_max_width)

    # サブコピー: 赤帯ボックスに白文字
    sub_size = (
        _fit_font_size([sub_line], text_max_width - 60, start=54, minimum=sub_min_size)
        if sub_line
        else 0
    )

    def build_text_block() -> Image.Image:
        main_font = _load_font(main_size, bold=True)
        outer_stroke = max(round(main_size * 0.085), 8)
        inner_stroke = max(round(main_size * 0.03), 3)
        main_layers = [
            _headline_text_layer(
                line,
                main_font,
                gradient_top=(255, 244, 92),
                gradient_bottom=(255, 170, 0),
                outer_stroke=outer_stroke,
                inner_stroke=inner_stroke,
            )
            for line in main_lines
        ]

        sub_layer: Image.Image | None = None
        if sub_line:
            sub_font = _load_font(sub_size, bold=True)
            sub_bbox = sub_font.getbbox(sub_line)
            pad_x, pad_y = 24, 14
            box_w = sub_bbox[2] - sub_bbox[0] + pad_x * 2
            box_h = sub_bbox[3] - sub_bbox[1] + pad_y * 2
            sub_layer = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            sub_draw = ImageDraw.Draw(sub_layer)
            sub_draw.rounded_rectangle((0, 0, box_w - 1, box_h - 1), radius=10, fill="#dc2626")
            sub_draw.text(
                (pad_x - sub_bbox[0], pad_y - sub_bbox[1]), sub_line, font=sub_font, fill="#ffffff"
            )

        # テキストブロックを1枚のレイヤーにまとめ、少し傾けて勢いを出す
        line_gap = 6
        block_w = max(
            [layer.width for layer in main_layers] + ([sub_layer.width] if sub_layer else [])
        )
        block_h = sum(layer.height + line_gap for layer in main_layers)
        if sub_layer is not None:
            block_h += sub_layer.height + 14
        block = Image.new("RGBA", (block_w + 40, block_h + 40), (0, 0, 0, 0))
        y_cur = 20
        for layer in main_layers:
            block.alpha_composite(layer, (20, y_cur))
            y_cur += layer.height + line_gap
        if sub_layer is not None:
            _paste_with_drop_shadow(block, sub_layer, (20 + outer_stroke, y_cur + 8), offset=(5, 6))
        rotated = block.rotate(2.4, resample=Image.Resampling.BICUBIC, expand=True)
        return rotated

    rotated = build_text_block()
    max_text_height = thumb_height - (112 if badge_visible else 52)
    while rotated.height > max_text_height and (
        main_size > main_min_size or (sub_line and sub_size > sub_min_size)
    ):
        if main_size > main_min_size:
            main_size = max(main_size - 8, main_min_size)
        if sub_line and sub_size > sub_min_size:
            sub_size = max(sub_size - 4, sub_min_size)
        rotated = build_text_block()

    # 見出しは左寄せ、バッジと下端の間で垂直センタリング。
    # ブロックが縦長のときはサブ帯が下端で切れないよう上に寄せる(バッジを描く場合は
    # バッジの下まで、描かない場合は上端近くまで許容)
    text_x = 30
    text_y = max((thumb_height - rotated.height) // 2 + 14, 96)
    bottom_limit = thumb_height - 16
    if text_y + rotated.height > bottom_limit:
        text_y = max(bottom_limit - rotated.height, 96 if badge_visible else 36)
    _paste_with_drop_shadow(image, rotated, (text_x, text_y), offset=(8, 10), blur=7)

    # 右下にずんだもん(驚き顔・白フチ)。下端は画面外に落としてバストアップに見せる
    character_layer = _thumbnail_character_layer(target_height=430)
    if character_layer is not None:
        char_x = thumb_width - character_layer.width + 26
        char_y = thumb_height - character_layer.height + 30

        # 生成背景の明るさに依存せずキャラクターと見出しのコントラストを保つため、
        # キャラクター背後にも柔らかい楕円形の暗めスクリムを重ねる。ハードエッジな
        # 図形に見えないよう、中心alpha≒90から端に向けてフェードさせ、境界を
        # GaussianBlurでぼかす
        char_scrim_mask = Image.new("L", (thumb_width, thumb_height), 0)
        ellipse_cx = char_x + character_layer.width // 2
        ellipse_cy = char_y + round(character_layer.height * 0.6)
        ellipse_rx = round(character_layer.width * 0.75)
        ellipse_ry = round(character_layer.height * 0.7)
        ImageDraw.Draw(char_scrim_mask).ellipse(
            (
                ellipse_cx - ellipse_rx,
                ellipse_cy - ellipse_ry,
                ellipse_cx + ellipse_rx,
                ellipse_cy + ellipse_ry,
            ),
            fill=90,
        )
        char_scrim_mask = char_scrim_mask.filter(ImageFilter.GaussianBlur(60))
        char_scrim = Image.new("RGBA", (thumb_width, thumb_height), (0, 0, 0, 255))
        char_scrim.putalpha(char_scrim_mask)
        image = Image.alpha_composite(image, char_scrim)

        _paste_with_drop_shadow(image, character_layer, (char_x, char_y), offset=(10, 12), blur=8)

    # 左上の赤バッジ(シリーズ認知用)。メインと文言が被る場合は省く
    draw = ImageDraw.Draw(image)
    if badge_visible:
        badge_font = _load_font(32, bold=True)
        bb = badge_font.getbbox(badge_text)
        bpad_x, bpad_y = 20, 10
        bx1, by1 = 34, 26
        bx2 = bx1 + (bb[2] - bb[0]) + bpad_x * 2
        by2 = by1 + (bb[3] - bb[1]) + bpad_y * 2
        draw.rounded_rectangle((bx1, by1, bx2, by2), radius=8, fill="#dc2626")
        draw.text(
            (bx1 + bpad_x - bb[0], by1 + bpad_y - bb[1]),
            badge_text,
            font=badge_font,
            fill="#ffffff",
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path)


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
    if spec.kind in {"opening", "ranking", "song"}:
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
    if spec.kind == "segment":
        # segmentは解説中はキャラ非表示だが、直後の感想パートでは同じ画面に
        # ずんだもんが現れる(_render_reaction_variant)。reaction_lineがある限り
        # レイアウトは常にキャラ分の余白を空けておく
        if spec.reaction_line and settings.CHARACTER_OVERLAY_ENABLED and settings.CHARACTER_OVERLAY_NAME:
            return 260
        return 0
    if _character_image(spec) is None:
        return 0
    if spec.kind in {"hook", "opening", "ranking", "song"}:
        return 420
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


def _render_divider_slide(spec: SlideSpec, path: Path, compact: bool = False) -> None:
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

    y = _draw_fitted(
        draw, (210, 540), spec.title, _compact_base_size(64, compact), 38, "#ffffff", 1500, 18,
        max_lines=_compact_max_lines(3, compact), bold=True,
    )

    chip_label_w = _text_width(chip_font, style.label)
    chip_w = 16 + 26 + 10 + chip_label_w + 16
    _draw_category_chip(draw, int((WIDTH + chip_w) / 2), y + 40, style, chip_font)

    path.parent.mkdir(parents=True, exist_ok=True)
    image = _paste_character_overlay(image.convert("RGBA"), spec)
    image.convert("RGB").save(path)


def _render_illustration_slide(
    spec: SlideSpec, path: Path, compact: bool = False, overlay_only: bool = False
) -> None:
    """ニュースごとのAI解説イラストスライド(区切りを兼ねる)。
    全画面イラスト(なければダーク背景) + 左下に巨大#N・カテゴリチップ・日本語タイトル。

    overlay_only=Trueの場合は背景を完全透明にし、スクリム・テキスト・キャラクター
    だけを描いたRGBA PNGを書き出す(Veo製背景クリップの上にffmpegで重ねる用)。"""
    style = category_style(spec.category)
    if overlay_only:
        image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    elif spec.image is not None:
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
    # 字幕は2行になるとy≈750から表示されるため、この一群はそれより十分上に収める
    draw.text(
        (140, 420), number_text, font=number_font, fill=style.color,
        stroke_width=4, stroke_fill="#0b1120",
    )
    number_w = _text_width(number_font, number_text)
    chip_label_w = _text_width(chip_font, style.label)
    chip_w = 16 + 26 + 10 + chip_label_w + 16
    _draw_category_chip(draw, 140 + number_w + 40 + chip_w, 470, style, chip_font)

    # 日本語タイトル(最大2行)。字幕領域(2行時 y≈750〜)にかからない位置に置く
    _draw_fitted(
        draw, (140, 560), label, _compact_base_size(60, compact), 34, "#ffffff", WIDTH - 280, 12,
        max_lines=_compact_max_lines(2, compact), bold=True,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    image = _paste_character_overlay(image, spec)
    if overlay_only:
        image.save(path)  # 背景クリップに重ねるため透明部分(RGBA)を保持する
    else:
        image.convert("RGB").save(path)


def _render_song_slide(spec: SlideSpec, path: Path, compact: bool = False) -> None:
    """ずんだもんニュースソングのコーナー。MV映像を主役にするため、スライドには
    背景だけを描く(タイトル・歌詞リスト・キャラクターの後乗せはしない)。
    歌詞はフレーズごとの焼き込み字幕(SRT)として画面下に表示される。

    このPNGはVeo製の動くMV背景(slide.clip)が使えない場合の静止画フォールバック。"""
    if spec.image is not None:
        image = _cover_crop(spec.image, WIDTH, HEIGHT)
    else:
        image = _draw_dark_background()
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _draw_opening_content(
    draw: ImageDraw.ImageDraw,
    spec: SlideSpec,
    compact: bool,
    content_width: int,
) -> None:
    title_font = _load_font(_compact_base_size(68, compact), bold=True)
    _draw_wrapped(
        draw, (140, 180), spec.title, title_font, _DARK_TITLE, content_width, 18,
        max_lines=_compact_max_lines(1, compact),
    )
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


def _render_opening_overlay(spec: SlideSpec, path: Path, compact: bool = False) -> None:
    """Veo製オープニング背景に重ねる透明オーバーレイ。"""
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    character_reserve = _character_reserve_width(spec)
    content_width = WIDTH - 280 - character_reserve

    # 動く背景の上でも文字を読ませるため、左側を中心に薄いスクリムを敷く。
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(5, 10, 24, 90))
    for x in range(0, WIDTH):
        alpha = max(0, int(110 * (1 - x / WIDTH)))
        draw.line(((x, 120), (x, HEIGHT)), fill=(0, 0, 0, alpha))
    _draw_header(draw, spec)
    draw.rectangle((80, 180, 96, 810), fill="#2563eb")
    _draw_opening_content(draw, spec, compact, content_width)

    path.parent.mkdir(parents=True, exist_ok=True)
    image = _paste_character_overlay(image, spec)
    image.save(path)


def _render_slide(spec: SlideSpec, path: Path, compact: bool = False) -> None:
    """スライドを1枚描画してpathに保存する。

    compact=Trueはレビュー(video_review)がtext_overflow/overlapを検出した際の
    決定的な再レンダリング用フラグ。ベースフォントサイズを約15%縮小し、
    折り返し行数を1行増やして許容する。
    """
    if spec.kind == "divider":
        _render_divider_slide(spec, path, compact=compact)
        return

    if spec.kind == "illustration":
        _render_illustration_slide(spec, path, compact=compact)
        return

    if spec.kind == "song":
        _render_song_slide(spec, path, compact=compact)
        return

    # 全スライド共通のダーク背景(生成背景は使わない)
    image = _draw_dark_background()
    draw = ImageDraw.Draw(image)
    character_reserve = _character_reserve_width(spec)
    content_width = WIDTH - 280 - character_reserve

    title_font = _load_font(_compact_base_size(68, compact), bold=True)
    body_font = _load_font(_compact_base_size(40, compact))
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
        hook_body_font = _load_font(_compact_base_size(64, compact), bold=True)
        draw.text((140, 200), "今週の注目ニュース", font=hook_label_font, fill="#f87171")
        _draw_wrapped(
            draw, (140, 320), spec.body, hook_body_font, _DARK_TITLE,
            content_width, 24, max_lines=_compact_max_lines(3, compact),
        )
    elif spec.kind == "opening":
        # 5〜20秒: 価値提示 + ラインナップ一覧
        _draw_opening_content(draw, spec, compact, content_width)
    elif spec.kind == "ranking":
        _draw_wrapped(
            draw, (140, 180), spec.title, title_font, _DARK_TITLE, content_width, 18,
            max_lines=_compact_max_lines(1, compact),
        )
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
            draw, (140, 180), spec.title, _compact_base_size(64, compact), 48, _DARK_TITLE,
            content_width, 18, max_lines=_compact_max_lines(2, compact), bold=True,
        )
        benefit_y = max(330, y_after_title + 6)
        y_after_benefit = _draw_fitted(
            draw, (140, benefit_y), spec.body, _compact_base_size(34, compact), 26, "#fbbf24",
            content_width, 8, max_lines=1, bold=True,
        )
        # 元の見出し(英語など)は事実の原典として小さく併記する。補助情報なので、
        # min_sizeまで縮小してもなお収まらない場合に限り「...」省略を許容する
        if spec.headline and spec.headline not in spec.title:
            _draw_fitted(
                draw, (140, max(395, y_after_benefit + 10)), spec.headline,
                _compact_base_size(24, compact), 20, _DARK_FAINT, content_width, 0, max_lines=2,
            )

        if spec.visual:
            _render_visual_panel(draw, spec.visual, accent)

        # 箇条書き(旧Impact/Actionボックスの代替)。図解の有無で開始yを変える。
        # 図解ありは残り高さが少ない(665〜820)ため max_lines=1 で先に縮小させ、
        # バジェット超過の長文だけが最小サイズの折り返しになるようにする
        bullet_label_font = _load_font(26, bold=True)
        bullet_max_lines = _compact_max_lines(1 if spec.visual else 2, compact)
        y = 665 if spec.visual else 500
        bullets = (("影響", spec.impact), ("注目ポイント", spec.action))
        for bullet_label, bullet_body in bullets:
            # アクセント色の正方形ビュレット + ラベル + 同じ行から始まる本文
            draw.rectangle((140, y + 11, 152, y + 23), fill=accent)
            draw.text((164, y + 2), bullet_label, font=bullet_label_font, fill="#93c5fd")
            body_x = 164 + _text_width(bullet_label_font, bullet_label) + 28
            y = _draw_fitted(
                draw, (body_x, y), bullet_body, _compact_base_size(30, compact), 22, _DARK_BODY,
                max(520, WIDTH - 200 - character_reserve - body_x), 8, max_lines=bullet_max_lines,
            )
            y += 40
    else:
        _draw_wrapped(
            draw, (140, 180), spec.title, title_font, _DARK_TITLE, content_width, 18,
            max_lines=_compact_max_lines(4, compact),
        )
        _draw_wrapped(
            draw, (140, 490), spec.body, body_font, _DARK_BODY, content_width, 18,
            max_lines=_compact_max_lines(4, compact),
        )

    # Keep everything above y=860; the area below is reserved for burned-in subtitles
    draw.rectangle((80, 830, WIDTH - 80, 833), fill=_DARK_LINE)
    if spec.kind == "segment" and spec.source:
        draw.text((80, 845), f"出典: {spec.source}", font=meta_font, fill=_DARK_FAINT)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = _paste_character_overlay(image, spec)
    image.convert("RGB").save(path)


def _render_reaction_variant(spec: SlideSpec, base_path: Path, reaction_path: Path) -> None:
    """感想パート用のフレームを書き出す。レイアウトは解説フレームと同一のまま、
    ずんだもんの立ち絵だけを追加する(narrator="expert"のためbase_pathには映っていない)。"""
    image = Image.open(base_path).convert("RGBA")
    image = _paste_character_overlay(image, replace(spec, narrator="zundamon"))
    reaction_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(reaction_path)


def _build_frame_timeline(
    frames: list[tuple[Path, float]], total_duration: float, min_duration: float = 0.1
) -> list[tuple[Path, float]]:
    """(画像パス, 表示開始時刻)のリストを、表示尺が min_duration 未満にならない
    よう間引いたうえで [(画像パス, 表示尺)] に変換する。

    reactionフレーム(expert_duration時点でキャラ出現)も段階表示フレームも、
    このヘルパーを通して最終的なタイムラインに変換する共通の仕組みとする。
    """
    cleaned: list[tuple[Path, float]] = []
    for path, start in frames:
        start = min(max(start, 0.0), total_duration)
        if cleaned and start - cleaned[-1][1] < min_duration:
            continue  # 直前のフレームと十分な間隔が無い場合はこのフレームを省く
        cleaned.append((path, start))
    while len(cleaned) > 1 and total_duration - cleaned[-1][1] < min_duration:
        cleaned.pop()

    result: list[tuple[Path, float]] = []
    for i, (path, start) in enumerate(cleaned):
        end = cleaned[i + 1][1] if i + 1 < len(cleaned) else total_duration
        result.append((path, end - start))
    return result


def _build_multi_frame_filter(frame_durations: list[float]) -> str:
    """各フレームの表示尺のリストから、静止画入力([0:v], [1:v], ...)を
    trim + concat でつなぎ [vcat] にまとめる filter_complex 文字列を組み立てる。

    入力ストリームは len(frame_durations) 個の -loop 1 画像入力が
    [0:v] から順に渡されている前提。
    """
    parts = [
        f"[{i}:v]trim=duration={dur:.3f},setpts=PTS-STARTPTS[v{i}]"
        for i, dur in enumerate(frame_durations)
    ]
    concat_inputs = "".join(f"[v{i}]" for i in range(len(frame_durations)))
    parts.append(f"{concat_inputs}concat=n={len(frame_durations)}:v=1:a=0[vcat]")
    return ";".join(parts)


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


def _select_bgm_file() -> Path | None:
    """BGM_FILE指定があればそのファイルを、未指定ならbgm/配下の音声ファイルを
    ソートして先頭の1つを使う。見つからない場合はNone(BGMなしで続行)。"""
    if settings.BGM_FILE:
        candidate = BGM_ASSETS_DIR / settings.BGM_FILE
        return candidate if candidate.exists() else None
    if not BGM_ASSETS_DIR.exists():
        return None
    files = sorted(
        (
            path
            for path in BGM_ASSETS_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in (".mp3", ".wav", ".m4a")
        ),
        key=lambda path: path.name,
    )
    return files[0] if files else None


def _mix_bgm(
    work_dir: Path,
    video_name: str,
    total_duration: float,
    mute_ranges: list[tuple[float, float]] | None = None,
) -> None:
    """concat直後の動画にナレーションより十分低い音量でBGMを重ねる。

    BGM無効・ファイル未検出・ffmpeg失敗時は例外にせずBGMなしのまま続行する。
    最終ラウドネス正規化(_normalize_final_loudness)より前に呼ぶことで、
    BGM込みの音声に対して目標ラウドネスが適用されるようにする。

    mute_ranges を渡すと、その[開始, 終了)秒区間だけBGM音量を0にする
    (例: ずんだもんニュースソングのコーナーではBGMを止めて歌に集中させる)。
    """
    if not settings.BGM_ENABLED:
        return
    bgm_path = _select_bgm_file()
    if bgm_path is None:
        return
    try:
        fade_start = max(total_duration - 2.0, 0.0)
        bgm_filters = [f"volume={settings.BGM_VOLUME_DB:g}dB"]
        for start, end in mute_ranges or []:
            bgm_filters.append(f"volume=0:enable='between(t,{start:.3f},{end:.3f})'")
        bgm_filters.append(f"afade=t=out:st={fade_start:.3f}:d=2")
        filter_complex = (
            f"[1:a]{','.join(bgm_filters)}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        mixed_name = "video_bgm.mp4"
        _run_ffmpeg(
            [
                "-i",
                video_name,
                "-stream_loop",
                "-1",
                "-i",
                str(bgm_path.resolve()),
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-shortest",
                mixed_name,
            ],
            cwd=work_dir,
        )
        (work_dir / mixed_name).replace(work_dir / video_name)
    except Exception:
        return


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
# 禁則: 行頭に来てはいけない文字(閉じ括弧・句読点・終端記号・小書き文字/長音)
_KINSOKU_LINE_START_FORBIDDEN = "」』）)]、。，．！？!?…ーゃゅょぁぃぅぇぉっャュョァィゥェォッ"
# 禁則: 行末に来てはいけない文字(開き括弧)
_KINSOKU_LINE_END_FORBIDDEN = "「『([（"
# カタカナの連なり(「セキュリティ」等の外来語)の内部分割を避けるための判定。
# 長音符ーは行頭禁則で既に弾かれるが、連なり判定にも含めて語の一体性を優先する
_KATAKANA_RE = re.compile(r"[ァ-ヶー]")


def _is_safe_subtitle_split(text: str, i: int) -> bool:
    """位置i(前半=text[:i]、後半=text[i:])での分割が禁則に触れないか判定する。

    英数字の連なり(モデル名・バージョン番号など、例: "GovCloud" "2.5" "GPT-4o")の
    内部、後半が閉じ括弧・句読点・小書き文字等で始まる行頭禁則、前半が開き括弧で
    終わる行末禁則のいずれにも該当しなければ安全とみなす。
    """
    if i <= 0 or i >= len(text):
        return False
    prev_char, next_char = text[i - 1], text[i]
    if _ASCII_RE.match(prev_char) and _ASCII_RE.match(next_char):
        return False
    if next_char in _KINSOKU_LINE_START_FORBIDDEN:
        return False
    if prev_char in _KINSOKU_LINE_END_FORBIDDEN:
        return False
    return True


def _find_safe_split(text: str, ideal: int, lo: int, hi: int) -> int | None:
    """[lo, hi]の範囲内で禁則を満たす分割位置のうちidealに最も近いものを返す。

    句読点・終端記号・空白の直後(優先度1)を最優先し、次にカタカナ語の内部を
    避けた文字間(優先度2)、それも無ければ禁則さえ満たせば良い任意の文字間
    (優先度3)を採用する。_split_subtitle_cuesと_wrap_cue_linesの両方から使う
    共通ヘルパー。範囲内に安全な位置が無ければNoneを返し、呼び出し側で
    強制分割にフォールバックさせる。
    """
    lo = max(lo, 1)
    hi = min(hi, len(text) - 1)
    if lo > hi:
        return None

    def best(prefer_break: bool, avoid_katakana_run: bool) -> int | None:
        found = None
        for i in range(lo, hi + 1):
            if prefer_break and text[i - 1] not in _SUBTITLE_BREAK_CHARS:
                continue
            if avoid_katakana_run and _KATAKANA_RE.match(text[i - 1]) and _KATAKANA_RE.match(text[i]):
                continue
            if not _is_safe_subtitle_split(text, i):
                continue
            if found is None or abs(i - ideal) < abs(found - ideal):
                found = i
        return found

    for prefer_break, avoid_katakana_run in ((True, False), (False, True), (False, False)):
        found = best(prefer_break, avoid_katakana_run)
        if found is not None:
            return found
    return None


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
        # 禁則を守れる位置をmax_cue_chars近傍(+6文字まで延長可)で探し、
        # 見つからない場合のみ現状どおりの強制分割にフォールバックする
        split_at = _find_safe_split(
            remaining, ideal=max_cue_chars, lo=max_cue_chars // 2, hi=max_cue_chars + 6
        )
        if split_at is None:
            split_at = max_cue_chars
        cues.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        cues.append(remaining)

    # 末尾が「です。」のような数文字だけの孤立キューになった場合は、2行に
    # 収まる範囲(max_line_chars+6を2行分)で直前のキューへ併合する
    if (
        len(cues) >= 2
        and len(cues[-1]) < 6
        and len(cues[-2]) + len(cues[-1]) + 1 <= (max_line_chars + 6) * max_lines
    ):
        tail = cues.pop()
        joiner = " " if _ASCII_RE.match(cues[-1][-1]) and _ASCII_RE.match(tail[0]) else ""
        cues[-1] = cues[-1] + joiner + tail

    total_chars = sum(len(cue) for cue in cues) or 1
    return [(cue, duration * len(cue) / total_chars) for cue in cues]


def _wrap_cue_lines(text: str, max_line_chars: int) -> list[str]:
    # textwrap は英単語・ハイフン優先で折って3行以上になり得るため、2行保証の自前分割を使う
    if len(text) <= max_line_chars:
        return [text]

    max_extend = 6
    # 1行目・2行目のどちらも max_line_chars+max_extend に収まる範囲でのみ
    # 分割位置を探す(2行保証を維持したまま禁則を優先するため)。
    # idealは中央付近にして2行の長さを揃え、「〜されまし / た。」のような
    # 数文字だけの2行目を避ける
    lo = max(1, len(text) - (max_line_chars + max_extend))
    hi = min(len(text) - 1, max_line_chars + max_extend)
    ideal = min(max((len(text) + 1) // 2, lo), hi)
    split_at = _find_safe_split(text, ideal=ideal, lo=lo, hi=hi)
    if split_at is None:
        # 強制分割でも2行がそれぞれ max_line_chars+max_extend に収まるよう
        # 中央寄りの位置で切る
        split_at = ideal
    first = text[:split_at].rstrip()
    second = text[split_at:].strip()
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


def _save_segment_clip_assets(
    segment_clips: dict[int, Path], assets_dir: Path
) -> dict[int, Path]:
    """キャッシュ上のVeoクリップを成果物ディレクトリへコピーし、コピー先のパスを返す。

    work_dir配下に置くことで成果物が自己完結し、リテイク時もキャッシュの掃除に
    影響されない。コピーに失敗したセグメントは辞書から外す(=静止画フォールバック)。"""
    saved: dict[int, Path] = {}
    if not segment_clips:
        return saved
    clips_dir = assets_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    for number, clip in segment_clips.items():
        dest = clips_dir / f"segment_{number:02}.mp4"
        try:
            shutil.copyfile(clip, dest)
        except OSError:
            logger.exception("failed to copy segment clip: %s", clip)
            continue
        saved[number] = dest
    return saved


def _display_label(segment: VideoSegment) -> str:
    """スライドに出す表示ラベル。

    title_ja が使える場合は長くても採用し、使えない場合も元見出しを省略せずに使う。
    収まりは描画側の折り返し・フォント縮小で調整する。
    """
    title_ja = segment.title_ja
    if title_ja and contains_japanese(title_ja):
        return title_ja
    return segment.headline.strip()


def _video_news_contexts(draft: VideoPlanDraft) -> list[str]:
    contexts: list[str] = []
    for segment in draft.segments:
        parts = [
            f"#{segment.number}",
            f"category={segment.category}" if segment.category else "",
            _display_label(segment),
            segment.summary,
            f"impact: {segment.impact}" if segment.impact else "",
            f"action: {segment.action}" if segment.action else "",
        ]
        context = " ".join(" ".join(part.split()) for part in parts if part)
        if context:
            contexts.append(context[:360])
    return contexts


def _build_slides(
    draft: VideoPlanDraft,
    segment_images: dict[int, Image.Image] | None = None,
    song_lyrics: list[str] | None = None,
    song_bg: Image.Image | None = None,
    segment_clips: dict[int, Path] | None = None,
    song_clip: Path | None = None,
    opening_clip: Path | None = None,
) -> list[SlideSpec]:
    segment_images = segment_images or {}
    segment_clips = segment_clips or {}
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
    # 冒頭はフック(〜5秒) + ずんだもんニュースソング(あれば) + オープニング(〜15秒)の構成。
    # 歌はフックの直後・オープニングの前に挿入する(フックがなければ歌が先頭になる)。
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
    if song_lyrics:
        slides.append(
            SlideSpec(
                kind="song",
                title="ずんだもんニュースソング",
                body="",
                narration="",
                week_label=draft.week_label,
                narrator="zundamon",
                lyrics=song_lyrics,
                image=song_bg,
                clip=song_clip,
            )
        )
    slides.append(
        SlideSpec(
            kind="opening",
            title="今週のAIニュースラインナップ",
            body=f"重要ニュース{len(draft.segments)}本を短時間でキャッチアップ",
            narration=draft.intro,
            entries=entries,
            clip=opening_clip,
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
                clip=segment_clips.get(segment.number),
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


@dataclass
class PartBuildResult:
    """_build_part 1回分の結果。generate_video_from_draft側の集計(padded_durations等)と、
    レビューによるリテイク時の再利用(reuse_audio)に必要な情報を両方保持する。"""

    slide: SlideSpec
    part_number: int
    chunk_durations: list[tuple[str, float, str]]
    part_duration: float
    expert_duration: float
    fade_duration: float
    voice_wav_params: tuple[int, int, int] | None


async def _build_part(
    slide: SlideSpec,
    part_number: int,
    work_dir: Path,
    *,
    reading_map: dict[str, str] | None = None,
    voice_wav_params: tuple[int, int, int] | None = None,
    font_name: str | None = None,
    compact: bool = False,
    reuse_audio: bool = False,
    reuse: PartBuildResult | None = None,
    song_entries: list[tuple[str, float]] | None = None,
) -> PartBuildResult:
    """1パート分(スライド1枚)の音声合成・スライド描画・ffmpegエンコードを行い、
    work_dir/parts/part_NNN.mp4 を書き出す。

    reuse_audio=Trueの場合(video_reviewからのリテイク)は音声合成を一切行わず、
    既存の audio/audio_NNN.wav と reuse(直前ビルドのPartBuildResult)が持つ
    chunk_durations/part_duration/expert_duration/fade_durationをそのまま使い、
    スライドPNGの再描画(compactを反映)とffmpegでのパート再エンコードだけを行う。
    これによりリテイクは純粋に「見た目」の修正であり、音声・尺・SRTのタイミングは
    一切変えない。
    """
    slides_dir = work_dir / "slides"
    audio_dir = work_dir / "audio"
    index = part_number
    font_name = font_name or _subtitle_font()

    slide_path = slides_dir / f"slide_{index:03}.png"
    audio_path = audio_dir / f"audio_{index:03}.wav"
    part_srt_rel = f"parts/part_{index:03}.srt"
    part_srt_path = work_dir / part_srt_rel

    _render_slide(slide, slide_path, compact=compact)

    # Veo製背景クリップ付きスライド(illustration / opening / songのMV背景)は、テキスト・
    # スクリム・キャラだけの透明オーバーレイPNGを別途描き、ffmpegでクリップの上に
    # 重ねる。クリップが無い(生成失敗・無効化)場合は従来どおり静止画スライドを使う
    use_clip = (
        slide.kind in ("illustration", "song", "opening")
        and slide.clip is not None
        and slide.clip.exists()
    )
    # 歌はMV映像をそのまま見せる(歌詞は焼き込み字幕のみ)ためオーバーレイ不要。
    # illustration/openingはテキスト・スクリム・キャラの透明オーバーレイを重ねる
    overlay_path = slides_dir / f"slide_{index:03}_overlay.png"
    if use_clip and slide.kind == "illustration":
        _render_illustration_slide(slide, overlay_path, compact=compact, overlay_only=True)
    elif use_clip and slide.kind == "opening":
        _render_opening_overlay(slide, overlay_path, compact=compact)

    # segmentは解説中はキャラ非表示だが、直後の感想パートでは同じ画面のまま
    # ずんだもんが現れる2枚目のフレームを用意し、その切り替わりで表現する
    show_reaction_character = bool(
        slide.kind == "segment"
        and slide.reaction_line
        and settings.CHARACTER_OVERLAY_ENABLED
        and settings.CHARACTER_OVERLAY_NAME
    )
    reaction_slide_path = slides_dir / f"slide_{index:03}_reaction.png"
    if show_reaction_character:
        _render_reaction_variant(slide, slide_path, reaction_slide_path)

    if reuse_audio:
        if reuse is None:
            raise ValueError("reuse_audio=True には reuse(直前のPartBuildResult)が必要です")
        chunk_durations = reuse.chunk_durations
        part_duration = reuse.part_duration
        expert_duration = reuse.expert_duration
        fade_duration = reuse.fade_duration
    else:
        expert_duration = 0.0
        if slide.kind == "divider":
            # 区切りは無音・固定尺・字幕なし。WAVパラメータはVOICEVOX出力に揃える
            params = voice_wav_params or (1, 2, 24000)
            _write_silent_wav(audio_path, DIVIDER_DURATION, *params)
            chunk_durations = []
            part_duration = DIVIDER_DURATION
            fade_duration = 0.3
        elif slide.kind == "song":
            # 歌唱音声はループ冒頭で既に合成済み(probe_path)。パート番号確定後の
            # 正式なaudio_pathへ移し、通常パートと同じくSRT・フェード・loudnormを適用する
            probe_path = audio_dir / "song_probe.wav"
            probe_path.replace(audio_path)
            chunk_durations = [(text, dur, "zundamon") for text, dur in (song_entries or [])]
            if voice_wav_params is None:
                voice_wav_params = _wav_params(audio_path)
            audio_duration = max(sum(dur for _, dur, _ in chunk_durations), 1.0)
            part_duration = audio_duration + 0.4
            fade_duration = 0.4
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
            expert_duration = sum(dur for _, dur in primary_durations)
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

    # 表示フレーム(画像パス, 表示開始時刻)のリストを組み立てる。
    # segmentの箇条書きは最初から全表示し、必要な場合だけリアクション用フレームを末尾に追加する。
    # クリップ使用時のillustration/openingは静止スライドの代わりに透明オーバーレイをフレームにする
    frames: list[tuple[Path, float]] = [
        (
            overlay_path
            if use_clip and slide.kind in ("illustration", "opening")
            else slide_path,
            0.0,
        )
    ]
    if show_reaction_character:
        frames.append((reaction_slide_path, expert_duration))

    frame_specs = _build_frame_timeline(frames, part_duration)

    if use_clip:
        # 背景クリップを画面いっぱいに整える。openingはVeo拡張済みなのでループせず、
        # illustration/songは短尺クリップをループする。illustration/openingは
        # 透明オーバーレイ(テキスト・スクリム・キャラ)を重ね、songはMV映像を
        # そのまま見せる(歌詞は焼き込み字幕)。字幕・フェード等(vf)は最後に適用する
        overlay_specs = frame_specs if slide.kind in ("illustration", "opening") else []
        bg_filter = (
            f"[0:v]tpad=stop_mode=clone:stop_duration={part_duration:.3f},"
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT}[bg]"
            if slide.kind == "opening"
            else f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT}[bg]"
        )
        filter_parts = [
            bg_filter
        ]
        args = []
        if slide.kind != "opening":
            args += ["-stream_loop", "-1"]
        args += ["-i", str(slide.clip)]
        prev_label = "bg"
        for i, (frame_path, _duration) in enumerate(overlay_specs, start=1):
            args += ["-loop", "1", "-i", f"slides/{frame_path.name}"]
            label = f"ov{i}"
            filter_parts.append(f"[{prev_label}][{i}:v]overlay=0:0[{label}]")
            prev_label = label
        filter_parts.append(f"[{prev_label}]{vf}[vout]")
        args += [
            "-i",
            f"audio/audio_{index:03}.wav",
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            f"{len(overlay_specs) + 1}:a",
        ]
    elif len(frame_specs) > 1:
        filter_complex = _build_multi_frame_filter([duration for _, duration in frame_specs])
        filter_complex += f";[vcat]{vf}[vout]"
        args = []
        for frame_path, _ in frame_specs:
            args += ["-loop", "1", "-i", f"slides/{frame_path.name}"]
        args += [
            "-i",
            f"audio/audio_{index:03}.wav",
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            f"{len(frame_specs)}:a",
        ]
    else:
        args = [
            "-loop",
            "1",
            "-i",
            f"slides/{frame_specs[0][0].name}",
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

    return PartBuildResult(
        slide=slide,
        part_number=part_number,
        chunk_durations=chunk_durations,
        part_duration=part_duration,
        expert_duration=expert_duration,
        fade_duration=fade_duration,
        voice_wav_params=voice_wav_params,
    )


def _assemble_final(
    work_dir: Path,
    final_slides: list[SlideSpec],
    padded_durations: list[float],
) -> list[float]:
    """parts/*.mp4 からconcat→BGM重ね→ラウドネス正規化までを行い、動画全体を
    work_dir/video.mp4 として書き出す。スライドのoffsetのリスト(SRT・チャプター用)を返す。

    毎回 parts/*.mp4 から作り直す(concatの出力・中間ファイルは全てffmpegの -y で
    都度上書き)ため、リテイク後にこの関数を再実行しても古い中間ファイルの内容が
    混入することはない。
    """
    concat_file = work_dir / "concat.txt"
    concat_file.write_text(
        "".join(f"file 'parts/part_{i:03}.mp4'\n" for i in range(1, len(final_slides) + 1)),
        encoding="utf-8",
    )
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

    # ずんだもんニュースソングのコーナー中はBGMを止め、歌に集中させる
    song_mute_ranges: list[tuple[float, float]] = [
        (offset, offset + dur)
        for slide, offset, dur in zip(final_slides, slide_offsets, padded_durations)
        if slide.kind == "song"
    ]

    # ナレーション音声に低音量BGMを重ねる(ラウドネス正規化の前に行う)
    _mix_bgm(
        work_dir, "video.mp4", sum(padded_durations), mute_ranges=song_mute_ranges or None
    )

    # YouTube向けラウドネス(-16 LUFS / TP -1.5 dBTP)を動画全体で保証する
    _normalize_final_loudness(work_dir, "video.mp4")

    return slide_offsets


async def generate_video_from_draft(draft: VideoPlanDraft) -> VideoArtifact:
    video_id = _now_id()
    work_dir = GENERATED_DIR / video_id
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
    news_contexts = _video_news_contexts(draft)

    # イラストをVeoのimage-to-videoで動くクリップにする(未設定・失敗した
    # セグメントは辞書に含まれず、静止イラストのままになる)
    segment_clips = await generate_segment_clips(segment_images, draft.segments)
    segment_clips = _save_segment_clip_assets(segment_clips, work_dir / "assets")

    if theme.thumbnail_bg is None:
        raise ThumbnailGenerationError(
            "サムネイル背景画像の生成に失敗しました。IMAGE_GEN_ENABLED と GEMINI_PROJECT、"
            "画像生成モデルの権限・クォータを確認してください。"
        )
    _render_thumbnail(draft, work_dir / "thumbnail.png", background=theme.thumbnail_bg)

    # ずんだもんニュースソング(週次まとめの歌唱コーナー)。VOICEVOXが歌唱合成に
    # 対応していない・歌詞生成/合成に失敗した場合は静かにスキップし、通常の
    # 動画生成を止めない。
    song_lyrics: list[str] | None = None
    song_bg: Image.Image | None = None
    if settings.SONG_ENABLED:
        try:
            if await check_song_support():
                song_lyrics = await generate_song_lyrics(draft)
                (work_dir / "song_lyrics.json").write_text(
                    json.dumps({"phrases": song_lyrics}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            logger.exception("song lyrics generation failed; skipping song corner")
            song_lyrics = None

        if song_lyrics:
            try:
                song_bg = await generate_song_background(draft, song_lyrics)
            except Exception:
                logger.exception("song background generation failed; using dark fallback")
                song_bg = None

    # MV背景をVeoで動くクリップにする(失敗・無効時はNoneで静止画のまま)
    song_clip: Path | None = None
    if song_bg is not None:
        song_clip = await generate_song_clip(song_bg, song_lyrics, news_contexts)
        if song_clip is not None:
            clips_dir = work_dir / "assets" / "clips"
            clips_dir.mkdir(parents=True, exist_ok=True)
            dest = clips_dir / "song.mp4"
            try:
                shutil.copyfile(song_clip, dest)
                song_clip = dest
            except OSError:
                logger.exception("failed to copy song clip: %s", song_clip)
                song_clip = None

    # オープニング背景はVeo拡張で長尺化し、ffmpeg側ではループさせない。
    # 文字・キャラは後段で透明オーバーレイとして重ねる。
    opening_clip: Path | None = None
    opening_clip = await generate_opening_clip(
        draft.week_label,
        [_display_label(segment) for segment in draft.segments],
        news_contexts,
    )
    if opening_clip is not None:
        clips_dir = work_dir / "assets" / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        dest = clips_dir / "opening.mp4"
        try:
            shutil.copyfile(opening_clip, dest)
            opening_clip = dest
        except OSError:
            logger.exception("failed to copy opening clip: %s", opening_clip)
            opening_clip = None

    slides = _build_slides(
        draft,
        segment_images,
        song_lyrics=song_lyrics,
        song_bg=song_bg,
        segment_clips=segment_clips,
        song_clip=song_clip,
        opening_clip=opening_clip,
    )
    reading_map = await build_reading_map(
        [slide.narration for slide in slides if slide.narration]
        + [slide.reaction_line for slide in slides if slide.reaction_line]
    )
    padded_durations: list[float] = []
    all_chunk_durations: list[list[tuple[str, float, str]]] = []
    final_slides: list[SlideSpec] = []
    part_records: list[PartBuildResult] = []
    font_name = _subtitle_font()
    voice_wav_params: tuple[int, int, int] | None = None

    part_number = 0
    for slide in slides:
        song_entries: list[tuple[str, float]] | None = None
        if slide.kind == "song":
            # 歌唱合成はVOICEVOX呼び出しを伴い失敗しうる。失敗時はこのスライド
            # 丸ごとをスキップし、パート番号・concatリストの連番は乱れないようにする
            # (part_numberをまだ進めていないので、後続スライドの番号もずれない)。
            probe_path = audio_dir / "song_probe.wav"
            try:
                song_entries = await synthesize_song(slide.lyrics, probe_path)
            except Exception:
                logger.exception("song synthesis failed; skipping song corner")
                continue

        part_number += 1
        result = await _build_part(
            slide,
            part_number,
            work_dir,
            reading_map=reading_map,
            voice_wav_params=voice_wav_params,
            font_name=font_name,
            song_entries=song_entries,
        )
        voice_wav_params = result.voice_wav_params
        padded_durations.append(result.part_duration)
        all_chunk_durations.append(result.chunk_durations)
        final_slides.append(slide)
        part_records.append(result)

    slide_offsets = _assemble_final(work_dir, final_slides, padded_durations)

    # --- 自己レビュー&自動リテイク(Feature C) ---
    # 完成した各パートをGeminiのマルチモーダルにチェックさせ、はみ出し・重なり等の
    # 明確な不具合を自動リテイクする。retake_part/assembleはvideo_reviewとの
    # 循環importを避けるためのクロージャコールバック。レビューは失敗しても
    # 動画生成自体を絶対に止めない(status="skipped"に倒す)。
    async def retake_part(part_index: int, *, compact: bool, drop_image: bool) -> None:
        record = part_records[part_index - 1]
        slide_for_retake = record.slide
        if drop_image:
            # illustrationスライドの生成イラスト・背景クリップを外し、
            # ダーク背景フォールバックにする
            slide_for_retake = replace(slide_for_retake, image=None, clip=None)
        updated = await _build_part(
            slide_for_retake,
            record.part_number,
            work_dir,
            reading_map=reading_map,
            voice_wav_params=voice_wav_params,
            font_name=font_name,
            compact=compact,
            reuse_audio=True,
            reuse=record,
        )
        part_records[part_index - 1] = updated
        final_slides[part_index - 1] = slide_for_retake

    async def assemble() -> None:
        _assemble_final(work_dir, final_slides, padded_durations)

    review_report = ReviewReport(status="skipped")
    try:
        if settings.REVIEW_ENABLED and settings.GEMINI_PROJECT:
            part_contexts = [
                {
                    "part": record.part_number,
                    "kind": record.slide.kind,
                    "title": record.slide.title,
                    "narration": record.slide.narration,
                    "reaction_line": record.slide.reaction_line,
                }
                for record in part_records
            ]
            review_report = await review_and_retake(
                work_dir=work_dir,
                part_durations=[record.part_duration for record in part_records],
                part_contexts=part_contexts,
                retake_part=retake_part,
                assemble=assemble,
            )
    except Exception:
        logger.exception("review_and_retake failed; skipping review")
        review_report = ReviewReport(status="skipped")

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

    chapters = _build_chapters(final_slides, slide_offsets)
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
        video_path=(work_dir / "video.mp4").name,
        subtitles_path=subtitles_path.name,
        slide_count=len(final_slides),
        thumbnail_path="thumbnail.png",
        chapters=chapters,
        youtube_description=youtube_description,
        title_candidates=title_candidates,
        thumbnail_text_candidates=draft.thumbnail_text_candidates,
        review_status=review_report.status,
        review_findings=review_report.findings,
        hashtags=draft.hashtags,
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
