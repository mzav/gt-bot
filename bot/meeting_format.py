"""Meeting time display helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from .models import Meeting
from .utils import ensure_utc

TimeStyle = Literal["iso", "short", "card", "card_new", "announce", "today", "details_date", "details_time"]

_RUSSIAN_WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)
_RUSSIAN_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_RUSSIAN_MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def format_meeting_time(
    meeting: Meeting,
    local_tz,
    *,
    style: TimeStyle = "iso",
) -> str:
    """Format meeting start (and end when set) in local timezone."""
    start_local = ensure_utc(meeting.start_at_utc).astimezone(local_tz)
    end_local = (
        ensure_utc(meeting.end_at_utc).astimezone(local_tz)
        if meeting.end_at_utc is not None
        else None
    )

    if style == "iso":
        if end_local:
            return f"{start_local:%Y-%m-%d %H:%M}–{end_local:%H:%M}"
        return f"{start_local:%Y-%m-%d %H:%M}"

    if style == "short":
        if end_local:
            return f"{start_local:%d.%m.%Y %H:%M}–{end_local:%H:%M}"
        return f"{start_local:%d.%m.%Y %H:%M}"

    if style == "card":
        weekday = _RUSSIAN_WEEKDAYS[start_local.weekday()]
        if end_local:
            return f"{start_local:%d.%m} ({weekday}) {start_local:%H:%M} - {end_local:%H:%M}"
        return f"{start_local:%d.%m} ({weekday}) {start_local:%H:%M}"

    if style == "card_new":
        if end_local:
            return f"{start_local:%d %B at %H:%M}–{end_local:%H:%M}"
        return f"{start_local:%d %B at %H:%M}"

    if style == "announce":
        weekday = _RUSSIAN_WEEKDAYS[start_local.weekday()].capitalize()
        month = _RUSSIAN_MONTHS_GENITIVE[start_local.month - 1]
        if end_local:
            return f"{weekday}, {start_local.day} {month} {start_local:%H:%M} - {end_local:%H:%M}"
        return f"{weekday}, {start_local.day} {month} {start_local:%H:%M}"

    if style == "today":
        if end_local:
            return f"{start_local:%H:%M}–{end_local:%H:%M}"
        return f"{start_local:%H:%M}"

    if style == "details_date":
        weekday = _RUSSIAN_WEEKDAYS[start_local.weekday()]
        month = _RUSSIAN_MONTHS_GENITIVE[start_local.month - 1]
        return f"{weekday}, {start_local.day} {month} {start_local.year}"

    if style == "details_time":
        if end_local:
            return f"{start_local:%H:%M}–{end_local:%H:%M} (Berlin time)"
        return f"{start_local:%H:%M} (Berlin time)"

    raise ValueError(f"Unknown style: {style}")


def format_month_year_russian(dt: datetime) -> str:
    """Format a local datetime as 'июнь 2026'."""
    month = _RUSSIAN_MONTHS_NOMINATIVE[dt.month - 1]
    return f"{month} {dt.year}"


def format_registration_start(meeting: Meeting, local_tz) -> str:
    """Format registration start for host-facing display."""
    if meeting.registration_starts_at_utc is None:
        return "Сразу"
    when = ensure_utc(meeting.registration_starts_at_utc).astimezone(local_tz)
    return f"{when:%d.%m.%Y %H:%M}"
