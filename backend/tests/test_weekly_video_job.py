import pytest

from app.schemas.draft import VideoPlanDraft, VideoSegment
from app.schemas.video import VideoArtifact, VideoGenerationResult
from app.services import weekly_video_job


def _result() -> VideoGenerationResult:
    draft = VideoPlanDraft(
        title="Weekly AI News",
        week_label="2026-07-10",
        thumbnail_text="AI News",
        intro="Intro text",
        segments=[
            VideoSegment(
                number=1,
                headline="New model released",
                summary="A new model was announced.",
                impact="Teams can automate more review work.",
                action="Check the API changes.",
                slide_title="Model release",
                narration="Narration text",
                source="Example News",
            )
        ],
        outro="Outro text",
        slide_outline=["Opening", "Model release"],
        narration_script="Full narration",
        description="Draft description",
        hashtags=["#AI", "#News"],
        reference_urls=["https://example.com/news"],
        total_items=1,
        generated_at="2026-07-10T08:00:00+09:00",
    )
    video = VideoArtifact(
        id="video-1",
        title="Weekly AI News",
        created_at="2026-07-10T08:30:00+09:00",
        draft_generated_at=draft.generated_at,
        total_items=1,
        duration_seconds=60.0,
        video_path="video.mp4",
        subtitles_path="subtitles.srt",
        slide_count=2,
        thumbnail_path="thumbnail.png",
        chapters="00:00 Opening\n00:10 Model release",
        youtube_description="YouTube description",
        hashtags=draft.hashtags,
        youtube_video_id="yt-123",
        youtube_privacy="unlisted",
        youtube_url="https://youtu.be/yt-123",
    )
    return VideoGenerationResult(draft=draft, video=video)


def test_success_email_body_includes_unlisted_url_and_video_contents():
    body = weekly_video_job.build_weekly_video_success_email_body(_result())

    assert "Unlisted YouTube URL: https://youtu.be/yt-123" in body
    assert "YouTube privacy: unlisted" in body
    assert "YouTube description" in body
    assert "00:10 Model release" in body
    assert "1. New model released" in body
    assert "Summary: A new model was announced." in body
    assert "Impact: Teams can automate more review work." in body
    assert "Action: Check the API changes." in body
    assert "Source: Example News" in body
    assert "#AI #News" in body
    assert "https://example.com/news" in body


@pytest.mark.asyncio
async def test_success_notification_sends_generated_body(monkeypatch):
    sent = {}

    def fake_send_email_notification(subject: str, body: str) -> None:
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr(weekly_video_job, "send_email_notification", fake_send_email_notification)

    await weekly_video_job.notify_weekly_video_success(_result())

    assert sent["subject"] == "[AI News Studio] Weekly video generated: Weekly AI News"
    assert "Unlisted YouTube URL: https://youtu.be/yt-123" in sent["body"]
    assert "1. New model released" in sent["body"]
