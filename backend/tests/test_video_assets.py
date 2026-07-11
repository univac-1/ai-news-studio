from app.services import video_assets
from app.schemas.draft import VideoSegment


def test_extended_opening_uses_extension_prompts(monkeypatch):
    prompts = ["base", "rise", "settle"]
    used_prompts: list[str] = []

    class FakeVideo:
        video_bytes = b"video"

    def fake_generate_video_sync(model, prompt, **kwargs):
        used_prompts.append(prompt)
        return FakeVideo()

    monkeypatch.setattr(video_assets.settings, "VIDEO_GEN_DURATION_SECONDS", 8)
    monkeypatch.setattr(video_assets, "_generate_video_sync", fake_generate_video_sync)

    result = video_assets._generate_extended_clip_sync("veo-3.1-fast-generate-001", prompts, 22)

    assert result == b"video"
    assert used_prompts == ["base", "rise", "settle"]


def test_extended_clip_passes_image_and_audio_flags(monkeypatch):
    calls: list[dict] = []

    class FakeVideo:
        video_bytes = b"video"

    def fake_generate_video_sync(model, prompt, **kwargs):
        calls.append(kwargs)
        return FakeVideo()

    monkeypatch.setattr(video_assets.settings, "VIDEO_GEN_DURATION_SECONDS", 8)
    monkeypatch.setattr(video_assets, "_generate_video_sync", fake_generate_video_sync)

    result = video_assets._generate_extended_clip_sync(
        "veo-3.1-fast-generate-001",
        ["base", "more"],
        15,
        image_bytes=b"png",
        generate_audio=True,
    )

    assert result == b"video"
    # ベース生成のみ画像を先頭フレームとして渡し、全呼び出しで音声を生成する
    assert calls[0]["image_bytes"] == b"png"
    assert all(call["generate_audio"] is True for call in calls)
    assert len(calls) == 2  # ベース8秒 + 拡張1回で15秒


def test_opening_context_suffix_keeps_topics_abstract():
    suffix = video_assets._opening_context_suffix("2026年7月第2週", ["AI検索", "半導体"])

    assert "AI検索" in suffix
    assert "半導体" in suffix
    assert "do not render topic names" in suffix


def test_segment_motion_prompt_includes_news_context():
    segment = VideoSegment(
        number=1,
        headline="AI model launches new coding agent",
        summary="The tool can inspect repositories and propose patches.",
        impact="Developers can automate repetitive maintenance.",
        action="Review generated changes before merging.",
        slide_title="Coding agent",
        narration="",
        category="devtools",
    )

    prompt = video_assets._segment_motion_prompt(segment)

    assert "AI model launches new coding agent" in prompt
    assert "devtools" in prompt
    assert "abstract visual metaphors" in prompt
    assert "any text" in prompt


def test_song_video_prompts_split_lyrics_and_include_news_context():
    prompts = video_assets._song_video_prompts(
        ["ずんずんニュースだ", "きょうのわだいだ", "モデルがきたのだ", "いっしょにみるのだ"],
        ["#1 category=hardware new AI chip"],
    )

    base, extension, outro = prompts
    # 前半2フレーズはベース生成、後半2フレーズは拡張で歌わせる。
    # 歌詞はVeoが歌唱として解釈しやすいダブルクォート引用で渡す
    assert '"ずんずんニュースだ"' in base and '"きょうのわだいだ"' in base
    assert '"モデルがきたのだ"' in extension and '"いっしょにみるのだ"' in extension
    assert "モデルがきたのだ" not in base
    # ボーカルが主役で、インストのみの曲にならないことを明示する
    assert "WITH VOCALS" in base
    for prompt in (base, extension):
        assert "instrumental-only" in prompt
    # アウトロはボーカルなしで曲を続ける
    assert "without vocals" in outro
    for prompt in prompts:
        assert "new AI chip" in prompt
        assert "no on-screen text" in prompt
