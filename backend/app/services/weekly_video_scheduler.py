import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.config import settings
from .weekly_video_job import (
    generate_weekly_video_from_new_draft,
    notify_weekly_video_failure,
    notify_weekly_video_success,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
STATE_PATH = BASE_DIR / "data" / "weekly_video_scheduler.json"
FRIDAY = 4


@dataclass(frozen=True)
class WeeklySchedule:
    timezone: str
    hour: int
    minute: int


def _schedule_from_settings() -> WeeklySchedule:
    return WeeklySchedule(
        timezone=settings.WEEKLY_VIDEO_SCHEDULE_TIMEZONE,
        hour=settings.WEEKLY_VIDEO_SCHEDULE_HOUR,
        minute=settings.WEEKLY_VIDEO_SCHEDULE_MINUTE,
    )


def _get_timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        if name in {"Asia/Tokyo", "JST"}:
            return timezone(timedelta(hours=9), "JST")
        if name == "UTC":
            return timezone.utc
        raise ValueError(f"Invalid timezone: {name}") from exc


def next_weekly_run(now: datetime, schedule: WeeklySchedule, weekday: int = FRIDAY) -> datetime:
    tz = _get_timezone(schedule.timezone)
    local_now = now.astimezone(tz)
    target_time = time(schedule.hour, schedule.minute, tzinfo=tz)
    days_until = (weekday - local_now.weekday()) % 7
    candidate = datetime.combine(local_now.date() + timedelta(days=days_until), target_time)
    if candidate <= local_now:
        candidate += timedelta(days=7)
    return candidate


def _load_state() -> dict[str, str]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load weekly video scheduler state")
        return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_weekly_video_generation_once(scheduled_for: datetime) -> None:
    local_date = scheduled_for.date().isoformat()
    state = _load_state()
    if state.get("last_success_date") == local_date:
        logger.info("Weekly video generation already completed for %s; skipping", local_date)
        return

    state["last_attempt_at"] = datetime.now(tz=scheduled_for.tzinfo).isoformat()
    state["last_scheduled_for"] = scheduled_for.isoformat()
    _save_state(state)

    try:
        result = await generate_weekly_video_from_new_draft()
    except Exception as exc:
        logger.exception("Weekly video generation failed")
        await notify_weekly_video_failure(exc)
        raise

    await notify_weekly_video_success(result)
    state = _load_state()
    state["last_success_date"] = local_date
    state["last_success_at"] = datetime.now(tz=scheduled_for.tzinfo).isoformat()
    state["last_video_id"] = result.video.id
    _save_state(state)


async def weekly_video_scheduler_loop(stop_event: asyncio.Event) -> None:
    schedule = _schedule_from_settings()
    logger.info(
        "Weekly video scheduler started: Friday %02d:%02d %s",
        schedule.hour,
        schedule.minute,
        schedule.timezone,
    )
    while not stop_event.is_set():
        run_at = next_weekly_run(datetime.now(tz=timezone.utc), schedule)
        delay = max((run_at - datetime.now(tz=run_at.tzinfo)).total_seconds(), 0)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
            break
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            break
        try:
            await run_weekly_video_generation_once(run_at)
        except Exception:
            pass
