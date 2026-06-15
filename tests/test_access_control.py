"""Tests for channel membership access control."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import User as TgUser
from telegram.error import BadRequest

from bot.config import Settings
from bot.handlers import BotApp
from bot.messages import RESTRICTED_ACCESS_MESSAGE
from tests.conftest import TEST_CHANNEL_ID, create_host, create_meeting, make_context


def _make_app(
    db,
    waitlist,
    *,
    admin_user_ids: list[int] | None = None,
    announcements_channel_id: int | None = TEST_CHANNEL_ID,
) -> BotApp:
    settings = Settings(
        telegram_bot_token="test-token",
        tz="Europe/Berlin",
        announcements_channel_id=announcements_channel_id,
        admin_user_ids=admin_user_ids or [],
    )
    scheduler = MagicMock()
    scheduler.on_participant_change = AsyncMock()
    return BotApp(settings, db, scheduler, waitlist)


def _make_callback_update(callback_data: str, *, user_id: int = 10):
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    message = MagicMock()
    message.reply_text = AsyncMock()
    cq = MagicMock()
    cq.data = callback_data
    cq.message = message
    cq.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = cq
    update.effective_user = user
    update.effective_message = None
    return update

def _make_text_update(text: str, *, user_id: int = 10):
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_message = message
    update.callback_query = None
    return update


def _make_start_update(payload: str | None = None, *, user_id: int = 10) -> tuple[MagicMock, MagicMock]:
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    message = MagicMock()
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_message = message
    update.callback_query = None
    context = make_context()
    context.args = [payload] if payload else []
    return update, context


@pytest.fixture
async def app(db, waitlist):
    return _make_app(db, waitlist)


@pytest.mark.asyncio
async def test_channel_member_can_register(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    context = make_context(status="member")

    update = _make_callback_update(f"register:{meeting_id}")
    await app._community_gated(app.cb_register)(update, context)

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "Перед записью" in text


@pytest.mark.asyncio
async def test_channel_member_can_list_meetings(app, db):
    host_id = await create_host(db)
    await create_meeting(db, host_id)
    context = make_context(status="member")
    update = _make_text_update("/upcoming")

    await app._community_gated(app.cmd_meetings)(update, context)

    update.effective_message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_admin_bypasses_membership_check(app, db):
    host_id = await create_host(db)
    await create_meeting(db, host_id)
    admin_app = _make_app(db, app.waitlist, admin_user_ids=[99])
    context = make_context(status="left")
    update = _make_text_update("/upcoming", user_id=99)

    await admin_app._community_gated(admin_app.cmd_meetings)(update, context)

    update.effective_message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_admin_force_summary_still_protected(app):
    admin_app = _make_app(MagicMock(), MagicMock(), admin_user_ids=[99])
    admin_app.settings.announcements_channel_id = TEST_CHANNEL_ID
    admin_app.scheduler.run_announcement_now = AsyncMock()
    context = make_context()
    update = _make_text_update("Force summary", user_id=1)
    message = update.effective_message

    await admin_app.cmd_force_summary(update, context)

    message.reply_text.assert_awaited_once_with("Not authorized.")


@pytest.mark.asyncio
async def test_admin_can_force_summary(app):
    admin_app = _make_app(MagicMock(), MagicMock(), admin_user_ids=[99])
    admin_app.settings.announcements_channel_id = TEST_CHANNEL_ID
    admin_app.scheduler.run_announcement_now = AsyncMock()
    context = make_context()
    update = _make_text_update("Force summary", user_id=99)
    message = update.effective_message

    await admin_app.cmd_force_summary(update, context)

    admin_app.scheduler.run_announcement_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_member_denied_meetings_command(app, db):
    host_id = await create_host(db)
    await create_meeting(db, host_id, topic="Secret Topic")
    context = make_context(status="left")
    update = _make_text_update("/upcoming")

    await app._community_gated(app.cmd_meetings)(update, context)

    text = update.effective_message.reply_text.await_args.args[0]
    assert text == RESTRICTED_ACCESS_MESSAGE
    assert "Secret Topic" not in text


@pytest.mark.asyncio
async def test_non_member_deep_link_shows_restricted_message(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, topic="Secret Topic")
    meeting = await db.get_meeting(meeting_id)
    context = make_context(status="kicked")
    update, context = _make_start_update(f"m_{meeting.public_token}")
    context.bot.get_chat_member = make_context(status="kicked").bot.get_chat_member

    await app.cmd_start(update, context)

    text = update.effective_message.reply_text.await_args.args[0]
    assert text == RESTRICTED_ACCESS_MESSAGE
    assert "Secret Topic" not in text


@pytest.mark.asyncio
async def test_non_member_callback_denied(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, topic="Secret Topic")
    context = make_context(status="left")
    update = _make_callback_update(f"details:{meeting_id}")

    await app._community_gated(app.cb_details)(update, context)

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == RESTRICTED_ACCESS_MESSAGE
    assert "Secret Topic" not in text


@pytest.mark.asyncio
async def test_get_chat_member_error_fails_closed(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    context = make_context(raise_error=BadRequest("User not found"))
    update = _make_callback_update(f"register:{meeting_id}")

    await app._community_gated(app.cb_register)(update, context)

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == RESTRICTED_ACCESS_MESSAGE


@pytest.mark.asyncio
async def test_start_shows_restricted_for_non_member(app, db):
    context = make_context(status="left")
    update, context = _make_start_update()
    context.bot.get_chat_member = make_context(status="left").bot.get_chat_member

    await app.cmd_start(update, context)

    text = update.effective_message.reply_text.await_args.args[0]
    assert text == RESTRICTED_ACCESS_MESSAGE
