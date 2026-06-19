"""Unit tests for registration confirmation helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.models import Meeting
from bot.registration_confirmation import (
    format_overlap_confirm,
    format_step1,
    format_step3,
    preflight_registration,
)
from tests.conftest import create_host, create_meeting, fill_meeting


def test_format_step1(local_tz):
    meeting = Meeting(
        id=1,
        topic="Yoga",
        description="",
        start_at_utc=datetime(2026, 7, 1, 16, 0, 0, tzinfo=timezone.utc),
        location="Berlin",
        max_participants=5,
        created_by=1,
    )
    text = format_step1(meeting, local_tz)
    assert "Berlin" in text
    assert "01.07.2026" in text
    assert "свободна" in text.lower()


def test_format_step3():
    assert "Записать тебя" in format_step3()


def test_format_overlap_confirm():
    text = format_overlap_confirm("📌 A\n📅 B")
    assert "пересекается" in text
    assert "📌 A" in text


@pytest.mark.asyncio
async def test_preflight_registration_not_open_yet(db, local_tz):
    host_id = await create_host(db)
    reg_start = datetime.now(timezone.utc) + timedelta(days=1)
    meeting_id = await create_meeting(
        db,
        host_id,
        registration_starts_at_utc=reg_start,
    )
    result = await preflight_registration(db, meeting_id, user_id=10, local_tz=local_tz)
    assert result.meeting is None
    assert "Регистрация откроется" in result.error


@pytest.mark.asyncio
async def test_preflight_registration_already_registered(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    await db.get_or_create_user(10, "Guest", "guest")
    await db.register(meeting_id, 10)
    result = await preflight_registration(db, meeting_id, user_id=10, local_tz=local_tz)
    assert result.meeting is None
    assert "already registered" in result.error


@pytest.mark.asyncio
async def test_preflight_registration_full(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, max_participants=1)
    await fill_meeting(db, meeting_id, [2])
    result = await preflight_registration(db, meeting_id, user_id=10, local_tz=local_tz)
    assert result.meeting is None
    assert "waitlist" in result.error.lower()


@pytest.mark.asyncio
async def test_preflight_registration_success(db, local_tz):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id)
    result = await preflight_registration(db, meeting_id, user_id=10, local_tz=local_tz)
    assert result.meeting is not None
    assert result.error is None
