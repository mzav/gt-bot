"""Google Calendar template-link helpers for meetings."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import quote, urlencode

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .links import build_meeting_deep_link
from .utils import telegram_html_to_plain
from .models import Meeting
from .utils import ensure_utc

DEFAULT_MEETING_DURATION = timedelta(hours=2)

_GCAL_BASE_URL = "https://calendar.google.com/calendar/render"

GCAL_ADD_DISCLAIMER_EN = (
    "This creates a personal copy in Google Calendar. "
    "If the meeting changes later, update or remove it manually."
)
GCAL_ADD_DISCLAIMER_RU = (
    "Это создаёт личную копию в Google Calendar. "
    "Если встреча изменится, обновите или удалите запись вручную. 👇"
)
GCAL_UPDATE_REMINDER_EN = (
    "\n\nIf you added this meeting to Google Calendar, update or remove the event manually."
)
GCAL_UPDATE_REMINDER_RU = (
    "\n\nЕсли вы добавляли встречу в Google Calendar, обновите или удалите запись вручную."
)

GCAL_BUTTON_LABEL_EN = "Add to Google Calendar"
GCAL_BUTTON_LABEL_RU = "Добавить в Google Calendar"

def effective_end_at_utc(meeting: Meeting) -> datetime:
    """Return meeting end time, falling back to start + default duration."""
    if meeting.end_at_utc:
        return ensure_utc(meeting.end_at_utc)
    return ensure_utc(meeting.start_at_utc) + DEFAULT_MEETING_DURATION


def can_offer_google_calendar(*, is_host: bool, is_participant: bool) -> bool:
    """Return True when the user may add the meeting to their calendar."""
    return is_host or is_participant


def gcal_disclaimer(lang: Literal["en", "ru"]) -> str:
    return GCAL_ADD_DISCLAIMER_RU if lang == "ru" else GCAL_ADD_DISCLAIMER_EN


def gcal_update_reminder(lang: Literal["en", "ru"]) -> str:
    return GCAL_UPDATE_REMINDER_RU if lang == "ru" else GCAL_UPDATE_REMINDER_EN


def gcal_button_label(lang: Literal["en", "ru"]) -> str:
    return GCAL_BUTTON_LABEL_RU if lang == "ru" else GCAL_BUTTON_LABEL_EN


def _format_gcal_local_datetime(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def build_google_calendar_description(
    meeting: Meeting,
    *,
    bot_username: str | None = None,
) -> str:
    """Compose the Google Calendar event description from meeting data."""
    parts: list[str] = []
    if meeting.description and meeting.description.strip():
        parts.append(telegram_html_to_plain(meeting.description.strip()))
    if bot_username and meeting.public_token:
        try:
            link = build_meeting_deep_link(bot_username, meeting.public_token)
            parts.append(f"Meeting in bot: {link}")
        except ValueError:
            pass
    return "\n\n".join(parts)


def build_google_calendar_event_url(
    meeting: Meeting,
    *,
    local_tz,
    bot_username: str | None = None,
    duration: timedelta = DEFAULT_MEETING_DURATION,
) -> str | None:
    """Build a Google Calendar event-creation URL from meeting data."""
    if not meeting.topic or not meeting.topic.strip():
        return None
    if not meeting.start_at_utc:
        return None

    start_local = ensure_utc(meeting.start_at_utc).astimezone(local_tz)
    if duration is not DEFAULT_MEETING_DURATION:
        end_local = start_local + duration
    else:
        end_local = effective_end_at_utc(meeting).astimezone(local_tz)
    dates = f"{_format_gcal_local_datetime(start_local)}/{_format_gcal_local_datetime(end_local)}"

    params: dict[str, str] = {
        "action": "TEMPLATE",
        "text": meeting.topic.strip(),
        "dates": dates,
    }

    description = build_google_calendar_description(meeting, bot_username=bot_username)
    if description:
        params["details"] = description

    if meeting.location and meeting.location.strip():
        params["location"] = telegram_html_to_plain(meeting.location.strip())

    return f"{_GCAL_BASE_URL}?{urlencode(params, quote_via=quote)}"


def google_calendar_keyboard(
    url: str,
    *,
    lang: Literal["en", "ru"] = "en",
) -> InlineKeyboardMarkup:
    """Inline keyboard with a single Google Calendar URL button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text=gcal_button_label(lang), url=url)],
    ])


def build_calendar_offer(
    meeting: Meeting,
    *,
    local_tz,
    bot_username: str | None = None,
    lang: Literal["en", "ru"] = "en",
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Return disclaimer text and keyboard for a calendar offer, or None if URL cannot be built."""
    url = build_google_calendar_event_url(
        meeting,
        local_tz=local_tz,
        bot_username=bot_username,
    )
    if not url:
        return None
    return gcal_disclaimer(lang), google_calendar_keyboard(url, lang=lang)
