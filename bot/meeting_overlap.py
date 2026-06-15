"""Meeting time overlap detection for registration."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from .meeting_reminders import local_day_bounds
from .models import Meeting
from .utils import ensure_utc

if TYPE_CHECKING:
    from .storage import Database


def overlap_bounds(meeting: Meeting, local_tz) -> tuple[datetime, datetime]:
    """Return UTC (start, end) bounds used for overlap detection."""
    start = ensure_utc(meeting.start_at_utc)
    if meeting.end_at_utc is not None:
        return start, ensure_utc(meeting.end_at_utc)
    local_date = start.astimezone(local_tz).date()
    return local_day_bounds(local_date, local_tz)


def meetings_overlap(a: Meeting, b: Meeting, local_tz) -> bool:
    """Return True when two meetings overlap in time."""
    start_a, end_a = overlap_bounds(a, local_tz)
    start_b, end_b = overlap_bounds(b, local_tz)
    return start_a < end_b and start_b < end_a


async def find_overlapping_meetings(
    db: Database,
    user_id: int,
    target: Meeting,
    local_tz,
    now_utc: datetime,
) -> list[Meeting]:
    """Return upcoming user meetings that overlap with target (excluding target itself)."""
    user_meetings = await db.list_user_meetings(user_id, now_utc)
    return [
        m for m in user_meetings
        if m.id != target.id and meetings_overlap(m, target, local_tz)
    ]
