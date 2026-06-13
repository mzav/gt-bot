"""Persistent reply-keyboard main menu for the bot."""
from __future__ import annotations

import re

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import filters

MENU_MEETINGS = "🗓 Встречи"
MENU_MY = "👩‍💼 Мои встречи"
MENU_CREATE = "✨ Создать встречу"
MENU_HELP = "❓ Помощь"
MENU_FORCE_SUMMARY = "Force summary"

USER_MENU_LABELS = (MENU_MEETINGS, MENU_MY, MENU_CREATE, MENU_HELP)
ALL_MENU_LABELS = USER_MENU_LABELS + (MENU_FORCE_SUMMARY,)


def build_main_menu_keyboard(*, is_admin: bool) -> ReplyKeyboardMarkup:
    """Build the persistent main-menu reply keyboard."""
    rows = [
        [KeyboardButton(MENU_MEETINGS), KeyboardButton(MENU_MY)],
        [KeyboardButton(MENU_CREATE), KeyboardButton(MENU_HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(MENU_FORCE_SUMMARY)])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def remove_main_menu_keyboard() -> ReplyKeyboardRemove:
    """Remove the main-menu reply keyboard."""
    return ReplyKeyboardRemove()


def menu_label_filter() -> filters.BaseFilter:
    """Filter matching any main-menu button label."""
    pattern = "|".join(re.escape(label) for label in ALL_MENU_LABELS)
    return filters.Regex(f"^({pattern})$")


