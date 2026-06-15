"""Participant notifications for important meeting changes."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from .google_calendar import gcal_update_reminder
from .links import build_meeting_deep_link
from .meeting_format import format_meeting_time
from .models import Meeting
from .utils import ensure_utc

if TYPE_CHECKING:
    from .storage import Database

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeetingSnapshot:
    start_at_utc: datetime
    end_at_utc: datetime | None
    location: str | None
    canceled_at: datetime | None


def snapshot_meeting(meeting: Meeting) -> MeetingSnapshot:
    return MeetingSnapshot(
        start_at_utc=ensure_utc(meeting.start_at_utc),
        end_at_utc=ensure_utc(meeting.end_at_utc) if meeting.end_at_utc else None,
        location=meeting.location,
        canceled_at=ensure_utc(meeting.canceled_at) if meeting.canceled_at else None,
    )


def _normalize_location(location: str | None) -> str | None:
    if location is None:
        return None
    stripped = location.strip()
    return stripped or None


def detect_important_changes(before: MeetingSnapshot, after: Meeting) -> list[str]:
    """Return Russian labels for meaningful field changes."""
    changes: list[str] = []
    after_start = ensure_utc(after.start_at_utc)
    if before.start_at_utc != after_start:
        changes.append("время начала")

    after_end = ensure_utc(after.end_at_utc) if after.end_at_utc else None
    if before.end_at_utc != after_end:
        changes.append("время окончания")

    if _normalize_location(before.location) != _normalize_location(after.location):
        changes.append("место")

    after_canceled = ensure_utc(after.canceled_at) if after.canceled_at else None
    if before.canceled_at != after_canceled and after_canceled is not None:
        changes.append("встреча отменена")

    return changes


def format_participant_update_message(
    meeting: Meeting,
    changes: list[str],
    *,
    local_tz,
    bot_username: str | None = None,
) -> str:
    """Build a short Russian update message for registered participants."""
    lines = [f"⚠️ <b>Обновление встречи «{meeting.topic}»</b>", ""]
    if changes:
        lines.append("Изменено: " + ", ".join(changes) + ".")
        lines.append("")
    lines.append(f"📅 {format_meeting_time(meeting, local_tz, style='short')}")
    location = meeting.location and meeting.location.strip()
    lines.append(f"📍 {location or 'не указано'}")
    lines.append(gcal_update_reminder("ru"))
    return "\n".join(lines)


def build_meeting_open_keyboard(
    meeting: Meeting, bot_username: str | None
) -> InlineKeyboardMarkup | None:
    """Build a single URL button to open meeting details."""
    if not bot_username or not meeting.public_token:
        return None
    try:
        link = build_meeting_deep_link(bot_username, meeting.public_token)
    except ValueError:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Открыть встречу", url=link),
    ]])


def _build_update_keyboard(meeting: Meeting, bot_username: str | None) -> InlineKeyboardMarkup | None:
    return build_meeting_open_keyboard(meeting, bot_username)


async def notify_participants(
    bot: Bot,
    db: Database,
    meeting: Meeting,
    changes: list[str],
    *,
    exclude_user_id: int,
    local_tz,
    bot_username: str | None = None,
) -> None:
    """Notify confirmed non-host participants about important meeting changes."""
    if not changes:
        return

    text = format_participant_update_message(
        meeting, changes, local_tz=local_tz, bot_username=bot_username
    )
    keyboard = _build_update_keyboard(meeting, bot_username)
    rows = await db.list_confirmed_participants(meeting.id)

    for reg, user in rows:
        if user.id == exclude_user_id:
            continue
        try:
            await bot.send_message(
                chat_id=user.id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            log.exception("Failed to notify participant user_id=%s meeting_id=%s", user.id, meeting.id)
