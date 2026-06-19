"""Automatic participant reminder DMs before upcoming meetings."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING

from dateutil import tz as dateutil_tz
from telegram import Bot

from .log_context import log_event, user_log_fields
from .meeting_notifications import build_meeting_open_keyboard
from .models import Meeting, Registration
from .utils import ensure_utc

if TYPE_CHECKING:
    from .storage import Database

log = logging.getLogger(__name__)

REMINDER_OFFSETS = (7, 3, 1)


def local_day_bounds(target_date: date, local_tz) -> tuple[datetime, datetime]:
    """Return UTC bounds for a local calendar day."""
    from_utc = datetime.combine(target_date, time.min).replace(tzinfo=local_tz).astimezone(dateutil_tz.UTC)
    to_utc = datetime.combine(target_date, time.max).replace(tzinfo=local_tz).astimezone(dateutil_tz.UTC)
    return from_utc, to_utc


def reminder_date_for(meeting: Meeting, offset: int, local_tz) -> date:
    """Return the local calendar date when a reminder should be sent."""
    meeting_date = ensure_utc(meeting.start_at_utc).astimezone(local_tz).date()
    return meeting_date - timedelta(days=offset)


def should_skip_reminder(reg: Registration, reminder_date_local: date, local_tz) -> bool:
    """Skip when the participant registered on the same day as the reminder."""
    registration_date = ensure_utc(reg.created_at).astimezone(local_tz).date()
    return registration_date == reminder_date_local


def format_reminder_message(meeting: Meeting, local_tz) -> str:
    """Build the Russian reminder message for a registered participant."""
    start_local = ensure_utc(meeting.start_at_utc).astimezone(local_tz)
    date_str = start_local.strftime("%d.%m.%Y")
    start_time = start_local.strftime("%H:%M")
    end_local = (
        ensure_utc(meeting.end_at_utc).astimezone(local_tz)
        if meeting.end_at_utc is not None
        else None
    )
    if end_local:
        when = f"{date_str}, {start_time}–{end_local:%H:%M}"
    else:
        when = f"{date_str}, {start_time}"
    location = meeting.location or "TBA"
    return (
        "Небольшое напоминание 🌿\n\n"
        f"Ты записана на встречу «{meeting.topic}».\n\n"
        f"🗓 {when}\n"
        f"📍 {location}\n\n"
        "До встречи 💛"
    )


async def process_participant_reminders(
    bot: Bot,
    db: Database,
    local_tz,
    bot_username: str | None,
    *,
    now_utc: datetime | None = None,
) -> None:
    """Send 7/3/1-day reminders to confirmed non-host participants."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    else:
        now_utc = ensure_utc(now_utc)

    today_local = now_utc.astimezone(local_tz).date()

    for offset in REMINDER_OFFSETS:
        target_date = today_local + timedelta(days=offset)
        from_utc, to_utc = local_day_bounds(target_date, local_tz)
        meetings = await db.list_meetings_in_range(from_utc, to_utc)

        for meeting in meetings:
            if not await db.is_meeting_open(meeting.id, now_utc):
                continue

            keyboard = build_meeting_open_keyboard(meeting, bot_username)
            if keyboard is None:
                log_event(
                    log,
                    logging.WARNING,
                    "reminder_missing_keyboard",
                    meeting_id=meeting.id,
                    offset_days=offset,
                )

            text = format_reminder_message(meeting, local_tz)
            participants = await db.list_confirmed_participants(meeting.id)

            for reg, user in participants:
                if should_skip_reminder(reg, today_local, local_tz):
                    continue
                if await db.has_participant_reminder(meeting.id, user.id, offset):
                    continue
                if not await db.is_registered(meeting.id, user.id):
                    continue

                try:
                    await bot.send_message(
                        chat_id=user.id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                except Exception:
                    log_event(
                        log,
                        logging.ERROR,
                        "reminder_send_failed",
                        meeting_id=meeting.id,
                        offset_days=offset,
                        **user_log_fields(user_id=user.id, username=user.username, name=user.name),
                        exc_info=True,
                    )
                    continue

                await db.record_participant_reminder(
                    meeting.id, user.id, offset, now_utc
                )
