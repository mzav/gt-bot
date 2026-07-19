"""Tests for iCalendar feed generation."""
from __future__ import annotations

from datetime import datetime, timezone

from bot.google_calendar import DEFAULT_MEETING_DURATION
from bot.ical_feed import (
    build_community_ical_feed,
    build_vevent,
    escape_ical_text,
    fold_ical_line,
    meeting_event_uid,
)
from bot.models import Meeting


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
        "updated_at": datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return Meeting(**defaults)


NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_escape_ical_text_special_characters():
    assert escape_ical_text("a;b,c\\d\ne") == "a\\;b\\,c\\\\d\\ne"


def test_fold_ical_line_short():
    assert fold_ical_line("SHORT") == "SHORT"


def test_meeting_event_uid_uses_public_token():
    assert meeting_event_uid("abc123token") == "meeting-abc123token@girltalkberlin.bot"


def test_build_community_ical_feed_structure():
    meeting = _make_meeting()
    feed = build_community_ical_feed([meeting], bot_username="TestBot", now_utc=NOW)
    assert feed.startswith("BEGIN:VCALENDAR\r\n")
    assert "VERSION:2.0" in feed
    assert "BEGIN:VEVENT" in feed
    assert "END:VEVENT" in feed
    assert feed.endswith("END:VCALENDAR\r\n")


def test_build_vevent_uid_stable_after_edit():
    meeting = _make_meeting()
    uid_before = meeting_event_uid(meeting.public_token)
    meeting.topic = "Updated Topic"
    meeting.start_at_utc = datetime(2026, 7, 2, 18, 0, 0, tzinfo=timezone.utc)
    event = build_vevent(meeting, bot_username="TestBot", now_utc=NOW)
    assert event is not None
    assert f"UID:{uid_before}" in event
    assert "SUMMARY:Updated Topic" in event


def test_build_vevent_end_fallback_two_hours():
    meeting = _make_meeting(end_at_utc=None)
    event = build_vevent(meeting, bot_username="TestBot", now_utc=NOW)
    assert event is not None
    assert "DTSTART:20260701T160000Z" in event
    assert "DTEND:20260701T180000Z" in event
    assert DEFAULT_MEETING_DURATION.total_seconds() == 2 * 3600


def test_build_vevent_uses_meeting_end_at_utc():
    meeting = _make_meeting(
        end_at_utc=datetime(2026, 7, 1, 19, 30, 0, tzinfo=timezone.utc),
    )
    event = build_vevent(meeting, bot_username="TestBot", now_utc=NOW)
    assert event is not None
    assert "DTEND:20260701T193000Z" in event


def test_build_vevent_includes_all_fields():
    meeting = _make_meeting()
    event = build_vevent(meeting, bot_username="TestBot", now_utc=NOW)
    assert event is not None
    assert "SUMMARY:Coffee Chat" in event
    assert "DTSTART:20260701T160000Z" in event
    assert "LOCATION:Berlin Cafe" in event
    assert "Weekly catch-up" in event
    assert "https://t.me/TestBot?start=m_abc123token" in event
    assert "LAST-MODIFIED:20260601T100000Z" in event


def test_build_vevent_strips_html_location():
    meeting = _make_meeting(
        location='<a href="https://maps.example.com">Cafe</a>',
    )
    event = build_vevent(meeting, bot_username="TestBot", now_utc=NOW)
    assert event is not None
    assert "LOCATION:Cafe (https://maps.example.com)" in event


def test_build_vevent_skips_missing_topic():
    meeting = _make_meeting(topic="")
    assert build_vevent(meeting, bot_username="TestBot", now_utc=NOW) is None


def test_build_vevent_skips_missing_start():
    meeting = _make_meeting(start_at_utc=None)
    assert build_vevent(meeting, bot_username="TestBot", now_utc=NOW) is None


def test_build_vevent_skips_missing_public_token():
    meeting = _make_meeting(public_token=None)
    assert build_vevent(meeting, bot_username="TestBot", now_utc=NOW) is None


def test_build_community_ical_feed_omits_invalid_meetings():
    valid = _make_meeting()
    invalid = _make_meeting(topic="", public_token="bad")
    feed = build_community_ical_feed([valid, invalid], bot_username="TestBot", now_utc=NOW)
    assert feed.count("BEGIN:VEVENT") == 1
