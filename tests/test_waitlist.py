"""Waitlist feature tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.meeting_actions import resolve_meeting_actions
from bot.models import WaitlistStatus
from tests.conftest import create_host, create_meeting, fill_meeting


@pytest.mark.asyncio
async def test_join_waitlist_when_full(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [10, 11])
    await db.get_or_create_user(12, "Wait User", "wait")

    result = await waitlist.join_waitlist(meeting_id, 12, now_utc)
    assert result.ok
    assert result.position == 1
    entry = await db.get_active_waitlist_entry(meeting_id, 12)
    assert entry is not None
    assert entry.status == WaitlistStatus.WAITING


@pytest.mark.asyncio
async def test_prevent_duplicate_waitlist(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [10])
    await db.get_or_create_user(11, "Wait User", "wait")

    first = await waitlist.join_waitlist(meeting_id, 11, now_utc)
    second = await waitlist.join_waitlist(meeting_id, 11, now_utc)
    assert first.ok
    assert not second.ok


@pytest.mark.asyncio
async def test_cancel_waitlist(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [10])
    await db.get_or_create_user(11, "Wait User", "wait")
    await waitlist.join_waitlist(meeting_id, 11, now_utc)

    result = await waitlist.cancel_waitlist(meeting_id, 11)
    assert result.ok
    assert await db.get_active_waitlist_entry(meeting_id, 11) is None


@pytest.mark.asyncio
async def test_participant_cannot_join_waitlist(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [10, 11])

    result = await waitlist.join_waitlist(meeting_id, 10, now_utc)
    assert not result.ok


@pytest.mark.asyncio
async def test_waitlist_member_cannot_register(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [10])
    await db.get_or_create_user(11, "Wait User", "wait")
    await waitlist.join_waitlist(meeting_id, 11, now_utc)

    ok, msg, status = await db.register(meeting_id, 11)
    assert not ok
    assert status is None


@pytest.mark.asyncio
async def test_available_spots_decrements_on_offer(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [10, 11])
    await db.get_or_create_user(12, "Wait User", "wait")
    await waitlist.join_waitlist(meeting_id, 12, now_utc)

    assert await db.available_spots(meeting_id) == 0
    await db.unregister(meeting_id, 10)
    assert await db.available_spots(meeting_id) == 1

    offers = await waitlist.process_available_spots(meeting_id, now_utc)
    assert len(offers) == 1
    assert await db.available_spots(meeting_id) == 0


@pytest.mark.asyncio
async def test_unregister_triggers_offer(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [10, 11])
    await db.get_or_create_user(12, "Wait User", "wait")
    await waitlist.join_waitlist(meeting_id, 12, now_utc)

    await db.unregister(meeting_id, 10)
    offers = await waitlist.process_available_spots(meeting_id, now_utc)
    assert len(offers) == 1
    assert offers[0].user_id == 12
    entry = await db.get_waitlist_entry(offers[0].entry.id)
    assert entry.status == WaitlistStatus.OFFERED


@pytest.mark.asyncio
async def test_accept_offer_registers_user(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [10, 11])
    await db.get_or_create_user(12, "Wait User", "wait")
    await waitlist.join_waitlist(meeting_id, 12, now_utc)
    await db.unregister(meeting_id, 10)
    offers = await waitlist.process_available_spots(meeting_id, now_utc)

    result = await waitlist.accept_offer(offers[0].entry.id, 12, now_utc)
    assert result.ok
    assert await db.is_registered(meeting_id, 12)
    entry = await db.get_waitlist_entry(offers[0].entry.id)
    assert entry.status == WaitlistStatus.ACCEPTED


@pytest.mark.asyncio
async def test_decline_offer_moves_to_next(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [10])
    await db.get_or_create_user(11, "Wait One", "w1")
    await db.get_or_create_user(12, "Wait Two", "w2")
    await waitlist.join_waitlist(meeting_id, 11, now_utc)
    await waitlist.join_waitlist(meeting_id, 12, now_utc)

    await db.unregister(meeting_id, 10)
    offers = await waitlist.process_available_spots(meeting_id, now_utc)
    assert offers[0].user_id == 11

    result = await waitlist.decline_offer(offers[0].entry.id, 11, now_utc)
    assert result.ok
    assert len(result.notifications) == 1
    assert result.notifications[0].user_id == 12


@pytest.mark.asyncio
async def test_expired_offer_cannot_be_accepted(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [10])
    await db.get_or_create_user(11, "Wait User", "wait")
    await waitlist.join_waitlist(meeting_id, 11, now_utc)
    await db.unregister(meeting_id, 10)
    offers = await waitlist.process_available_spots(meeting_id, now_utc)

    late = now_utc + timedelta(hours=7)
    result = await waitlist.accept_offer(offers[0].entry.id, 11, late)
    assert not result.ok
    assert not await db.is_registered(meeting_id, 11)


@pytest.mark.asyncio
async def test_expiration_job_continues_queue(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [10])
    await db.get_or_create_user(11, "Wait One", "w1")
    await db.get_or_create_user(12, "Wait Two", "w2")
    await waitlist.join_waitlist(meeting_id, 11, now_utc)
    await waitlist.join_waitlist(meeting_id, 12, now_utc)

    await db.unregister(meeting_id, 10)
    offers = await waitlist.process_available_spots(meeting_id, now_utc)
    entry_id = offers[0].entry.id

    async with db.session() as s:
        from bot.models import WaitlistEntry
        entry = await s.get(WaitlistEntry, entry_id)
        entry.offer_expires_at = now_utc - timedelta(minutes=1)
        await s.commit()

    expired, new_offers = await waitlist.expire_stale_offers(now_utc)
    assert len(expired) == 1
    assert len(new_offers) == 1
    assert new_offers[0].user_id == 12
    entry = await db.get_waitlist_entry(entry_id)
    assert entry.status == WaitlistStatus.EXPIRED


@pytest.mark.asyncio
async def test_canceled_meeting_blocks_join(db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [10])
    await db.cancel_meeting(meeting_id, now_utc)
    await db.get_or_create_user(11, "Wait User", "wait")

    result = await waitlist.join_waitlist(meeting_id, 11, now_utc)
    assert not result.ok


def test_keyboard_states():
    assert resolve_meeting_actions(
        is_host=True, is_participant=False, available=0,
        waitlist_state=None, include_register=True,
    ) == "host"
    assert resolve_meeting_actions(
        is_host=False, is_participant=True, available=5,
        waitlist_state=None, include_register=True,
    ) == "participant"
    assert resolve_meeting_actions(
        is_host=False, is_participant=False, available=3,
        waitlist_state=None, include_register=True,
    ) == "register"
    assert resolve_meeting_actions(
        is_host=False, is_participant=False, available=0,
        waitlist_state=None, include_register=True,
    ) == "waitlist_join"
    assert resolve_meeting_actions(
        is_host=False, is_participant=False, available=0,
        waitlist_state="waiting", include_register=True,
    ) == "waitlist_waiting"
    assert resolve_meeting_actions(
        is_host=False, is_participant=False, available=0,
        waitlist_state="offered", include_register=True,
    ) == "waitlist_offered"
