"""Tests for mindful registration confirmation flow."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User as TgUser

from bot.config import Settings
from bot.handlers import BotApp
from bot.messages import REGISTRATION_SUCCESS
from bot.registration_confirmation import (
    format_cancelled,
    format_offer_short_confirm,
    format_step2,
    format_step3,
    format_unavailable,
)
from tests.conftest import create_host, create_meeting, fill_meeting

def _keyboard_callback_texts(keyboard) -> list[str]:
    return [btn.callback_data for row in keyboard.inline_keyboard for btn in row]


def _make_app(db, waitlist) -> BotApp:
    settings = Settings(
        telegram_bot_token="test-token",
        tz="Europe/Berlin",
    )
    scheduler = MagicMock()
    scheduler.on_participant_change = AsyncMock()
    return BotApp(settings, db, scheduler, waitlist)


def _make_callback_update(callback_data: str, *, user_id: int = 10) -> Update:
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=user_id, type="private")
    message = Message(message_id=1, date=None, chat=chat, from_user=user)
    cq = CallbackQuery(
        id=1,
        from_user=user,
        chat_instance="test",
        data=callback_data,
        message=message,
    )
    return Update(update_id=1, callback_query=cq)


@pytest.fixture
async def app(db, waitlist):
    return _make_app(db, waitlist)


@pytest.mark.asyncio
async def test_register_starts_confirmation_flow(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")

    update = _make_callback_update(f"register:{meeting_id}")
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_register(update, MagicMock())

    update.callback_query.message.reply_text.assert_awaited_once()
    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "Перед записью" in text
    assert "Ты свободна в это время?" in text
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    assert f"reg_s1_yes:{meeting_id}" in _keyboard_callback_texts(kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_reg_s1_yes_shows_step2(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)

    update = _make_callback_update(f"reg_s1_yes:{meeting_id}")
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_reg_s1_yes(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == format_step2()
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    assert f"reg_s2_yes:{meeting_id}" in _keyboard_callback_texts(kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_reg_s2_yes_shows_step3(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)

    update = _make_callback_update(f"reg_s2_yes:{meeting_id}")
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_reg_s2_yes(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == format_step3()
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    assert f"reg_s3_yes:{meeting_id}" in _keyboard_callback_texts(kwargs["reply_markup"])


@pytest.mark.parametrize("callback", ["reg_s1_no", "reg_s2_no", "reg_s3_no"])
@pytest.mark.asyncio
async def test_negative_answers_cancel_flow(app, db, callback):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)

    update = _make_callback_update(f"{callback}:{meeting_id}")
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    handler = {
        "reg_s1_no": app.cb_reg_s1_no,
        "reg_s2_no": app.cb_reg_s2_no,
        "reg_s3_no": app.cb_reg_s3_no,
    }[callback]
    await handler(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == format_cancelled()
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    callbacks = _keyboard_callback_texts(kwargs["reply_markup"])
    assert f"details:{meeting_id}" in callbacks
    assert "show_upcoming" in callbacks


@pytest.mark.asyncio
async def test_reg_s3_yes_completes_registration(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    app._send_google_calendar_offer = AsyncMock()

    update = _make_callback_update(f"reg_s3_yes:{meeting_id}")
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_reg_s3_yes(update, MagicMock())

    assert await db.is_registered(meeting_id, 10)
    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == REGISTRATION_SUCCESS
    app.scheduler.on_participant_change.assert_awaited_once()


@pytest.mark.asyncio
async def test_reg_s3_yes_when_full_shows_waitlist(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [11, 12])
    await db.get_or_create_user(10, "Guest", "guest")

    update = _make_callback_update(f"reg_s3_yes:{meeting_id}")
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_reg_s3_yes(update, MagicMock())

    assert not await db.is_registered(meeting_id, 10)
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    callbacks = _keyboard_callback_texts(kwargs["reply_markup"])
    assert f"waitlist_join:{meeting_id}" in callbacks
    app.scheduler.on_participant_change.assert_not_awaited()


@pytest.mark.asyncio
async def test_reg_s3_yes_when_meeting_gone(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.cancel_meeting(meeting_id, datetime.now(timezone.utc))

    update = _make_callback_update(f"reg_s3_yes:{meeting_id}")
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_reg_s3_yes(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == format_unavailable()
    assert not await db.is_registered(meeting_id, 10)
    app.scheduler.on_participant_change.assert_not_awaited()


@pytest.mark.asyncio
async def test_waitlist_join_skips_confirmation_flow(app, db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [11])
    await db.get_or_create_user(10, "Guest", "guest")

    update = _make_callback_update(f"waitlist_join:{meeting_id}")
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_waitlist_join(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "waitlist" in text.lower()
    assert "Перед записью" not in text


@pytest.mark.asyncio
async def test_offer_accept_shows_short_confirm(app, db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [11])
    await db.get_or_create_user(10, "Guest", "guest")
    join_result = await waitlist.join_waitlist(meeting_id, 10, now_utc)
    assert join_result.ok
    await db.unregister(meeting_id, 11)
    notifications = await waitlist.process_available_spots(meeting_id, now_utc)
    assert notifications
    entry_id = notifications[0].entry.id

    update = _make_callback_update(f"offer_accept:{entry_id}", user_id=10)
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_offer_accept(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == format_offer_short_confirm()
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    assert f"offer_confirm:{entry_id}" in _keyboard_callback_texts(kwargs["reply_markup"])
    assert not await db.is_registered(meeting_id, 10)


@pytest.mark.asyncio
async def test_offer_confirm_completes_registration(app, db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [11])
    await db.get_or_create_user(10, "Guest", "guest")
    await waitlist.join_waitlist(meeting_id, 10, now_utc)
    await db.unregister(meeting_id, 11)
    notifications = await waitlist.process_available_spots(meeting_id, now_utc)
    entry_id = notifications[0].entry.id

    app._send_google_calendar_offer = AsyncMock()

    update = _make_callback_update(f"offer_confirm:{entry_id}", user_id=10)
    update.callback_query.answer = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()

    await app.cb_offer_confirm(update, MagicMock())

    assert await db.is_registered(meeting_id, 10)
    app.scheduler.on_participant_change.assert_awaited_once()
    app._send_google_calendar_offer.assert_awaited_once()
