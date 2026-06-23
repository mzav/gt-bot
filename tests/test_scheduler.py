"""Tests for scheduler pure functions and BotScheduler jobs."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from dateutil import tz

from bot.models import Meeting, User
from bot.scheduler import (
    BotScheduler,
    _announcement_window,
    _covered_by_future_announcement,
    _split_messages,
)
from tests.conftest import create_host, create_meeting


def test_announcement_window_first_day_of_month():
    today = date(2026, 6, 1)
    from_date, to_date = _announcement_window([1, 15], today)
    assert from_date == date(2026, 6, 1)
    assert to_date == date(2026, 6, 30)


def test_announcement_window_subsequent_day():
    today = date(2026, 6, 15)
    from_date, to_date = _announcement_window([1, 15], today)
    assert from_date == date(2026, 6, 18)
    assert to_date == date(2026, 6, 30)


def test_covered_by_future_announcement():
    today = date(2026, 6, 10)
    meeting_date = date(2026, 6, 25)
    assert _covered_by_future_announcement(meeting_date, today, [1, 15]) is True


def test_covered_by_future_announcement_same_day_not_counted():
    today = date(2026, 6, 25)
    meeting_date = date(2026, 6, 25)
    assert _covered_by_future_announcement(meeting_date, today, [1, 15]) is False


def test_split_messages():
    header = "Header\n"
    cards = ["A" * 100, "B" * 100]
    messages = _split_messages(header, cards, max_len=150)
    assert len(messages) >= 2
    assert all(len(m) <= 150 for m in messages)


def _make_scheduler(db, *, threshold=10, bot_username="TestBot", channel_id=-100):
    send_channel_card = AsyncMock()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    scheduler = BotScheduler(
        db=db,
        timezone=tz.gettz("Europe/Berlin"),
        send_channel_card=send_channel_card,
        bot=bot,
        notify_threshold=threshold,
        bot_username=bot_username,
    )
    scheduler._channel_id = channel_id
    scheduler._announce_days = [1, 15]
    return scheduler, send_channel_card, bot


@pytest.mark.asyncio
async def test_on_participant_change_instant_below_threshold(db):
    scheduler, _, bot = _make_scheduler(db, threshold=10)
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    user = await db.get_user(host_id)
    await scheduler.on_participant_change(meeting, user, "joined", confirmed_count=1)
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_participant_change_batches_above_threshold(db):
    scheduler, _, bot = _make_scheduler(db, threshold=2)
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    user = await db.get_user(host_id)
    await scheduler.on_participant_change(meeting, user, "joined", confirmed_count=5)
    bot.send_message.assert_not_awaited()
    assert meeting_id in scheduler._pending_events


@pytest.mark.asyncio
async def test_flush_pending_notifications(db):
    scheduler, _, bot = _make_scheduler(db, threshold=2)
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    user = await db.get_user(host_id)
    await scheduler.on_participant_change(meeting, user, "joined", confirmed_count=5)
    await scheduler._flush_pending_notifications()
    bot.send_message.assert_awaited_once()
    assert not scheduler._pending_events


@pytest.mark.asyncio
async def test_meeting_deep_link_and_cta_keyboard(db):
    scheduler, _, _ = _make_scheduler(db)
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    link = scheduler._meeting_deep_link(meeting)
    assert link == f"https://t.me/TestBot?start=m_{meeting.public_token}"
    keyboard = scheduler._meeting_cta_keyboard(meeting)
    assert keyboard is not None
    assert keyboard.inline_keyboard[0][0].url == link


@pytest.mark.asyncio
async def test_meeting_deep_link_missing_username(db):
    scheduler, _, _ = _make_scheduler(db, bot_username=None)
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    assert scheduler._meeting_deep_link(meeting) is None


@pytest.mark.asyncio
async def test_plan_urgent_announcement_publishes_immediately(db):
    scheduler, send_channel_card, _ = _make_scheduler(db, channel_id=-100)
    host_id = await create_host(db)
    meeting_id = await create_meeting(
        db,
        host_id,
        start_at_utc=datetime(2026, 12, 25, 18, 0, 0, tzinfo=timezone.utc),
    )
    meeting = await db.get_meeting(meeting_id)
    await scheduler.plan_urgent_announcement(meeting)
    send_channel_card.assert_awaited_once()
    updated = await db.get_meeting(meeting_id)
    assert updated.urgent_announce_posted_at_utc is not None


@pytest.mark.asyncio
async def test_plan_urgent_announcement_skips_digest(db):
    scheduler, send_channel_card, _ = _make_scheduler(db, channel_id=-100)
    host_id = await create_host(db)
    meeting_id = await create_meeting(
        db,
        host_id,
        start_at_utc=datetime(2026, 7, 5, 18, 0, 0, tzinfo=timezone.utc),
    )
    meeting = await db.get_meeting(meeting_id)
    from unittest.mock import patch

    with patch("bot.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 23, 12, 0, 0, tzinfo=scheduler._tz)
        await scheduler.plan_urgent_announcement(meeting)
    send_channel_card.assert_not_awaited()
    updated = await db.get_meeting(meeting_id)
    assert updated.urgent_announce_at_utc is None


@pytest.mark.asyncio
async def test_plan_urgent_announcement_defers_to_reg_start(db):
    scheduler, send_channel_card, _ = _make_scheduler(db, channel_id=-100)
    host_id = await create_host(db)
    berlin = scheduler._tz
    reg_start = datetime(2026, 6, 24, 8, 0, 0, tzinfo=timezone.utc)
    meeting_id = await create_meeting(
        db,
        host_id,
        start_at_utc=datetime(2026, 6, 25, 16, 0, 0, tzinfo=timezone.utc),
        registration_starts_at_utc=reg_start,
    )
    meeting = await db.get_meeting(meeting_id)
    from unittest.mock import patch

    with patch("bot.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 23, 12, 0, 0, tzinfo=berlin)
        await scheduler.plan_urgent_announcement(meeting)
    send_channel_card.assert_not_awaited()
    updated = await db.get_meeting(meeting_id)
    from bot.utils import ensure_utc
    assert ensure_utc(updated.urgent_announce_at_utc) == reg_start
    assert updated.urgent_announce_posted_at_utc is None


@pytest.mark.asyncio
async def test_urgent_announce_job_publishes_pending(db):
    scheduler, send_channel_card, _ = _make_scheduler(db, channel_id=-100)
    host_id = await create_host(db)
    meeting_id = await create_meeting(
        db,
        host_id,
        start_at_utc=datetime(2026, 6, 25, 16, 0, 0, tzinfo=timezone.utc),
    )
    due = datetime(2026, 6, 24, 8, 0, 0, tzinfo=timezone.utc)
    await db.update_meeting_urgent_announce_schedule(meeting_id, due)
    from unittest.mock import patch

    with patch("bot.scheduler.utc_now", return_value=due):
        await scheduler._urgent_announce_job()
    send_channel_card.assert_awaited_once()
    updated = await db.get_meeting(meeting_id)
    assert updated.urgent_announce_posted_at_utc is not None


@pytest.mark.asyncio
async def test_maybe_announce_new_meeting_sends(db):
    scheduler, send_channel_card, _ = _make_scheduler(db, channel_id=-100)
    host_id = await create_host(db)
    meeting_id = await create_meeting(
        db,
        host_id,
        start_at_utc=datetime(2026, 12, 25, 18, 0, 0, tzinfo=timezone.utc),
    )
    meeting = await db.get_meeting(meeting_id)
    await scheduler.maybe_announce_new_meeting(meeting)
    send_channel_card.assert_awaited_once()


@pytest.mark.asyncio
async def test_participant_reminders_job_runs(db):
    scheduler, _, _ = _make_scheduler(db)
    await scheduler._participant_reminders_job()


@pytest.mark.asyncio
async def test_waitlist_expiration_job(db, waitlist):
    scheduler, _, _ = _make_scheduler(db)
    scheduler._waitlist_service = waitlist
    from unittest.mock import patch

    with patch.object(
        waitlist, "expire_stale_offers", new=AsyncMock(return_value=([], []))
    ) as expire_mock, patch("bot.waitlist.send_offer_dms", new=AsyncMock()) as send_offers, patch(
        "bot.waitlist.send_expired_notices", new=AsyncMock()
    ) as send_expired:
        await scheduler._waitlist_expiration_job()
    expire_mock.assert_awaited_once()
    send_offers.assert_awaited_once_with(scheduler._bot, waitlist, [])
    send_expired.assert_awaited_once()
