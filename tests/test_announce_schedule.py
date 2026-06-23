"""Tests for urgent announcement scheduling helpers."""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest
from dateutil import tz

from bot.announce_schedule import (
    announcement_window,
    compute_urgent_announce_at,
    covered_by_future_announcement,
    reg_start_at_announce_time,
)


@pytest.fixture
def berlin_tz():
    return tz.gettz("Europe/Berlin")


def test_announcement_window_first_day_of_month():
    today = date(2026, 6, 1)
    from_date, to_date = announcement_window([1, 15], today)
    assert from_date == date(2026, 6, 1)
    assert to_date == date(2026, 6, 30)


def test_announcement_window_subsequent_day():
    today = date(2026, 6, 15)
    from_date, to_date = announcement_window([1, 15], today)
    assert from_date == date(2026, 6, 18)
    assert to_date == date(2026, 6, 30)


def test_covered_by_future_announcement():
    today = date(2026, 6, 10)
    meeting_date = date(2026, 6, 25)
    assert covered_by_future_announcement(meeting_date, today, [1, 15]) is True


def test_covered_by_future_announcement_same_day_not_counted():
    today = date(2026, 6, 25)
    meeting_date = date(2026, 6, 25)
    assert covered_by_future_announcement(meeting_date, today, [1, 15]) is False


def test_reg_start_at_announce_time(berlin_tz):
    reg_day = date(2026, 6, 24)
    announce_time = time(10, 0)
    reg_utc = reg_start_at_announce_time(reg_day, announce_time, berlin_tz)
    local = reg_utc.astimezone(berlin_tz)
    assert local.date() == reg_day
    assert local.hour == 10
    assert local.minute == 0


def test_compute_urgent_announce_skips_digest(berlin_tz):
    at = compute_urgent_announce_at(
        meeting_start_at_utc=datetime(2026, 7, 5, 18, 0, 0, tzinfo=timezone.utc),
        registration_starts_at_utc=None,
        reference_date=date(2026, 6, 23),
        announce_days=[1, 15],
        created_at_utc=datetime(2026, 6, 23, 8, 0, 0, tzinfo=timezone.utc),
        local_tz=berlin_tz,
    )
    assert at is None


def test_compute_urgent_announce_immediate(berlin_tz):
    created = datetime(2026, 6, 23, 8, 0, 0, tzinfo=timezone.utc)
    at = compute_urgent_announce_at(
        meeting_start_at_utc=datetime(2026, 6, 25, 18, 0, 0, tzinfo=timezone.utc),
        registration_starts_at_utc=None,
        reference_date=date(2026, 6, 23),
        announce_days=[1, 15],
        created_at_utc=created,
        local_tz=berlin_tz,
    )
    assert at == created


def test_compute_urgent_announce_deferred_to_reg_start(berlin_tz):
    reg_start = reg_start_at_announce_time(date(2026, 6, 24), time(10, 0), berlin_tz)
    at = compute_urgent_announce_at(
        meeting_start_at_utc=datetime(2026, 6, 25, 18, 0, 0, tzinfo=timezone.utc),
        registration_starts_at_utc=reg_start,
        reference_date=date(2026, 6, 23),
        announce_days=[1, 15],
        created_at_utc=datetime(2026, 6, 23, 8, 0, 0, tzinfo=timezone.utc),
        local_tz=berlin_tz,
    )
    assert at == reg_start
