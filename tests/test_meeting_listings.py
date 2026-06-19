"""Tests for meeting listings and detail callbacks."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from tests.conftest import (
    create_host,
    create_meeting,
    fill_meeting,
    make_app,
    make_callback_update,
    make_context,
    make_message_update,
)


@pytest.fixture
async def app(db, waitlist):
    return make_app(db, waitlist)


@pytest.mark.asyncio
async def test_cmd_meetings_lists_visible_meetings(app, db):
    host_id = await create_host(db)
    await create_meeting(db, host_id, topic="Visible Meeting")
    update = make_message_update("", user_id=10)
    context = make_context()
    await app.cmd_meetings(update, context)
    update.effective_message.reply_text.assert_awaited()
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Visible Meeting" in text


@pytest.mark.asyncio
async def test_cmd_meetings_empty(app, db):
    update = make_message_update("", user_id=10)
    context = make_context()
    await app.cmd_meetings(update, context)
    assert "No upcoming meetings" in update.effective_message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_cb_show_upcoming(app, db):
    host_id = await create_host(db)
    await create_meeting(db, host_id, topic="Upcoming")
    update = make_callback_update("show_upcoming", user_id=10)
    await app.cb_show_upcoming(update, make_context())
    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "Upcoming" in text


@pytest.mark.asyncio
async def test_cmd_my_host_shows_edit_button(app, db):
    host_id = await create_host(db, user_id=5)
    await create_meeting(db, host_id)
    update = make_message_update("", user_id=host_id)
    await app.cmd_my(update, make_context())
    kwargs = update.effective_message.reply_text.await_args.kwargs
    callbacks = [
        btn.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for btn in row
    ]
    assert any(c.startswith("edit:") for c in callbacks)


@pytest.mark.asyncio
async def test_cmd_my_participant_shows_leave(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    update = make_message_update("", user_id=10)
    await app.cmd_my(update, make_context())
    kwargs = update.effective_message.reply_text.await_args.kwargs
    callbacks = [
        btn.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for btn in row
    ]
    assert any(c.startswith("leave:") for c in callbacks)


@pytest.mark.asyncio
async def test_cmd_my_excludes_canceled_registration(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    await db.unregister(meeting_id, 10)
    update = make_message_update("", user_id=10)
    await app.cmd_my(update, make_context())
    assert "no meetings" in update.effective_message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_cb_details_shows_meeting_card(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, topic="Detail Topic")
    update = make_callback_update(f"details:{meeting_id}", user_id=10)
    await app.cb_details(update, make_context())
    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "Detail Topic" in text


@pytest.mark.asyncio
async def test_cb_participants_host_only(app, db):
    host_id = await create_host(db, user_id=5)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await fill_meeting(db, meeting_id, [10])

    host_update = make_callback_update(f"participants:{meeting_id}", user_id=host_id)
    await app.cb_participants(host_update, make_context())
    text = host_update.callback_query.message.reply_text.await_args.args[0]
    assert "User 10" in text

    guest_update = make_callback_update(f"participants:{meeting_id}", user_id=10)
    await app.cb_participants(guest_update, make_context())
    guest_text = guest_update.callback_query.message.reply_text.await_args.args[0]
    assert "только организатору" in guest_text
