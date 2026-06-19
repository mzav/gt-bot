"""Tests for remaining handler callbacks and commands."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import (
    create_host,
    create_meeting,
    fill_meeting,
    make_app,
    make_callback_update,
    make_command_update,
    make_context,
    make_message_update,
)


@pytest.fixture
async def app(db, waitlist):
    scheduler = MagicMock()
    scheduler.on_participant_change = AsyncMock()
    scheduler.maybe_announce_new_meeting = AsyncMock()
    scheduler.run_announcement_now = AsyncMock()
    scheduler._bot = MagicMock()
    return make_app(db, waitlist, scheduler=scheduler)


@pytest.mark.asyncio
async def test_cmd_register_starts_flow(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    update = make_message_update("", user_id=10)
    context = make_context()
    context.args = [str(meeting_id)]
    await app.cmd_register(update, context)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Перед записью" in text


@pytest.mark.asyncio
async def test_cmd_register_missing_id(app, db):
    update = make_message_update("", user_id=10)
    context = make_context()
    context.args = []
    await app.cmd_register(update, context)
    assert "Usage" in update.effective_message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_unregister(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    update = make_message_update("", user_id=10)
    context = make_context()
    context.args = [str(meeting_id)]
    await app.cmd_unregister(update, context)
    assert not await db.is_registered(meeting_id, 10)
    app.scheduler.on_participant_change.assert_awaited()


@pytest.mark.asyncio
async def test_cb_waitlist_join(app, db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [2])
    await db.get_or_create_user(10, "Guest", "guest")
    update = make_callback_update(f"waitlist_join:{meeting_id}", user_id=10)
    await app.cb_waitlist_join(update, make_context())
    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "waitlist" in text.lower() or "Waitlist" in text or "очеред" in text.lower()


@pytest.mark.asyncio
async def test_cb_waitlist_cancel(app, db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [2])
    await db.get_or_create_user(10, "Guest", "guest")
    await waitlist.join_waitlist(meeting_id, 10, now_utc)
    update = make_callback_update(f"waitlist_cancel:{meeting_id}", user_id=10)
    await app.cb_waitlist_cancel(update, make_context())
    update.callback_query.message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_complete_registration_sends_gcal_offer(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    db_user = await db.get_user(10)
    message = MagicMock()
    message.reply_text = AsyncMock()
    await app._complete_registration(message, meeting_id, 10, db_user)
    assert message.reply_text.await_count >= 2


@pytest.mark.asyncio
async def test_cb_offer_decline(app, db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [2])
    await db.get_or_create_user(10, "Guest", "guest")
    await waitlist.join_waitlist(meeting_id, 10, now_utc)
    await db.unregister(meeting_id, 2)
    offers = await waitlist.process_available_spots(meeting_id, now_utc)
    assert offers
    entry_id = offers[0].entry.id
    update = make_callback_update(f"offer_decline:{entry_id}", user_id=10)
    await app.cb_offer_decline(update, make_context())
    update.callback_query.message.reply_text.assert_awaited()
