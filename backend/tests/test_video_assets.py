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

    result = video_assets._generate_extended_opening_sync("veo-3.1-fast-generate-001", prompts, 22)

    assert result == b"video"
    assert used_prompts == ["base", "rise", "settle"]


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


def test_song_motion_prompt_includes_lyrics_and_news_context():
    prompt = video_assets._song_motion_prompt(
        ["AIニュースを歌う", "未来が近づく"],
        ["#1 category=hardware new AI chip"],
    )

    assert "AIニュースを歌う" in prompt
    assert "new AI chip" in prompt
    assert "without rendering the lyrics as text" in prompt
