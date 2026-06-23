"""Tests for create meeting conversation handlers."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import ConversationHandler

from bot.handlers import BotApp
from bot.keyboards import CONV_CANCEL_CALLBACK
from tests.conftest import create_host, make_app, make_callback_update, make_context, make_message_update


@pytest.fixture
async def app(db, waitlist):
    scheduler = MagicMock()
    scheduler.on_participant_change = AsyncMock()
    scheduler.plan_urgent_announcement = AsyncMock()
    scheduler.run_announcement_now = AsyncMock()
    scheduler._bot = MagicMock()
    return make_app(db, waitlist, scheduler=scheduler)


@pytest.mark.asyncio
async def test_create_meeting_happy_path_skip_photo(app, db):
    host_id = await create_host(db, user_id=5)
    context = make_context()
    update = make_message_update("", user_id=host_id)
    state = await app._create_meeting_start(update, context)
    assert state == app.STATE_TOPIC

    update = make_message_update("Coffee Chat", user_id=host_id)
    assert await app._create_meeting_topic(update, context) == app.STATE_DESCRIPTION

    update = make_message_update("Weekly meetup", user_id=host_id)
    assert await app._create_meeting_description(update, context) == app.STATE_MAX

    update = make_message_update("5", user_id=host_id)
    assert await app._create_meeting_max_members(update, context) == app.STATE_LOCATION

    update = make_message_update("Berlin Cafe", user_id=host_id)
    assert await app._create_meeting_location(update, context) == app.STATE_MONTH

    start_utc = datetime(2026, 8, 15, 16, 0, 0, tzinfo=timezone.utc)
    context.user_data.update({
        "selected_start_utc": start_utc,
        "selected_end_utc": datetime(2026, 8, 15, 18, 0, 0, tzinfo=timezone.utc),
        "selected_reg_start_utc": None,
    })

    update = make_callback_update("skip_photo", user_id=host_id)
    update.callback_query.edit_message_text = AsyncMock()
    app._restore_main_menu = AsyncMock()
    result = await app._create_meeting_photo(update, context)
    assert result == ConversationHandler.END
    app.scheduler.plan_urgent_announcement.assert_awaited_once()
    meetings = await db.list_upcoming_meetings(datetime.now(timezone.utc))
    assert any(m.topic == "Coffee Chat" for m in meetings)


@pytest.mark.asyncio
async def test_create_meeting_max_invalid(app, db):
    host_id = await create_host(db, user_id=5)
    context = make_context()
    context.user_data["topic"] = "T"
    context.user_data["description"] = "D"
    update = make_message_update("not-a-number", user_id=host_id)
    state = await app._create_meeting_max_members(update, context)
    assert state == app.STATE_MAX


@pytest.mark.asyncio
async def test_create_meeting_photo_invalid(app, db):
    host_id = await create_host(db, user_id=5)
    context = make_context()
    update = make_message_update("not a photo", user_id=host_id)
    state = await app._create_meeting_photo_invalid(update, context)
    assert state == app.STATE_PHOTO


@pytest.mark.asyncio
async def test_create_meeting_cancel(app, db):
    host_id = await create_host(db, user_id=5)
    context = make_context()
    context.user_data["topic"] = "Partial"
    app._hide_main_menu = AsyncMock()
    app._restore_main_menu = AsyncMock()
    update = make_callback_update(CONV_CANCEL_CALLBACK, user_id=host_id)
    result = await app._create_meeting_cancel_callback(update, context)
    assert result == ConversationHandler.END
    assert context.user_data == {}
