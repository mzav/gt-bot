"""Tests for the persistent main-menu reply keyboard."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Settings
from bot.handlers import BotApp
from bot.main_menu import (
    ALL_MENU_LABELS,
    MENU_FORCE_SUMMARY,
    MENU_MEETINGS,
    MENU_MY,
    USER_MENU_LABELS,
    build_main_menu_keyboard,
    menu_label_filter,
)
from telegram import Chat, Message, Update, User as TgUser


def _keyboard_button_texts(keyboard) -> list[str]:
    return [btn.text for row in keyboard.keyboard for btn in row]


def _make_app(*, admin_user_ids: list[int] | None = None) -> BotApp:
    settings = Settings(
        telegram_bot_token="test-token",
        admin_user_ids=admin_user_ids or [],
    )
    return BotApp(settings, MagicMock(), MagicMock(), MagicMock())


def _make_text_update(text: str, *, user_id: int = 1) -> Update:
    user = TgUser(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=user_id, type="private")
    message = Message(message_id=1, date=None, chat=chat, text=text, from_user=user)
    return Update(update_id=1, message=message)


def test_keyboard_labels_for_regular_user():
    keyboard = build_main_menu_keyboard(is_admin=False)
    labels = _keyboard_button_texts(keyboard)
    assert len(labels) == 4
    assert MENU_FORCE_SUMMARY not in labels
    assert set(labels) == set(USER_MENU_LABELS)


def test_keyboard_labels_for_admin():
    keyboard = build_main_menu_keyboard(is_admin=True)
    labels = _keyboard_button_texts(keyboard)
    assert len(labels) == 5
    assert MENU_FORCE_SUMMARY in labels


def test_menu_labels_unique():
    assert len(ALL_MENU_LABELS) == len(set(ALL_MENU_LABELS))


@pytest.mark.parametrize("label", ALL_MENU_LABELS)
def test_menu_label_filter_matches(label: str):
    assert menu_label_filter().check_update(_make_text_update(label))


def test_menu_label_filter_rejects_random_text():
    assert not menu_label_filter().check_update(_make_text_update("hello"))


@pytest.mark.asyncio
async def test_handle_main_menu_text_routing():
    app = _make_app()
    app.cmd_meetings = AsyncMock()
    app.cmd_my = AsyncMock()
    app.cmd_force_summary = AsyncMock()
    app._send_welcome_message = AsyncMock()

    context = MagicMock()

    await app.handle_main_menu_text(_make_text_update(MENU_MEETINGS), context)
    app.cmd_meetings.assert_awaited_once()

    await app.handle_main_menu_text(_make_text_update(MENU_MY), context)
    app.cmd_my.assert_awaited_once()

    await app.handle_main_menu_text(_make_text_update(MENU_HELP), context)
    app._send_welcome_message.assert_awaited_once()

    await app.handle_main_menu_text(_make_text_update(MENU_FORCE_SUMMARY), context)
    app.cmd_force_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_summary_still_checks_admin():
    app = _make_app(admin_user_ids=[99])
    app.scheduler.run_announcement_now = AsyncMock()
    app.settings.announcements_channel_id = 123

    update = _make_text_update(MENU_FORCE_SUMMARY, user_id=1)
    message = update.effective_message
    message.reply_text = AsyncMock()

    await app.cmd_force_summary(update, MagicMock())

    message.reply_text.assert_awaited_once_with("Not authorized.")


@pytest.mark.asyncio
async def test_handle_unknown_text_replies_with_hint():
    app = _make_app()
    update = _make_text_update("random words")
    message = update.effective_message
    message.reply_text = AsyncMock()

    await app.handle_unknown_text(update, MagicMock())

    message.reply_text.assert_awaited_once()
    kwargs = message.reply_text.await_args.kwargs
    assert "меню" in message.reply_text.await_args.args[0].lower()
    assert kwargs["reply_markup"] == app._main_menu_markup(1)


@pytest.mark.asyncio
async def test_send_welcome_message_includes_main_menu():
    app = _make_app(admin_user_ids=[1])
    message = MagicMock()
    message.reply_text = AsyncMock()

    await app._send_welcome_message(message, user_id=1)

    kwargs = message.reply_text.await_args.kwargs
    labels = _keyboard_button_texts(kwargs["reply_markup"])
    assert MENU_FORCE_SUMMARY in labels
