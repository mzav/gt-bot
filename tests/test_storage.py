"""Tests for storage layer CRUD and edge cases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from bot.models import Meeting, RegistrationStatus
from tests.conftest import create_host, create_meeting, fill_meeting


async def _clear_public_token(db, meeting_id: int) -> None:
    async with db.session() as s:
        await s.execute(
            update(Meeting).where(Meeting.id == meeting_id).values(public_token=None)
        )
        await s.commit()


@pytest.mark.asyncio
async def test_backfill_meeting_public_tokens(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await _clear_public_token(db, meeting_id)
    meeting = await db.get_meeting(meeting_id)
    assert meeting.public_token is None

    count = await db.backfill_meeting_public_tokens()
    assert count == 1
    meeting = await db.get_meeting(meeting_id)
    assert meeting.public_token is not None

    assert await db.backfill_meeting_public_tokens() == 0


@pytest.mark.asyncio
async def test_update_meeting_fields(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    new_start = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    new_end = datetime(2026, 8, 1, 14, 0, 0, tzinfo=timezone.utc)
    reg_start = datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc)

    meeting = await db.update_meeting(
        meeting_id,
        topic="Updated",
        description="New desc",
        location="Munich",
        max_participants=10,
        start_at_utc=new_start,
        end_at_utc=new_end,
        registration_starts_at_utc=reg_start,
        photo_file_id="photo123",
    )
    assert meeting.topic == "Updated"
    assert meeting.description == "New desc"
    assert meeting.location == "Munich"
    assert meeting.max_participants == 10
    assert meeting.end_at_utc.replace(tzinfo=timezone.utc) == new_end
    assert meeting.registration_starts_at_utc.replace(tzinfo=timezone.utc) == reg_start
    assert meeting.photo_file_id == "photo123"


@pytest.mark.asyncio
async def test_update_meeting_clear_location(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.update_meeting(meeting_id, clear_location=True)
    assert meeting.location is None


@pytest.mark.asyncio
async def test_update_meeting_clear_photo_and_reg_start(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(
        db,
        host_id,
        registration_starts_at_utc=datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc),
    )
    await db.update_meeting(meeting_id, photo_file_id="pic")
    meeting = await db.update_meeting(
        meeting_id,
        clear_photo=True,
        clear_registration_start=True,
    )
    assert meeting.photo_file_id is None
    assert meeting.registration_starts_at_utc is None


@pytest.mark.asyncio
async def test_unregister_persists_reason(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    cancelled_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    ok, _ = await db.unregister(
        meeting_id,
        10,
        reason_type="ill",
        cancelled_at=cancelled_at,
    )
    assert ok
    reg = await db.get_canceled_registration(meeting_id, 10)
    assert reg is not None
    assert reg.cancellation_reason_type == "ill"
    assert reg.cancelled_at.replace(tzinfo=timezone.utc) == cancelled_at


@pytest.mark.asyncio
async def test_list_upcoming_visible_hides_closed_for_guest(db):
    host_id = await create_host(db)
    future_reg = datetime.now(timezone.utc) + timedelta(days=2)
    meeting_id = await create_meeting(
        db,
        host_id,
        registration_starts_at_utc=future_reg,
    )
    now = datetime.now(timezone.utc)
    guest_visible = await db.list_upcoming_meetings_visible(now, viewer_user_id=99)
    assert not guest_visible

    host_visible = await db.list_upcoming_meetings_visible(now, viewer_user_id=host_id)
    assert len(host_visible) == 1
    assert host_visible[0].id == meeting_id


@pytest.mark.asyncio
async def test_list_user_meetings_excludes_canceled_registration(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    await db.unregister(meeting_id, 10)
    now = datetime.now(timezone.utc)
    meetings = await db.list_user_meetings(10, now)
    assert meetings == []


@pytest.mark.asyncio
async def test_cancel_meeting_blocks_register(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.cancel_meeting(meeting_id, datetime.now(timezone.utc))
    await db.get_or_create_user(10, "Guest", "guest")
    ok, msg, _ = await db.register(meeting_id, 10)
    assert not ok
    assert "canceled" in msg.lower()


@pytest.mark.asyncio
async def test_count_hosts_and_available_spots(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    assert await db.count_hosts(meeting_id) == 1
    assert await db.available_spots(meeting_id) == 2

    await fill_meeting(db, meeting_id, [2])
    assert await db.available_spots(meeting_id) == 1

    await fill_meeting(db, meeting_id, [3])
    assert await db.available_spots(meeting_id) == 0

    await db.get_or_create_user(4, "Wait", "wait")
    await waitlist.join_waitlist(meeting_id, 4, now_utc)
    assert await db.available_spots(meeting_id) == 0


@pytest.mark.asyncio
async def test_participant_reminder_dedup(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    sent_at = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    assert not await db.has_participant_reminder(meeting_id, 10, 3)
    await db.record_participant_reminder(meeting_id, 10, 3, sent_at)
    assert await db.has_participant_reminder(meeting_id, 10, 3)
