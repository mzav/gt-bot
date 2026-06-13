"""Tests for participant change notifications."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from dateutil import tz

from bot.meeting_notifications import (
    detect_important_changes,
    format_participant_update_message,
    notify_participants,
    snapshot_meeting,
)
from bot.models import Meeting


def _make_meeting(**overrides) -> Meeting:
    defaults = {
        "id": 1,
        "topic": "Coffee Chat",
        "description": "Weekly",
        "start_at_utc": datetime(2026, 7, 1, 16, 0, 0, tzinfo=timezone.utc),
        "max_participants": 5,
        "location": "Cafe",
        "created_by": 1,
        "public_token": "tok123",
    }
    defaults.update(overrides)
    return Meeting(**defaults)


def test_detect_start_time_change():
    before = snapshot_meeting(_make_meeting())
    after = _make_meeting(start_at_utc=datetime(2026, 7, 1, 17, 0, 0, tzinfo=timezone.utc))
    changes = detect_important_changes(before, after)
    assert "время начала" in changes
    assert "время окончания" not in changes


def test_detect_end_time_change():
    before = snapshot_meeting(_make_meeting())
    after = _make_meeting(
        end_at_utc=datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc),
    )
    changes = detect_important_changes(before, after)
    assert "время окончания" in changes


def test_detect_location_change():
    before = snapshot_meeting(_make_meeting(location="Cafe A"))
    after = _make_meeting(location="Cafe B")
    changes = detect_important_changes(before, after)
    assert "место" in changes


def test_detect_cancellation():
    before = snapshot_meeting(_make_meeting())
    after = _make_meeting(canceled_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc))
    changes = detect_important_changes(before, after)
    assert "встреча отменена" in changes


def test_no_changes_for_description_only():
    before = snapshot_meeting(_make_meeting(description="Old"))
    after = _make_meeting(description="New description")
    assert detect_important_changes(before, after) == []


def test_format_participant_update_message():
    meeting = _make_meeting(
        end_at_utc=datetime(2026, 7, 1, 20, 0, 0, tzinfo=timezone.utc),
    )
    text = format_participant_update_message(
        meeting,
        ["время начала"],
        local_tz=tz.gettz("Europe/Berlin"),
        bot_username="TestBot",
    )
    assert "Coffee Chat" in text
    assert "время начала" in text
    assert "18:00–22:00" in text
    assert "Google Calendar" in text


@pytest.mark.asyncio
async def test_notify_participants_sends_dm():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    db = MagicMock()
    user = MagicMock()
    user.id = 10
    db.list_confirmed_participants = AsyncMock(return_value=[
        (MagicMock(), user),
    ])
    meeting = _make_meeting()
    await notify_participants(
        bot,
        db,
        meeting,
        ["место"],
        exclude_user_id=99,
        local_tz=tz.gettz("Europe/Berlin"),
        bot_username="TestBot",
    )
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 10
    assert "Coffee Chat" in bot.send_message.await_args.kwargs["text"]
