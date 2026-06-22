"""Tests for edit meeting conversation handlers."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from bot.handlers import BotApp
from bot.keyboards import CONV_CANCEL_CALLBACK
from bot.messages import FLOW_DONE_MESSAGE
from tests.conftest import create_host, create_meeting, make_app, make_callback_update, make_context, make_message_update


@pytest.fixture
async def app(db, waitlist):
    return make_app(db, waitlist)


async def _start_edit(app, db, *, host_id=5):
    host_id = await create_host(db, user_id=host_id)
    meeting_id = await create_meeting(db, host_id)
    update = make_callback_update(f"edit:{meeting_id}", user_id=host_id)
    context = make_context()
    state = await app._edit_meeting_start(update, context)
    assert state == app.STATE_EDIT_MENU
    return app, meeting_id, host_id, context


@pytest.mark.asyncio
async def test_edit_meeting_start_rejects_non_host(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id)
    update = make_callback_update(f"edit:{meeting_id}", user_id=99)
    result = await app._edit_meeting_start(update, make_context())
    assert result == ConversationHandler.END
    assert "Только автор" in update.callback_query.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_edit_meeting_start_rejects_canceled(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id)
    await db.cancel_meeting(meeting_id, datetime.now(timezone.utc))
    update = make_callback_update(f"edit:{meeting_id}", user_id=host_id)
    result = await app._edit_meeting_start(update, make_context())
    assert result == ConversationHandler.END
    assert "отменена" in update.callback_query.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_edit_topic_handler(app, db):
    app, meeting_id, host_id, context = await _start_edit(app, db)
    update = make_callback_update("edit_field:topic", user_id=host_id)
    await app._edit_select_topic(update, context)
    update = make_message_update("New Topic", user_id=host_id)
    state = await app._edit_topic_handler(update, context)
    assert state == app.STATE_EDIT_MENU
    meeting = await db.get_meeting(meeting_id)
    assert meeting.topic == "New Topic"


@pytest.mark.asyncio
async def test_edit_description_handler(app, db):
    app, meeting_id, host_id, context = await _start_edit(app, db)
    update = make_callback_update("edit_field:description", user_id=host_id)
    await app._edit_select_description(update, context)
    update = make_message_update("New description", user_id=host_id)
    await app._edit_description_handler(update, context)
    meeting = await db.get_meeting(meeting_id)
    assert meeting.description == "New description"


@pytest.mark.asyncio
async def test_edit_max_handler(app, db):
    app, meeting_id, host_id, context = await _start_edit(app, db)
    update = make_callback_update("edit_field:max", user_id=host_id)
    await app._edit_select_max(update, context)
    update = make_message_update("8", user_id=host_id)
    await app._edit_max_handler(update, context)
    meeting = await db.get_meeting(meeting_id)
    assert meeting.max_participants == 8


@pytest.mark.asyncio
async def test_edit_max_handler_rejects_invalid(app, db):
    app, meeting_id, host_id, context = await _start_edit(app, db)
    update = make_callback_update("edit_field:max", user_id=host_id)
    await app._edit_select_max(update, context)
    update = make_message_update("abc", user_id=host_id)
    state = await app._edit_max_handler(update, context)
    assert state == app.STATE_EDIT_MAX


@pytest.mark.asyncio
async def test_edit_location_clear_with_dash(app, db):
    app, meeting_id, host_id, context = await _start_edit(app, db)
    update = make_callback_update("edit_field:location", user_id=host_id)
    await app._edit_select_location(update, context)
    update = make_message_update("-", user_id=host_id)
    await app._edit_location_handler(update, context)
    meeting = await db.get_meeting(meeting_id)
    assert meeting.location is None


@pytest.mark.asyncio
async def test_edit_cancel_clears_state(app, db):
    app, meeting_id, host_id, context = await _start_edit(app, db)
    app._hide_main_menu = AsyncMock()
    app._restore_main_menu = AsyncMock()
    update = make_callback_update(CONV_CANCEL_CALLBACK, user_id=host_id)
    result = await app._edit_cancel_callback(update, context)
    assert result == ConversationHandler.END
    assert context.user_data == {}


@pytest.mark.asyncio
async def test_restore_main_menu_sends_gotovo_with_menu(app):
    message = MagicMock()
    message.reply_text = AsyncMock()
    await app._restore_main_menu(message, user_id=5)
    message.reply_text.assert_awaited_once_with(
        FLOW_DONE_MESSAGE,
        reply_markup=app._main_menu_markup(5),
    )


@pytest.mark.asyncio
async def test_edit_done_restores_menu_after_confirmation(app, db):
    app, meeting_id, host_id, context = await _start_edit(app, db)
    update = make_callback_update("edit_field:done", user_id=host_id)
    result = await app._edit_done(update, context)
    assert result == ConversationHandler.END
    assert context.user_data == {}
    update.callback_query.edit_message_text.assert_awaited_once()
    update.callback_query.message.reply_text.assert_awaited_once_with(
        FLOW_DONE_MESSAGE,
        reply_markup=app._main_menu_markup(host_id),
    )


@pytest.mark.asyncio
async def test_edit_topic_handler_does_not_restore_menu(app, db):
    app, meeting_id, host_id, context = await _start_edit(app, db)
    update = make_callback_update("edit_field:topic", user_id=host_id)
    await app._edit_select_topic(update, context)
    update = make_message_update("New Topic", user_id=host_id)
    await app._edit_topic_handler(update, context)
    for call in update.effective_message.reply_text.await_args_list:
        assert call.args[0] != FLOW_DONE_MESSAGE
