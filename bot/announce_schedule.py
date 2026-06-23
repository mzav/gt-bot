"""Pure helpers for scheduling urgent channel announcements."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from dateutil import tz

from .utils import ensure_utc

_NEXT_WINDOW_OFFSET_DAYS = 3


def announcement_window(announce_days: list[int], today: date) -> tuple[date, date]:
    """Return the (from_date, to_date) coverage window for today's announcement."""
    month_start = today.replace(day=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if today.day == min(announce_days):
        return month_start, month_end
    from_date = today + timedelta(days=_NEXT_WINDOW_OFFSET_DAYS)
    return from_date, month_end


def covered_by_future_announcement(
    meeting_date: date, today: date, announce_days: list[int]
) -> bool:
    """Return True if meeting_date will appear in a scheduled digest before it occurs."""
    sorted_days = sorted(announce_days)
    year, month = today.year, today.month
    for _ in range(2):
        for d in sorted_days:
            try:
                announce_date = date(year, month, d)
            except ValueError:
                continue
            if announce_date <= today or announce_date >= meeting_date:
                continue
            from_date, to_date = announcement_window(sorted_days, announce_date)
            if from_date <= meeting_date <= to_date:
                return True
        month += 1
        if month > 12:
            month, year = 1, year + 1
        if date(year, month, 1) > meeting_date:
            break
    return False


def reg_start_at_announce_time(reg_day: date, announce_time: time, local_tz) -> datetime:
    """Return UTC datetime for registration opening on reg_day at announce_time."""
    local_dt = datetime.combine(reg_day, announce_time, tzinfo=local_tz)
    return local_dt.astimezone(tz.UTC)


def compute_urgent_announce_at(
    *,
    meeting_start_at_utc: datetime,
    registration_starts_at_utc: datetime | None,
    reference_date: date,
    announce_days: list[int],
    created_at_utc: datetime,
    local_tz,
) -> datetime | None:
    """Return when to publish an urgent announce, or None if digest covers the meeting."""
    meeting_date = ensure_utc(meeting_start_at_utc).astimezone(local_tz).date()
    if covered_by_future_announcement(meeting_date, reference_date, announce_days):
        return None
    if registration_starts_at_utc is None:
        return ensure_utc(created_at_utc)
    return ensure_utc(registration_starts_at_utc)


_announcement_window = announcement_window
_covered_by_future_announcement = covered_by_future_announcement
