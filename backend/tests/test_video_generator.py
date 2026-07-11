from PIL import Image

from app.services.video_generator import HEIGHT, WIDTH, SlideSpec, _render_song_slide


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
