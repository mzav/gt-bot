"""Registration flow regression tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.messages import REGISTRATION_SUCCESS
from bot.models import RegistrationStatus
from tests.conftest import create_host, create_meeting, fill_meeting


@pytest.mark.asyncio
async def test_register_when_spots_available(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")

    ok, msg, status = await db.register(meeting_id, 10)
    assert ok
    assert status == RegistrationStatus.CONFIRMED
    assert msg == REGISTRATION_SUCCESS
    assert await db.is_registered(meeting_id, 10)


@pytest.mark.asyncio
async def test_register_fails_when_full(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [10, 11])
    await db.get_or_create_user(12, "Late Guest", "late")

    ok, msg, status = await db.register(meeting_id, 12)
    assert not ok
    assert status is None
    assert not await db.is_registered(meeting_id, 12)


@pytest.mark.asyncio
async def test_list_user_meetings_confirmed_only(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [10])
    now_utc = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    meetings = await db.list_user_meetings(10, now_utc)
    assert len(meetings) == 1
    assert meetings[0].id == meeting_id


@pytest.mark.asyncio
async def test_is_registered_confirmed_only(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await db.get_or_create_user(10, "Guest", "guest")

    assert not await db.is_registered(meeting_id, 10)
    await db.register(meeting_id, 10)
    assert await db.is_registered(meeting_id, 10)
