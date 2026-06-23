"""Tests for host meeting cancellation flow."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import create_host, create_meeting, make_app, make_callback_update, make_context


@pytest.fixture
async def app(db, waitlist):
    scheduler = type("S", (), {})()
    scheduler.on_participant_change = AsyncMock()
    scheduler.plan_urgent_announcement = AsyncMock()
    scheduler.run_announcement_now = AsyncMock()
    scheduler._bot = AsyncMock()
    return make_app(db, waitlist, scheduler=scheduler)


@pytest.mark.asyncio
async def test_cb_cancel_meeting_shows_confirmation(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id)
    update = make_callback_update(f"cancel:{meeting_id}", user_id=host_id)
    await app.cb_cancel_meeting(update, make_context())
    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "Отмена встречи" in text
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    callbacks = [
        btn.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for btn in row
    ]
    assert f"cancel_confirm:{meeting_id}" in callbacks
    assert f"cancel_abort:{meeting_id}" in callbacks


@pytest.mark.asyncio
async def test_cb_cancel_confirm_cancels_meeting(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id)
    update = make_callback_update(f"cancel_confirm:{meeting_id}", user_id=host_id)
    with patch("bot.handlers.notify_participants", new=AsyncMock()) as notify:
        await app.cb_cancel_confirm(update, make_context())
    meeting = await db.get_meeting(meeting_id)
    assert meeting.canceled_at is not None
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_cancel_abort_keeps_meeting(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id)
    update = make_callback_update(f"cancel_abort:{meeting_id}", user_id=host_id)
    await app.cb_cancel_abort(update, make_context())
    meeting = await db.get_meeting(meeting_id)
    assert meeting.canceled_at is None
    assert "отклонена" in update.callback_query.edit_message_text.await_args.args[0]


@pytest.mark.asyncio
async def test_cb_cancel_meeting_rejects_non_host(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id)
    update = make_callback_update(f"cancel:{meeting_id}", user_id=99)
    await app.cb_cancel_meeting(update, make_context())
    assert "Только автор" in update.callback_query.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_cb_cancel_meeting_already_canceled(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id)
    await db.cancel_meeting(meeting_id, datetime.now(timezone.utc))
    update = make_callback_update(f"cancel:{meeting_id}", user_id=host_id)
    await app.cb_cancel_meeting(update, make_context())
    assert "уже отменена" in update.callback_query.message.reply_text.await_args.args[0]
