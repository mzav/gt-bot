"""Tests for meeting time formatting and effective end time."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from dateutil import tz

from bot.google_calendar import effective_end_at_utc, DEFAULT_MEETING_DURATION
from bot.meeting_format import format_meeting_time
from bot.models import Meeting


@pytest.fixture
def berlin_tz():
    return tz.gettz("Europe/Berlin")


def _make_meeting(**overrides) -> Meeting:
    defaults = {
        "id": 1,
        "topic": "Test",
        "description": "Desc",
        "start_at_utc": datetime(2026, 7, 1, 16, 0, 0, tzinfo=timezone.utc),
        "max_participants": 5,
        "location": "Berlin",
        "created_by": 1,
    }
    defaults.update(overrides)
    return Meeting(**defaults)


def test_format_meeting_time_without_end(berlin_tz):
    meeting = _make_meeting()
    assert format_meeting_time(meeting, berlin_tz, style="iso") == "2026-07-01 18:00"
    assert format_meeting_time(meeting, berlin_tz, style="short") == "01.07.2026 18:00"


def test_format_meeting_time_with_end(berlin_tz):
    meeting = _make_meeting(
        end_at_utc=datetime(2026, 7, 1, 19, 30, 0, tzinfo=timezone.utc),
    )
    assert format_meeting_time(meeting, berlin_tz, style="iso") == "2026-07-01 18:00–21:30"
    assert format_meeting_time(meeting, berlin_tz, style="details_time") == "18:00–21:30 (Berlin time)"


def test_effective_end_at_utc_with_explicit_end():
    meeting = _make_meeting(
        end_at_utc=datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc),
    )
    assert effective_end_at_utc(meeting) == datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc)


def test_effective_end_at_utc_fallback():
    meeting = _make_meeting()
    end = effective_end_at_utc(meeting)
    expected = meeting.start_at_utc + DEFAULT_MEETING_DURATION
    assert end == expected
