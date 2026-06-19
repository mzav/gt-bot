"""Tests for meeting deep links and /start routing."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from tests.conftest import (
    TEST_CHANNEL_ID,
    create_host,
    create_meeting,
    make_app,
    make_command_update,
    make_context,
    make_message_update,
)


@pytest.fixture
async def app(db, waitlist):
    return make_app(db, waitlist)


@pytest.mark.asyncio
async def test_cmd_start_without_payload_shows_welcome(app, db):
    update = make_message_update("/start", user_id=10)
    context = make_context()
    context.args = []
    await app.cmd_start(update, context)
    update.effective_message.reply_text.assert_awaited()
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Girl Talk" in text or "встреч" in text.lower()


@pytest.mark.asyncio
async def test_cmd_start_with_valid_token_shows_meeting(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    update, context = make_command_update(f"m_{meeting.public_token}", user_id=10)
    await db.get_or_create_user(10, "Guest", "guest")
    await app.cmd_start(update, context)
    assert update.effective_message.reply_text.await_count >= 1
    text = update.effective_message.reply_text.await_args.args[0]
    assert meeting.topic in text


@pytest.mark.asyncio
async def test_cmd_start_invalid_token(app, db):
    update, context = make_command_update("m_bad!!!", user_id=10)
    await app.cmd_start(update, context)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Неверная ссылка" in text


@pytest.mark.asyncio
async def test_deep_link_unknown_token(app, db):
    update, context = make_command_update("m_unknownToken1", user_id=10)
    await app.cmd_start(update, context)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "не найдена" in text


@pytest.mark.asyncio
async def test_deep_link_canceled_meeting(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    await db.cancel_meeting(meeting_id, datetime.now(timezone.utc))
    update, context = make_command_update(f"m_{meeting.public_token}", user_id=10)
    await app.cmd_start(update, context)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "отменена" in text


@pytest.mark.asyncio
async def test_deep_link_past_meeting(app, db):
    host_id = await create_host(db)
    past = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    meeting_id = await create_meeting(db, host_id, start_at_utc=past)
    meeting = await db.get_meeting(meeting_id)
    update, context = make_command_update(f"m_{meeting.public_token}", user_id=10)
    await app.cmd_start(update, context)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "прошла" in text


@pytest.mark.asyncio
async def test_deep_link_host_keyboard(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id)
    meeting = await db.get_meeting(meeting_id)
    update, context = make_command_update(f"m_{meeting.public_token}", user_id=host_id)
    await app.cmd_start(update, context)
    kwargs = update.effective_message.reply_text.await_args.kwargs
    callbacks = [
        btn.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for btn in row
    ]
    assert any(c.startswith("edit:") for c in callbacks)
    assert any(c.startswith("cancel:") for c in callbacks)
