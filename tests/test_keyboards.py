"""Tests for keyboard helpers."""
from __future__ import annotations

from datetime import date, timedelta

from bot.keyboards import (
    CONV_CANCEL_CALLBACK,
    CONV_CANCEL_LABEL,
    MeetingCalendar,
    MonthPickerKeyboard,
    TimePickerKeyboard,
    append_cancel_row,
    conversation_cancel_keyboard,
    edit_photo_keyboard,
    photo_skip_keyboard,
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


def test_month_picker_keyboard():
    markup = MonthPickerKeyboard.build()
    assert len(markup.inline_keyboard) == 4  # 3 months + cancel
    for row in markup.inline_keyboard[:-1]:
        assert row[0].callback_data.startswith("month:")


def test_month_picker_parse_callback():
    assert MonthPickerKeyboard.parse_callback("month:2026:7") == (2026, 7)
    assert MonthPickerKeyboard.parse_callback("invalid") is None
    assert MonthPickerKeyboard.parse_callback("month:bad:7") is None


def test_time_picker_hours():
    markup = TimePickerKeyboard.build_hours()
    hour_buttons = [btn for row in markup.inline_keyboard for btn in row if btn.callback_data.startswith("hour:")]
    assert len(hour_buttons) == 18  # 6..23
    assert markup.inline_keyboard[-1][0].callback_data == CONV_CANCEL_CALLBACK


def test_time_picker_minutes():
    markup = TimePickerKeyboard.build_minutes(10)
    minute_row = markup.inline_keyboard[0]
    assert all(btn.callback_data.startswith("time:10:") for btn in minute_row)
    assert markup.inline_keyboard[1][0].callback_data == "hour:back"


def test_photo_skip_keyboard():
    markup = photo_skip_keyboard()
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "skip_photo" in callbacks
    assert CONV_CANCEL_CALLBACK in callbacks


def test_edit_photo_keyboard_with_photo():
    markup = edit_photo_keyboard(has_photo=True)
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "edit_photo:remove" in callbacks
    assert "edit_photo:cancel" in callbacks


def test_edit_photo_keyboard_without_photo():
    markup = edit_photo_keyboard(has_photo=False)
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "edit_photo:remove" not in callbacks
    assert "edit_photo:cancel" in callbacks
