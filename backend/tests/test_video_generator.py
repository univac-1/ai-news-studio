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


class TestRenderSongSlideKaraoke:
    """カラオケ演出(highlight_index/zoom)のスモークテスト。既存呼び出し(デフォルト引数)の
    挙動を変えないことと、新しいキーワード引数がPNGを問題なく書き出せることを確認する。"""

    def test_default_args_still_render_png(self, tmp_path):
        # highlight_index=None, zoom=1.0(デフォルト)は既存呼び出し元と同じ挙動のはず
        path = tmp_path / "default.png"
        _render_song_slide(_song_spec(), path)
        assert path.exists()
        with Image.open(path) as img:
            assert img.size == (WIDTH, HEIGHT)

    def test_highlight_and_zoom_without_image_background(self, tmp_path):
        # spec.imageがNone(ダークフォールバック)の場合はzoomを無視して描画できること
        path = tmp_path / "no_image.png"
        _render_song_slide(_song_spec(), path, highlight_index=1, zoom=1.05)
        assert path.exists()
        with Image.open(path) as img:
            assert img.size == (WIDTH, HEIGHT)

    def test_highlight_and_zoom_with_image_background(self, tmp_path):
        # spec.imageがある場合、指定したzoom倍率でMV背景をズームインしたうえで描画できること
        bg = Image.new("RGB", (640, 360), (30, 40, 60))
        path = tmp_path / "with_image.png"
        _render_song_slide(_song_spec(image=bg), path, highlight_index=1, zoom=1.05)
        assert path.exists()
        with Image.open(path) as img:
            assert img.size == (WIDTH, HEIGHT)

    def test_out_of_range_highlight_index_is_treated_as_none(self, tmp_path):
        # 範囲外のhighlight_indexは例外を出さず、全行通常表示(None扱い)にフォールバックする
        path = tmp_path / "out_of_range.png"
        _render_song_slide(_song_spec(), path, highlight_index=99, zoom=1.0)
        assert path.exists()
