"""Meeting time display helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from .models import Meeting
from .utils import ensure_utc

TimeStyle = Literal["iso", "short", "card", "card_new", "today", "details_date", "details_time"]


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
        if end_local:
            return f"{start_local:%d.%m (%A) %H:%M}–{end_local:%H:%M}"
        return f"{start_local:%d.%m (%A) %H:%M}"

    if style == "card_new":
        if end_local:
            return f"{start_local:%d %B at %H:%M}–{end_local:%H:%M}"
        return f"{start_local:%d %B at %H:%M}"

    if style == "today":
        if end_local:
            return f"{start_local:%H:%M}–{end_local:%H:%M}"
        return f"{start_local:%H:%M}"

    if style == "details_date":
        return start_local.strftime("%A, %d %B %Y")

    if style == "details_time":
        if end_local:
            return f"{start_local:%H:%M}–{end_local:%H:%M} (Berlin time)"
        return f"{start_local:%H:%M} (Berlin time)"

    raise ValueError(f"Unknown style: {style}")


def format_registration_start(meeting: Meeting, local_tz) -> str:
    """Format registration start for host-facing display."""
    if meeting.registration_starts_at_utc is None:
        return "Сразу"
    when = ensure_utc(meeting.registration_starts_at_utc).astimezone(local_tz)
    return f"{when:%d.%m.%Y %H:%M}"
