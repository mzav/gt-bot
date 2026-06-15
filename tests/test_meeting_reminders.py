"""Tests for automatic participant meeting reminders."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from dateutil import tz
from sqlalchemy import select, update

from bot.meeting_reminders import (
    REMINDER_OFFSETS,
    format_reminder_message,
    process_participant_reminders,
    should_skip_reminder,
)
from bot.models import Meeting, Registration
from bot.storage import Database
from tests.conftest import create_host, create_meeting


def _keyboard_button_labels(keyboard) -> list[str]:
    if keyboard is None:
        return []
    return [
        btn.text
        for row in keyboard.inline_keyboard
        for btn in row
    ]


def _keyboard_callback_data(keyboard) -> list[str]:
    if keyboard is None:
        return []
    return [
        btn.callback_data
        for row in keyboard.inline_keyboard
        for btn in row
        if btn.callback_data
    ]


async def _set_registration_created_at(
    db: Database,
    meeting_id: int,
    user_id: int,
    created_at: datetime,
) -> None:
    async with db.session() as s:
        await s.execute(
            update(Registration)
            .where(
                Registration.meeting_id == meeting_id,
                Registration.user_id == user_id,
            )
            .values(created_at=created_at)
        )
        await s.commit()


def _meeting_start_utc() -> datetime:
    return datetime(2026, 7, 1, 18, 0, 0, tzinfo=timezone.utc)


def _now_for_offset(offset: int) -> datetime:
    """Return now_utc on the reminder send date for a July 1 Berlin meeting."""
    reminder_dates = {7: 24, 3: 28, 1: 30}
    day = reminder_dates[offset]
    return datetime(2026, 6, day, 10, 0, 0, tzinfo=timezone.utc)


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.parametrize("offset", REMINDER_OFFSETS)
@pytest.mark.asyncio
async def test_sends_reminder_at_each_offset(db, local_tz, offset):
    host_id = await create_host(db)
    meeting_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_meeting_start_utc(),
        end_at_utc=datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc),
    )
    await db.get_or_create_user(10, "Alice", "alice")
    await db.register(meeting_id, 10)

    bot = _make_bot()
    await process_participant_reminders(
        bot,
        db,
        local_tz,
        "TestBot",
        now_utc=_now_for_offset(offset),
    )

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 10
    assert await db.has_participant_reminder(meeting_id, 10, offset)


@pytest.mark.asyncio
async def test_does_not_send_duplicate_reminders(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, start_at_utc=_meeting_start_utc())
    await db.get_or_create_user(10, "Alice", "alice")
    await db.register(meeting_id, 10)

    bot = _make_bot()
    now = _now_for_offset(1)
    await process_participant_reminders(bot, db, local_tz, "TestBot", now_utc=now)
    await process_participant_reminders(bot, db, local_tz, "TestBot", now_utc=now)

    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_skips_when_registered_on_reminder_date(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, start_at_utc=_meeting_start_utc())
    await db.get_or_create_user(10, "Alice", "alice")
    await db.register(meeting_id, 10)

    reminder_day = datetime(2026, 6, 24, 8, 0, 0, tzinfo=timezone.utc)
    await _set_registration_created_at(db, meeting_id, 10, reminder_day)

    bot = _make_bot()
    await process_participant_reminders(
        bot,
        db,
        local_tz,
        "TestBot",
        now_utc=_now_for_offset(7),
    )

    bot.send_message.assert_not_awaited()
    assert not await db.has_participant_reminder(meeting_id, 10, 7)


@pytest.mark.asyncio
async def test_skips_cancelled_participant(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, start_at_utc=_meeting_start_utc())
    await db.get_or_create_user(10, "Alice", "alice")
    await db.register(meeting_id, 10)
    await db.unregister(meeting_id, 10)

    bot = _make_bot()
    await process_participant_reminders(
        bot,
        db,
        local_tz,
        "TestBot",
        now_utc=_now_for_offset(1),
    )

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_cancelled_meeting(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, start_at_utc=_meeting_start_utc())
    await db.get_or_create_user(10, "Alice", "alice")
    await db.register(meeting_id, 10)
    await db.cancel_meeting(meeting_id, datetime.now(timezone.utc))

    bot = _make_bot()
    await process_participant_reminders(
        bot,
        db,
        local_tz,
        "TestBot",
        now_utc=_now_for_offset(1),
    )

    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_past_meeting(db, local_tz):
    host_id = await create_host(db)
    past_start = datetime(2026, 5, 1, 18, 0, 0, tzinfo=timezone.utc)
    meeting_id = await create_meeting(db, host_id, start_at_utc=past_start)
    await db.get_or_create_user(10, "Alice", "alice")
    await db.register(meeting_id, 10)

    bot = _make_bot()
    await process_participant_reminders(
        bot,
        db,
        local_tz,
        "TestBot",
        now_utc=datetime(2026, 4, 30, 10, 0, 0, tzinfo=timezone.utc),
    )

    bot.send_message.assert_not_awaited()


def test_format_reminder_message_with_end_time(local_tz):
    meeting = Meeting(
        id=1,
        topic="Coffee Chat",
        description="",
        start_at_utc=datetime(2026, 7, 1, 16, 0, 0, tzinfo=timezone.utc),
        end_at_utc=datetime(2026, 7, 1, 18, 0, 0, tzinfo=timezone.utc),
        max_participants=5,
        location="Berlin Cafe",
        created_by=1,
    )
    text = format_reminder_message(meeting, local_tz)
    assert "Coffee Chat" in text
    assert "01.07.2026" in text
    assert "18:00–20:00" in text
    assert "Berlin Cafe" in text
    assert "Небольшое напоминание" in text
    assert "До встречи" in text


def test_format_reminder_message_without_end_time(local_tz):
    meeting = Meeting(
        id=1,
        topic="Walk",
        description="",
        start_at_utc=datetime(2026, 7, 1, 16, 0, 0, tzinfo=timezone.utc),
        end_at_utc=None,
        max_participants=5,
        location=None,
        created_by=1,
    )
    text = format_reminder_message(meeting, local_tz)
    assert "01.07.2026, 18:00" in text
    assert "–" not in text.split("🗓")[1].split("\n")[0]
    assert "TBA" in text


def test_should_skip_reminder_same_day(local_tz):
    reg = Registration(
        meeting_id=1,
        user_id=10,
        created_at=datetime(2026, 6, 24, 6, 0, 0, tzinfo=timezone.utc),
    )
    reminder_date = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc).astimezone(local_tz).date()
    assert should_skip_reminder(reg, reminder_date, local_tz)


def test_should_not_skip_reminder_different_day(local_tz):
    reg = Registration(
        meeting_id=1,
        user_id=10,
        created_at=datetime(2026, 6, 23, 6, 0, 0, tzinfo=timezone.utc),
    )
    reminder_date = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc).astimezone(local_tz).date()
    assert not should_skip_reminder(reg, reminder_date, local_tz)


@pytest.mark.asyncio
async def test_reminder_content_and_keyboard(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(
        db,
        host_id,
        start_at_utc=_meeting_start_utc(),
        end_at_utc=datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc),
    )
    await db.get_or_create_user(10, "Alice", "alice")
    await db.register(meeting_id, 10)
    meeting = await db.get_meeting(meeting_id)

    bot = _make_bot()
    await process_participant_reminders(
        bot,
        db,
        local_tz,
        "TestBot",
        now_utc=_now_for_offset(3),
    )

    kwargs = bot.send_message.await_args.kwargs
    assert meeting.topic in kwargs["text"]
    assert "01.07.2026" in kwargs["text"]
    assert "20:00–22:00" in kwargs["text"]
    assert "Berlin" in kwargs["text"]
    assert _keyboard_button_labels(kwargs["reply_markup"]) == ["Открыть встречу"]
    assert _keyboard_callback_data(kwargs["reply_markup"]) == []


@pytest.mark.asyncio
async def test_send_failure_does_not_crash_job(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, start_at_utc=_meeting_start_utc())
    await db.get_or_create_user(10, "Alice", "alice")
    await db.get_or_create_user(11, "Bob", "bob")
    await db.register(meeting_id, 10)
    await db.register(meeting_id, 11)

    bot = _make_bot()

    async def _send_side_effect(*args, **kwargs):
        if kwargs.get("chat_id") == 10:
            raise RuntimeError("blocked")

    bot.send_message.side_effect = _send_side_effect

    await process_participant_reminders(
        bot,
        db,
        local_tz,
        "TestBot",
        now_utc=_now_for_offset(1),
    )

    assert bot.send_message.await_count == 2
    assert not await db.has_participant_reminder(meeting_id, 10, 1)
    assert await db.has_participant_reminder(meeting_id, 11, 1)
