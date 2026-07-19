"""iCalendar feed generation for community meetings."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .google_calendar import build_google_calendar_description, effective_end_at_utc
from .links import build_meeting_deep_link
from .models import Meeting
from .utils import ensure_utc, telegram_html_to_plain

_ICAL_DOMAIN = "girltalkberlin.bot"
_PRODID = "-//Girl Talk Berlin//Meetings Bot//RU"


def meeting_event_uid(public_token: str) -> str:
    """Return a stable UID for a meeting event (no database id)."""
    return f"meeting-{public_token}@{_ICAL_DOMAIN}"


def escape_ical_text(value: str) -> str:
    """Escape text for iCalendar property values (RFC 5545)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_ical_line(line: str) -> str:
    """Fold a content line to 75 octets per RFC 5545."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts: list[str] = []
    pos = 0
    while pos < len(encoded):
        chunk_end = min(pos + 75, len(encoded))
        while chunk_end > pos and (encoded[chunk_end - 1] & 0xC0) == 0x80:
            chunk_end -= 1
        if chunk_end == pos:
            chunk_end = min(pos + 75, len(encoded))
        parts.append(encoded[pos:chunk_end].decode("utf-8"))
        pos = chunk_end
    return parts[0] + "".join("\r\n " + part for part in parts[1:])


def _ical_property(name: str, value: str) -> str:
    return fold_ical_line(f"{name}:{escape_ical_text(value)}")


def _format_ical_utc(dt: datetime) -> str:
    return ensure_utc(dt).strftime("%Y%m%dT%H%M%SZ")


def _meeting_deep_link(meeting: Meeting, bot_username: str | None) -> str | None:
    if not bot_username or not meeting.public_token:
        return None
    try:
        return build_meeting_deep_link(bot_username, meeting.public_token)
    except ValueError:
        return None


def _can_include_meeting(meeting: Meeting) -> bool:
    return bool(
        meeting.topic
        and meeting.topic.strip()
        and meeting.start_at_utc
        and meeting.public_token
    )


def build_vevent(
    meeting: Meeting,
    *,
    bot_username: str | None,
    now_utc: datetime,
) -> str | None:
    """Build a single VEVENT block, or None if the meeting cannot be exported."""
    if not _can_include_meeting(meeting):
        return None

    lines: list[str] = ["BEGIN:VEVENT"]
    lines.append(f"UID:{meeting_event_uid(meeting.public_token)}")
    lines.append(f"DTSTAMP:{_format_ical_utc(now_utc)}")
    lines.append(f"DTSTART:{_format_ical_utc(meeting.start_at_utc)}")
    lines.append(f"DTEND:{_format_ical_utc(effective_end_at_utc(meeting))}")
    if meeting.updated_at:
        lines.append(f"LAST-MODIFIED:{_format_ical_utc(meeting.updated_at)}")
    lines.append(_ical_property("SUMMARY", meeting.topic.strip()))

    description = build_google_calendar_description(meeting, bot_username=bot_username)
    if description:
        lines.append(_ical_property("DESCRIPTION", description))

    if meeting.location and meeting.location.strip():
        location = telegram_html_to_plain(meeting.location.strip())
        if location:
            lines.append(_ical_property("LOCATION", location))

    deep_link = _meeting_deep_link(meeting, bot_username)
    if deep_link:
        lines.append(_ical_property("URL", deep_link))

    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def build_community_ical_feed(
    meetings: Sequence[Meeting],
    *,
    bot_username: str | None,
    now_utc: datetime,
) -> str:
    """Build a complete iCalendar feed for community meetings."""
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    events: list[str] = []
    for meeting in meetings:
        event = build_vevent(meeting, bot_username=bot_username, now_utc=now_utc)
        if event:
            events.append(event)
    footer = ["END:VCALENDAR"]
    body = header + events + footer
    return "\r\n".join(body) + "\r\n"
