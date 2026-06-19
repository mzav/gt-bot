"""Tests for keyboard helpers."""
from __future__ import annotations

from datetime import date, timedelta

from bot.keyboards import (
    CONV_CANCEL_CALLBACK,
    CONV_CANCEL_LABEL,
    MeetingCalendar,
    append_cancel_row,
    conversation_cancel_keyboard,
)


def test_conversation_cancel_keyboard():
    markup = conversation_cancel_keyboard()
    assert len(markup.inline_keyboard) == 1
    btn = markup.inline_keyboard[0][0]
    assert btn.text == CONV_CANCEL_LABEL
    assert btn.callback_data == CONV_CANCEL_CALLBACK


def test_append_cancel_row_accepts_calendar_json():
    tomorrow = date.today() + timedelta(days=1)
    calendar_json, _ = MeetingCalendar(
        calendar_id=1,
        current_date=tomorrow,
        min_date=tomorrow,
        max_date=date(tomorrow.year, tomorrow.month, 28),
    ).build()

    markup = append_cancel_row(calendar_json)

    assert markup.inline_keyboard[-1][0].text == CONV_CANCEL_LABEL
    assert markup.inline_keyboard[-1][0].callback_data == CONV_CANCEL_CALLBACK
    assert len(markup.inline_keyboard) > 1
