"""Tests for cancellation reason flow."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import User as TgUser

from bot.cancellation_confirmation import (
    CancellationReasonType,
    LEAVE_OTHER_PENDING_KEY,
    LEAVE_OTHER_TEXT_KEY,
    MAX_OTHER_REASON_LEN,
    format_final_confirm,
    format_other_reason_prompt,
    format_stayed_registered,
    format_unavailable,
)
from bot.config import Settings
from bot.handlers import BotApp
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


def _make_callback_update(callback_data: str, *, user_id: int = 10):
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    message = MagicMock()
    message.reply_text = AsyncMock()
    message.edit_message_text = AsyncMock()
    cq = MagicMock()
    cq.data = callback_data
    cq.message = message
    cq.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = cq
    update.effective_user = user
    return update


def _make_text_update(text: str, *, user_id: int = 10):
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_message = message
    update.effective_user = user
    return update


@pytest.fixture
async def app(db, waitlist):
    return _make_app(db, waitlist)


@pytest.mark.asyncio
async def test_leave_starts_reason_flow(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=3)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    update = _make_callback_update(f"leave:{meeting_id}")

    await app.cb_leave_meeting(update, MagicMock())

    update.callback_query.message.reply_text.assert_awaited_once()
    text = update.callback_query.message.reply_text.await_args.args[0]
    assert "Подскажи, пожалуйста, почему не получится прийти?" in text
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    callbacks = _keyboard_callback_texts(kwargs["reply_markup"])
    assert f"leave_r:{CancellationReasonType.ILL}:{meeting_id}" in callbacks
    assert f"leave_r:{CancellationReasonType.FAMILY}:{meeting_id}" in callbacks
    assert f"leave_r:{CancellationReasonType.OTHER}:{meeting_id}" in callbacks


@pytest.mark.asyncio
async def test_leave_reason_ill_shows_final_confirm(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    update = _make_callback_update(f"leave_r:{CancellationReasonType.ILL}:{meeting_id}")

    await app.cb_leave_reason(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == format_final_confirm()
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    assert f"leave_confirm:{CancellationReasonType.ILL}:{meeting_id}" in _keyboard_callback_texts(
        kwargs["reply_markup"]
    )


@pytest.mark.asyncio
async def test_leave_reason_other_prompts_text(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    context = MagicMock()
    context.user_data = {}

    update = _make_callback_update(f"leave_r:{CancellationReasonType.OTHER}:{meeting_id}")

    await app.cb_leave_reason(update, context)

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == format_other_reason_prompt()
    assert context.user_data[LEAVE_OTHER_PENDING_KEY] == {"meeting_id": meeting_id}
    kwargs = update.callback_query.message.reply_text.await_args.kwargs
    assert f"leave_other_abort:{meeting_id}" in _keyboard_callback_texts(kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_leave_other_text_proceeds_to_confirm(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    context = MagicMock()
    context.user_data = {LEAVE_OTHER_PENDING_KEY: {"meeting_id": meeting_id}}

    update = _make_text_update("Не успеваю по работе")

    await app.handle_unknown_text(update, context)

    assert context.user_data[LEAVE_OTHER_TEXT_KEY] == "Не успеваю по работе"
    text = update.effective_message.reply_text.await_args.args[0]
    assert text == format_final_confirm()
    kwargs = update.effective_message.reply_text.await_args.kwargs
    assert f"leave_confirm:{CancellationReasonType.OTHER}:{meeting_id}" in _keyboard_callback_texts(
        kwargs["reply_markup"]
    )


@pytest.mark.asyncio
async def test_leave_confirm_cancels_and_stores_reason(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    update = _make_callback_update(f"leave_confirm:{CancellationReasonType.ILL}:{meeting_id}")

    context = MagicMock()
    context.user_data = {}

    await app.cb_leave_confirm(update, context)

    assert not await db.is_registered(meeting_id, 10)
    reg = await db.get_canceled_registration(meeting_id, 10)
    assert reg is not None
    assert reg.cancellation_reason_type == CancellationReasonType.ILL
    assert reg.cancellation_reason_text is None
    assert reg.cancelled_at is not None
    app.scheduler.on_participant_change.assert_awaited_once()


@pytest.mark.asyncio
async def test_leave_confirm_other_stores_free_text(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    update = _make_callback_update(f"leave_confirm:{CancellationReasonType.OTHER}:{meeting_id}")

    context = MagicMock()
    context.user_data = {LEAVE_OTHER_TEXT_KEY: "Срочная поездка"}

    await app.cb_leave_confirm(update, context)

    reg = await db.get_canceled_registration(meeting_id, 10)
    assert reg.cancellation_reason_type == CancellationReasonType.OTHER
    assert reg.cancellation_reason_text == "Срочная поездка"


@pytest.mark.asyncio
async def test_leave_abort_keeps_registration(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    update = _make_callback_update(f"leave_abort:{meeting_id}")

    context = MagicMock()
    context.user_data = {LEAVE_OTHER_TEXT_KEY: "should clear"}

    await app.cb_leave_abort(update, context)

    assert await db.is_registered(meeting_id, 10)
    text = update.callback_query.message.edit_message_text.await_args.args[0]
    assert text == format_stayed_registered()
    kwargs = update.callback_query.message.edit_message_text.await_args.kwargs
    assert f"details:{meeting_id}" in _keyboard_callback_texts(kwargs["reply_markup"])
    assert LEAVE_OTHER_TEXT_KEY not in context.user_data


@pytest.mark.asyncio
async def test_leave_confirm_triggers_waitlist(app, db, waitlist, now_utc):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=2)
    await fill_meeting(db, meeting_id, [10, 11])
    await db.get_or_create_user(12, "Wait User", "wait")
    await waitlist.join_waitlist(meeting_id, 12, now_utc)

    app._process_waitlist_after_unregister = AsyncMock()

    update = _make_callback_update(f"leave_confirm:{CancellationReasonType.FAMILY}:{meeting_id}")

    context = MagicMock()
    context.user_data = {}

    await app.cb_leave_confirm(update, context)

    app._process_waitlist_after_unregister.assert_awaited_once_with(meeting_id)
    offers = await waitlist.process_available_spots(meeting_id, now_utc)
    assert len(offers) == 1
    assert offers[0].user_id == 12


@pytest.mark.asyncio
async def test_stale_leave_confirm_not_registered(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)

    update = _make_callback_update(f"leave_confirm:{CancellationReasonType.ILL}:{meeting_id}")

    context = MagicMock()
    context.user_data = {}

    await app.cb_leave_confirm(update, context)

    update.callback_query.message.reply_text.assert_awaited_once()
    assert "не зарегистрирован" in update.callback_query.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_invalid_other_text_empty(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    context = MagicMock()
    context.user_data = {LEAVE_OTHER_PENDING_KEY: {"meeting_id": meeting_id}}

    update = _make_text_update("   ")

    await app.handle_unknown_text(update, context)

    assert LEAVE_OTHER_TEXT_KEY not in context.user_data
    assert "напиши" in update.effective_message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_invalid_other_text_too_long(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    context = MagicMock()
    context.user_data = {LEAVE_OTHER_PENDING_KEY: {"meeting_id": meeting_id}}

    update = _make_text_update("x" * (MAX_OTHER_REASON_LEN + 1))

    await app.handle_unknown_text(update, context)

    assert LEAVE_OTHER_TEXT_KEY not in context.user_data
    assert str(MAX_OTHER_REASON_LEN) in update.effective_message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_leave_when_meeting_canceled(app, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    await db.cancel_meeting(meeting_id, datetime.now(timezone.utc))

    update = _make_callback_update(f"leave:{meeting_id}")

    await app.cb_leave_meeting(update, MagicMock())

    text = update.callback_query.message.reply_text.await_args.args[0]
    assert text == format_unavailable()


@pytest.mark.asyncio
async def test_unregister_with_reason_persists_fields(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)

    ok, _ = await db.unregister(
        meeting_id,
        10,
        reason_type=CancellationReasonType.FAMILY,
    )
    assert ok
    reg = await db.get_canceled_registration(meeting_id, 10)
    assert reg.cancellation_reason_type == CancellationReasonType.FAMILY
    assert reg.cancelled_at is not None
