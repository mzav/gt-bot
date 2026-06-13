"""Tests for registration start time gating."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dateutil import tz

from bot.messages import REGISTRATION_SUCCESS
from bot.storage import Database, is_registration_open
from tests.conftest import create_host, create_meeting


@pytest.fixture
def berlin_tz():
    return tz.gettz("Europe/Berlin")


async def test_is_registration_open_when_null(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert is_registration_open(meeting, now) is True


async def test_is_registration_open_before_start(db):
    host_id = await create_host(db)
    start = datetime(2026, 7, 1, 18, 0, 0, tzinfo=timezone.utc)
    reg_start = datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc)
    meeting_id = await create_meeting(
        db, host_id, start_at_utc=start, registration_starts_at_utc=reg_start
    )
    meeting = await db.get_meeting(meeting_id)
    before = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert is_registration_open(meeting, before) is False
    after = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
    assert is_registration_open(meeting, after) is True


async def test_register_blocked_before_registration_opens(db, berlin_tz):
    host_id = await create_host(db)
    start = datetime(2026, 7, 1, 18, 0, 0, tzinfo=timezone.utc)
    reg_start = datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)
    meeting_id = await create_meeting(
        db, host_id, start_at_utc=start, registration_starts_at_utc=reg_start
    )
    await db.get_or_create_user(2, "User Two", "user2")

    from unittest.mock import patch

    now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    with patch("bot.storage.datetime") as mock_dt:
        mock_dt.now.return_value = now
        ok, msg, status = await db.register(meeting_id, 2, local_tz=berlin_tz)
    assert ok is False
    assert status is None
    assert "Регистрация откроется" in msg


async def test_register_allowed_after_registration_opens(db, berlin_tz):
    host_id = await create_host(db)
    start = datetime(2026, 7, 1, 18, 0, 0, tzinfo=timezone.utc)
    reg_start = datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc)
    meeting_id = await create_meeting(
        db, host_id, start_at_utc=start, registration_starts_at_utc=reg_start
    )
    await db.get_or_create_user(2, "User Two", "user2")

    from unittest.mock import patch

    now = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
    with patch("bot.storage.datetime") as mock_dt:
        mock_dt.now.return_value = now
        ok, msg, status = await db.register(meeting_id, 2, local_tz=berlin_tz)
    assert ok is True
    assert msg == REGISTRATION_SUCCESS


async def test_list_upcoming_visible_hides_closed_for_non_host(db):
    host_id = await create_host(db, user_id=1)
    start = datetime(2026, 8, 1, 18, 0, 0, tzinfo=timezone.utc)
    reg_start = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
    meeting_id = await create_meeting(
        db, host_id, start_at_utc=start, registration_starts_at_utc=reg_start
    )
    now = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)

    hidden = await db.list_upcoming_meetings_visible(now, viewer_user_id=999)
    assert meeting_id not in [m.id for m in hidden]

    visible_host = await db.list_upcoming_meetings_visible(now, viewer_user_id=1)
    assert meeting_id in [m.id for m in visible_host]
