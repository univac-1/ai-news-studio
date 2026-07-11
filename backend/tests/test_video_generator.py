import pytest
from PIL import Image

from app.services.video_generator import (
    HEIGHT,
    WIDTH,
    SlideSpec,
    _even_song_entries,
    _render_song_slide,
)


def _song_spec(image: Image.Image | None = None) -> SlideSpec:
    return SlideSpec(
        kind="song",
        title="こんしゅうのうた",
        body="",
        narration="",
        lyrics=["いちぎょうめのうた", "にぎょうめのうた", "さんぎょうめのうた", "よんぎょうめのうた"],
        image=image,
    )


class TestRenderSongSlide:
    """歌スライドは背景のみを描く(歌詞・タイトル・キャラの後乗せはしない。
    歌詞はフレーズごとの焼き込み字幕で画面下に出す)。"""

    def test_renders_dark_fallback_without_image(self, tmp_path):
        path = tmp_path / "no_image.png"
        _render_song_slide(_song_spec(), path)
        assert path.exists()
        with Image.open(path) as img:
            assert img.size == (WIDTH, HEIGHT)

    def test_renders_cover_cropped_mv_background(self, tmp_path):
        # 16:9でないMV背景画像も全画面にカバークロップされて描画されること
        bg = Image.new("RGB", (640, 480), (30, 40, 60))
        path = tmp_path / "with_image.png"
        _render_song_slide(_song_spec(image=bg), path)
        assert path.exists()
        with Image.open(path) as img:
            assert img.size == (WIDTH, HEIGHT)
            # 背景のみ(テキスト描画なし)なので、単色画像は単色のまま出力される
            colors = img.convert("RGB").getcolors(maxcolors=16)
            assert colors is not None and len(colors) == 1

    def test_background_only_has_no_character_overlay(self, tmp_path):
        # 単色背景に対して出力も単色 = キャラクター等が合成されていないことの検証
        bg = Image.new("RGB", (1920, 1080), (10, 20, 30))
        path = tmp_path / "clean.png"
        _render_song_slide(_song_spec(image=bg), path)
        with Image.open(path) as img:
            colors = img.convert("RGB").getcolors(maxcolors=16)
            assert colors is not None and len(colors) == 1


class TestEvenSongEntries:
    """Veoが歌唱音声ごと生成するため、歌詞字幕はクリップ音声の実尺を均等割りして出す。"""

    def test_sums_to_total_and_leads_with_blank(self):
        lyrics = ["あ", "い", "う", "え"]
        entries = _even_song_entries(lyrics, 15.0)

        assert entries[0][0] == ""  # 先頭は前奏(無音字幕)
        assert [text for text, _ in entries[1:]] == lyrics
        assert sum(duration for _, duration in entries) == pytest.approx(15.0)

    def test_lead_is_capped_at_one_second(self):
        entries = _even_song_entries(["あ", "い"], 60.0)
        assert entries[0][1] == pytest.approx(1.0)

    def test_short_clip_still_gives_positive_durations(self):
        entries = _even_song_entries(["あ", "い", "う", "え"], 0.5)
        assert all(duration > 0 for _, duration in entries)
