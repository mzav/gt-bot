"""Unit tests for cancellation confirmation helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.cancellation_confirmation import (
    CancellationReasonType,
    format_final_confirm,
    format_reason_prompt,
    parse_leave_confirm_callback,
    parse_leave_reason_callback,
    preflight_leave,
)
from tests.conftest import create_host, create_meeting


def test_parse_leave_reason_callback_valid():
    assert parse_leave_reason_callback(f"leave_r:{CancellationReasonType.ILL}:42") == (
        CancellationReasonType.ILL,
        42,
    )


def test_parse_leave_reason_callback_invalid():
    assert parse_leave_reason_callback("leave_r:bad:1") is None
    assert parse_leave_reason_callback("wrong:ill:1") is None
    assert parse_leave_reason_callback("leave_r:ill:notint") is None


def test_parse_leave_confirm_callback_valid():
    assert parse_leave_confirm_callback(
        f"leave_confirm:{CancellationReasonType.FAMILY}:7"
    ) == (CancellationReasonType.FAMILY, 7)


def test_parse_leave_confirm_callback_invalid():
    assert parse_leave_confirm_callback("leave_confirm:bad:1") is None


def test_format_reason_prompt(local_tz):
    from bot.models import Meeting

    meeting = Meeting(
        id=1,
        topic="Coffee",
        description="",
        start_at_utc=datetime(2026, 7, 1, 16, 0, 0, tzinfo=timezone.utc),
        max_participants=5,
        created_by=1,
    )
    text = format_reason_prompt(meeting, local_tz)
    assert "Coffee" in text
    assert "Жаль" in text


def test_format_final_confirm_non_empty():
    assert "Отменить участие" in format_final_confirm()


@pytest.mark.asyncio
async def test_preflight_leave_not_registered(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    result = await preflight_leave(db, meeting_id, user_id=99)
    assert result.meeting is None
    assert "не зарегистрирован" in result.error


@pytest.mark.asyncio
async def test_preflight_leave_canceled_meeting(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    await db.cancel_meeting(meeting_id, datetime.now(timezone.utc))
    result = await preflight_leave(db, meeting_id, user_id=10)
    assert result.meeting is None
    assert result.error is not None


@pytest.mark.asyncio
async def test_preflight_leave_success(db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    result = await preflight_leave(db, meeting_id, user_id=10)
    assert result.meeting is not None
    assert result.error is None
