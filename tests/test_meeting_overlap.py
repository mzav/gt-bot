"""Tests for meeting time overlap detection."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.meeting_overlap import find_overlapping_meetings, meetings_overlap
from tests.conftest import create_host, create_meeting


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_same_day_non_overlapping_meetings(db, local_tz, now_utc):
    host_id = await create_host(db)
    meeting_a_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 8),
        end_at_utc=_utc(2026, 7, 1, 10),
    )
    meeting_b_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 12),
        end_at_utc=_utc(2026, 7, 1, 14),
    )
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_a_id, 10)

    meeting_a = await db.get_meeting(meeting_a_id)
    meeting_b = await db.get_meeting(meeting_b_id)

    assert not meetings_overlap(meeting_a, meeting_b, local_tz)
    overlaps = await find_overlapping_meetings(db, 10, meeting_b, local_tz, now_utc)
    assert overlaps == []


@pytest.mark.asyncio
async def test_overlap_with_participant_meeting(db, local_tz, now_utc):
    host_id = await create_host(db)
    meeting_a_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 8),
        end_at_utc=_utc(2026, 7, 1, 12),
    )
    meeting_b_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 10),
        end_at_utc=_utc(2026, 7, 1, 14),
    )
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_a_id, 10)

    meeting_b = await db.get_meeting(meeting_b_id)
    overlaps = await find_overlapping_meetings(db, 10, meeting_b, local_tz, now_utc)
    assert len(overlaps) == 1
    assert overlaps[0].id == meeting_a_id


@pytest.mark.asyncio
async def test_overlap_with_host_meeting(db, local_tz, now_utc):
    await db.get_or_create_user(10, "Host Guest", "hostguest")
    meeting_a_id = await create_meeting(
        db,
        10,
        start_at_utc=_utc(2026, 7, 1, 8),
        end_at_utc=_utc(2026, 7, 1, 12),
    )
    other_host = await create_host(db, user_id=2)
    meeting_b_id = await create_meeting(
        db,
        other_host,
        start_at_utc=_utc(2026, 7, 1, 10),
        end_at_utc=_utc(2026, 7, 1, 14),
    )

    meeting_b = await db.get_meeting(meeting_b_id)
    overlaps = await find_overlapping_meetings(db, 10, meeting_b, local_tz, now_utc)
    assert len(overlaps) == 1
    assert overlaps[0].id == meeting_a_id


@pytest.mark.asyncio
async def test_open_ended_meeting_treated_as_all_day(db, local_tz, now_utc):
    host_id = await create_host(db)
    meeting_a_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 8),
        end_at_utc=None,
    )
    meeting_b_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 20),
        end_at_utc=_utc(2026, 7, 1, 22),
    )
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_a_id, 10)

    meeting_b = await db.get_meeting(meeting_b_id)
    overlaps = await find_overlapping_meetings(db, 10, meeting_b, local_tz, now_utc)
    assert len(overlaps) == 1
    assert overlaps[0].id == meeting_a_id


@pytest.mark.asyncio
async def test_cancelled_meeting_ignored(db, local_tz, now_utc):
    host_id = await create_host(db)
    meeting_a_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 8),
        end_at_utc=_utc(2026, 7, 1, 12),
    )
    meeting_b_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 10),
        end_at_utc=_utc(2026, 7, 1, 14),
    )
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_a_id, 10)
    await db.cancel_meeting(meeting_a_id, now_utc)

    meeting_b = await db.get_meeting(meeting_b_id)
    overlaps = await find_overlapping_meetings(db, 10, meeting_b, local_tz, now_utc)
    assert overlaps == []


@pytest.mark.asyncio
async def test_past_meeting_ignored(db, local_tz):
    host_id = await create_host(db)
    meeting_a_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 6, 1, 8),
        end_at_utc=_utc(2026, 6, 1, 12),
    )
    meeting_b_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 10),
        end_at_utc=_utc(2026, 7, 1, 14),
    )
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_a_id, 10)

    now_utc = _utc(2026, 6, 15, 12)
    meeting_b = await db.get_meeting(meeting_b_id)
    overlaps = await find_overlapping_meetings(db, 10, meeting_b, local_tz, now_utc)
    assert overlaps == []


@pytest.mark.asyncio
async def test_target_meeting_excluded_from_overlap(db, local_tz, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_utc(2026, 7, 1, 8),
        end_at_utc=_utc(2026, 7, 1, 12),
    )
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    meeting = await db.get_meeting(meeting_id)
    overlaps = await find_overlapping_meetings(db, 10, meeting, local_tz, now_utc)
    assert overlaps == []
