"""Tests for Google Calendar link generation."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from dateutil import tz

from bot.google_calendar import (
    DEFAULT_MEETING_DURATION,
    build_calendar_offer,
    build_google_calendar_description,
    build_google_calendar_event_url,
    can_offer_google_calendar,
    google_calendar_keyboard,
    gcal_button_label,
    gcal_disclaimer,
)
from bot.models import Meeting


@pytest.fixture
def berlin_tz():
    return tz.gettz("Europe/Berlin")


def _make_meeting(**overrides) -> Meeting:
    defaults = {
        "id": 1,
        "topic": "Coffee Chat",
        "description": "Weekly catch-up",
        "start_at_utc": datetime(2026, 7, 1, 16, 0, 0, tzinfo=timezone.utc),
        "max_participants": 5,
        "location": "Berlin Cafe",
        "created_by": 1,
        "public_token": "abc123token",
    }
    defaults.update(overrides)
    return Meeting(**defaults)


def test_build_url_full_meeting(berlin_tz):
    meeting = _make_meeting()
    url = build_google_calendar_event_url(
        meeting,
        local_tz=berlin_tz,
        bot_username="TestBot",
    )
    assert url is not None
    assert url.startswith("https://calendar.google.com/calendar/render?")

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert params["action"] == ["TEMPLATE"]
    assert params["text"] == ["Coffee Chat"]
    assert params["dates"] == ["20260701T180000/20260701T200000"]
    assert params["location"] == ["Berlin Cafe"]
    assert "Weekly catch-up" in params["details"][0]
    assert "https://t.me/TestBot?start=m_abc123token" in params["details"][0]


def test_build_url_missing_location(berlin_tz):
    meeting = _make_meeting(location=None)
    url = build_google_calendar_event_url(meeting, local_tz=berlin_tz)
    assert url is not None
    params = parse_qs(urlparse(url).query)
    assert "location" not in params


def test_build_url_empty_location(berlin_tz):
    meeting = _make_meeting(location="   ")
    url = build_google_calendar_event_url(meeting, local_tz=berlin_tz)
    assert url is not None
    params = parse_qs(urlparse(url).query)
    assert "location" not in params


def test_build_url_empty_description_with_bot_link(berlin_tz):
    meeting = _make_meeting(description="")
    url = build_google_calendar_event_url(
        meeting,
        local_tz=berlin_tz,
        bot_username="TestBot",
    )
    assert url is not None
    params = parse_qs(urlparse(url).query)
    assert params["details"] == ["Meeting in bot: https://t.me/TestBot?start=m_abc123token"]


def test_build_url_no_bot_username(berlin_tz):
    meeting = _make_meeting()
    url = build_google_calendar_event_url(meeting, local_tz=berlin_tz, bot_username=None)
    assert url is not None
    params = parse_qs(urlparse(url).query)
    assert "Weekly catch-up" in params["details"][0]
    assert "t.me" not in params["details"][0]


def test_build_url_no_public_token(berlin_tz):
    meeting = _make_meeting(public_token=None)
    url = build_google_calendar_event_url(
        meeting,
        local_tz=berlin_tz,
        bot_username="TestBot",
    )
    assert url is not None
    params = parse_qs(urlparse(url).query)
    assert "Weekly catch-up" in params["details"][0]
    assert "t.me" not in params["details"][0]


def test_build_url_default_duration_two_hours(berlin_tz):
    meeting = _make_meeting()
    url = build_google_calendar_event_url(meeting, local_tz=berlin_tz)
    params = parse_qs(urlparse(url).query)
    start, end = params["dates"][0].split("/")
    assert start == "20260701T180000"
    assert end == "20260701T200000"
    assert DEFAULT_MEETING_DURATION.total_seconds() == 2 * 3600


def test_build_url_uses_meeting_end_at_utc(berlin_tz):
    meeting = _make_meeting(
        end_at_utc=datetime(2026, 7, 1, 19, 30, 0, tzinfo=timezone.utc),
    )
    url = build_google_calendar_event_url(meeting, local_tz=berlin_tz)
    params = parse_qs(urlparse(url).query)
    assert params["dates"] == ["20260701T180000/20260701T213000"]


def test_build_url_special_characters_encoded(berlin_tz):
    meeting = _make_meeting(
        topic="Meet & Greet",
        description="Line one\nLine two",
        location="Café №1",
    )
    url = build_google_calendar_event_url(meeting, local_tz=berlin_tz)
    assert url is not None
    assert "&" in url
    params = parse_qs(urlparse(url).query)
    assert params["text"] == ["Meet & Greet"]
    assert params["location"] == ["Café №1"]
    assert "Line one\nLine two" in params["details"][0]


def test_build_url_missing_topic(berlin_tz):
    meeting = _make_meeting(topic="")
    assert build_google_calendar_event_url(meeting, local_tz=berlin_tz) is None


def test_build_url_missing_start(berlin_tz):
    meeting = _make_meeting(start_at_utc=None)
    assert build_google_calendar_event_url(meeting, local_tz=berlin_tz) is None


def test_build_description_only_link(berlin_tz):
    meeting = _make_meeting(description="")
    desc = build_google_calendar_description(meeting, bot_username="TestBot")
    assert desc == "Meeting in bot: https://t.me/TestBot?start=m_abc123token"


def test_build_description_strips_html_links(berlin_tz):
    meeting = _make_meeting(
        description='<a href="https://example.com">подробнее</a>',
    )
    desc = build_google_calendar_description(meeting, bot_username="TestBot")
    assert "подробнее (https://example.com)" in desc


def test_build_url_strips_html_location(berlin_tz):
    meeting = _make_meeting(
        location='<a href="https://maps.example.com">Cafe</a>',
    )
    url = build_google_calendar_event_url(meeting, local_tz=berlin_tz)
    params = parse_qs(urlparse(url).query)
    assert params["location"] == ["Cafe (https://maps.example.com)"]


def test_can_offer_google_calendar():
    assert can_offer_google_calendar(is_host=True, is_participant=False) is True
    assert can_offer_google_calendar(is_host=False, is_participant=True) is True
    assert can_offer_google_calendar(is_host=True, is_participant=True) is True
    assert can_offer_google_calendar(is_host=False, is_participant=False) is False


def test_google_calendar_keyboard_label():
    keyboard = google_calendar_keyboard("https://example.com", lang="en")
    button = keyboard.inline_keyboard[0][0]
    assert button.text == gcal_button_label("en")
    assert button.url == "https://example.com"


def test_build_calendar_offer(berlin_tz):
    meeting = _make_meeting()
    offer = build_calendar_offer(
        meeting,
        local_tz=berlin_tz,
        bot_username="TestBot",
        lang="en",
    )
    assert offer is not None
    text, keyboard = offer
    assert text == gcal_disclaimer("en")
    assert keyboard.inline_keyboard[0][0].url is not None


def test_build_calendar_offer_returns_none_without_start(berlin_tz):
    meeting = _make_meeting(start_at_utc=None)
    assert build_calendar_offer(meeting, local_tz=berlin_tz) is None
