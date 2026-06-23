"""Tests for escaping create/edit meeting conversations."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, PhotoSize, Update, User as TgUser
from telegram.error import BadRequest
from telegram.ext import ConversationHandler

from bot.handlers import BotApp, _CREATE_CANCEL_MESSAGE, _EDIT_CANCEL_MESSAGE
from bot.keyboards import CONV_CANCEL_CALLBACK
from bot.main_menu import MENU_MEETINGS
from bot.messages import WELCOME_MESSAGE
from tests.conftest import TEST_CHANNEL_ID, create_host, create_meeting, make_context
from tests.test_access_control import _make_app, _make_start_update, _make_text_update


def _make_cancel_callback_update(*, user_id: int = 10):
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    message = MagicMock()
    message.reply_text = AsyncMock()
    cq = MagicMock()
    cq.data = CONV_CANCEL_CALLBACK
    cq.message = message
    cq.answer = AsyncMock()
    cq.edit_message_reply_markup = AsyncMock()
    cq.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = cq
    update.effective_user = user
    update.effective_message = None
    return update


def _make_edit_start_update(meeting_id: int, *, user_id: int = 1):
    user = TgUser(id=user_id, is_bot=False, first_name="Host")
    message = MagicMock()
    message.reply_text = AsyncMock()
    cq = MagicMock()
    cq.data = f"edit:{meeting_id}"
    cq.message = message
    cq.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = cq
    update.effective_user = user
    update.effective_message = None
    return update


@pytest.fixture
async def app(db, waitlist):
    return _make_app(db, waitlist)


@pytest.mark.asyncio
async def test_create_menu_fallback_clears_state_and_lists_meetings(app, db):
    host_id = await create_host(db, user_id=10)
    await create_meeting(db, host_id)
    context = make_context(status="member")
    context.user_data["topic"] = "Draft meeting"

    update = _make_text_update(MENU_MEETINGS, user_id=10)
    gated = app._community_gated(app._create_meeting_menu_fallback, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    update.effective_message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_create_start_fallback_shows_welcome_and_clears_state(app, db):
    await create_host(db, user_id=10)
    context = make_context(status="member")
    context.user_data["topic"] = "Draft meeting"

    update = _make_text_update("/start", user_id=10)
    gated = app._community_gated(app._create_meeting_start_fallback, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    text = update.effective_message.reply_text.await_args.args[0]
    assert WELCOME_MESSAGE in text or text == WELCOME_MESSAGE


@pytest.mark.asyncio
async def test_create_start_fallback_handles_meeting_deep_link(app, db):
    host_id = await create_host(db, user_id=10)
    meeting_id = await create_meeting(db, host_id, topic="Book Club")
    meeting = await db.get_meeting(meeting_id)
    context = make_context(status="member")
    context.user_data["topic"] = "Draft meeting"

    update, context = _make_start_update(f"m_{meeting.public_token}", user_id=10)
    gated = app._community_gated(app._create_meeting_start_fallback, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Book Club" in text
    assert text != WELCOME_MESSAGE


@pytest.mark.asyncio
async def test_create_cancel_callback_clears_state(app, db):
    await create_host(db, user_id=10)
    context = make_context(status="member")
    context.user_data["topic"] = "Draft meeting"

    update = _make_cancel_callback_update(user_id=10)
    gated = app._community_gated(app._create_meeting_cancel_callback, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    update.callback_query.edit_message_reply_markup.assert_awaited_with(reply_markup=None)
    update.callback_query.message.reply_text.assert_awaited_with(
        _CREATE_CANCEL_MESSAGE,
        reply_markup=app._main_menu_markup(10),
    )


@pytest.mark.asyncio
async def test_edit_cancel_callback_clears_state(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    context = make_context(status="member")
    context.user_data["edit_meeting_id"] = meeting_id

    update = _make_cancel_callback_update(user_id=host_id)
    gated = app._community_gated(app._edit_cancel_callback, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    update.callback_query.edit_message_reply_markup.assert_awaited_with(reply_markup=None)
    update.callback_query.message.reply_text.assert_awaited_with(
        _EDIT_CANCEL_MESSAGE,
        reply_markup=app._main_menu_markup(host_id),
    )


@pytest.mark.asyncio
async def test_edit_menu_fallback_clears_state_and_lists_meetings(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    context = make_context(status="member")
    context.user_data["edit_meeting_id"] = meeting_id

    update = _make_text_update(MENU_MEETINGS, user_id=host_id)
    gated = app._community_gated(app._edit_menu_fallback, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    update.effective_message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_edit_start_fallback_shows_welcome_and_clears_state(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    context = make_context(status="member")
    context.user_data["edit_meeting_id"] = meeting_id

    update = _make_text_update("/start", user_id=host_id)
    gated = app._community_gated(app._edit_start_fallback, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    text = update.effective_message.reply_text.await_args.args[0]
    assert text == WELCOME_MESSAGE


@pytest.mark.asyncio
async def test_edit_start_fallback_handles_meeting_deep_link(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, topic="Deep Link Meeting")
    meeting = await db.get_meeting(meeting_id)
    other_meeting_id = await create_meeting(db, host_id, topic="Other Meeting")
    context = make_context(status="member")
    context.user_data["edit_meeting_id"] = other_meeting_id

    update, context = _make_start_update(f"m_{meeting.public_token}", user_id=host_id)
    gated = app._community_gated(app._edit_start_fallback, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Deep Link Meeting" in text
    assert text != WELCOME_MESSAGE


@pytest.mark.asyncio
async def test_create_meeting_photo_ends_even_if_restore_main_menu_fails(app, db):
    await create_host(db, user_id=10)
    context = make_context(status="member")
    context.user_data.update(
        {
            "selected_start_utc": datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc),
            "topic": "Test",
            "description": "Desc",
            "max_participants": 3,
            "location": "Berlin",
        }
    )

    user = TgUser(id=10, is_bot=False, first_name="Test")
    photo = PhotoSize(file_id="photo123", file_unique_id="uniq", width=100, height=100)
    message = MagicMock()
    message.photo = [photo]
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.effective_user = user
    update.effective_message = message
    update.message = message
    update.callback_query = None

    app.scheduler.plan_urgent_announcement = AsyncMock()
    app._restore_main_menu = AsyncMock(side_effect=BadRequest("restore failed"))

    gated = app._community_gated(app._create_meeting_photo, conv=True)
    result = await gated(update, context)

    assert result == ConversationHandler.END
    assert context.user_data == {}
    message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_create_menu_in_state_photo_routes_to_fallback(app, db):
    host_id = await create_host(db, user_id=10)
    await create_meeting(db, host_id)

    handler = app._build_create_meeting_handler()
    chat = Chat(id=100, type="private")
    user = TgUser(id=10, is_bot=False, first_name="Test")
    message = Message(
        message_id=1,
        date=datetime(2026, 6, 19, 10, 53, tzinfo=timezone.utc),
        chat=chat,
        from_user=user,
        text=MENU_MEETINGS,
    )
    update = Update(update_id=1, message=message)

    context = make_context(status="member")
    context.user_data["topic"] = "Draft meeting"

    conv_key = (chat.id, user.id)
    handler._conversations[conv_key] = app.STATE_PHOTO

    app.cmd_meetings = AsyncMock()

    application = MagicMock()
    check_result = handler.check_update(update)
    assert check_result is not None

    new_state = await handler.handle_update(update, application, check_result, context)

    assert new_state is None
    assert conv_key not in handler._conversations
    assert context.user_data == {}
    app.cmd_meetings.assert_awaited_once()
