import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import weekly_video_scheduler
from app.services.weekly_video_scheduler import WeeklySchedule, next_weekly_run


JST = timezone(timedelta(hours=9), "JST")


def test_next_weekly_run_uses_friday_morning_in_configured_timezone():
    schedule = WeeklySchedule(timezone="Asia/Tokyo", hour=8, minute=0)
    now = datetime(2026, 7, 10, 7, 30, tzinfo=JST)

    run_at = next_weekly_run(now, schedule)

    assert run_at == datetime(2026, 7, 10, 8, 0, tzinfo=JST)


def test_next_weekly_run_moves_to_next_week_after_target_time():
    schedule = WeeklySchedule(timezone="Asia/Tokyo", hour=8, minute=0)
    now = datetime(2026, 7, 10, 8, 0, tzinfo=JST)

    run_at = next_weekly_run(now, schedule)

    assert run_at == datetime(2026, 7, 17, 8, 0, tzinfo=JST)


@pytest.mark.asyncio
async def test_run_once_records_success_and_video_id(tmp_path, monkeypatch):
    state_path = tmp_path / "weekly_video_scheduler.json"
    monkeypatch.setattr(weekly_video_scheduler, "STATE_PATH", state_path)

    async def fake_generate():
        return SimpleNamespace(video=SimpleNamespace(id="video-1"))

    async def fake_notify(_result):
        return None

    monkeypatch.setattr(weekly_video_scheduler, "generate_weekly_video_from_new_draft", fake_generate)
    monkeypatch.setattr(weekly_video_scheduler, "notify_weekly_video_success", fake_notify)

    scheduled_for = datetime(2026, 7, 10, 8, 0, tzinfo=JST)
    await weekly_video_scheduler.run_weekly_video_generation_once(scheduled_for)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_success_date"] == "2026-07-10"
    assert state["last_video_id"] == "video-1"


@pytest.mark.asyncio
async def test_run_once_skips_when_same_date_already_succeeded(tmp_path, monkeypatch):
    state_path = tmp_path / "weekly_video_scheduler.json"
    state_path.write_text(
        json.dumps({"last_success_date": "2026-07-10"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(weekly_video_scheduler, "STATE_PATH", state_path)

    called = False

    async def fake_generate():
        nonlocal called
        called = True

    monkeypatch.setattr(weekly_video_scheduler, "generate_weekly_video_from_new_draft", fake_generate)

    scheduled_for = datetime(2026, 7, 10, 8, 0, tzinfo=JST)
    await weekly_video_scheduler.run_weekly_video_generation_once(scheduled_for)

    assert called is False
