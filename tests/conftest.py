"""Shared pytest fixtures for gt-bot tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dateutil import tz

from bot.models import WaitlistStatus
from bot.storage import Database
from bot.waitlist import WaitlistService


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.engine.dispose()


@pytest.fixture
def local_tz():
    return tz.gettz("Europe/Berlin")


@pytest.fixture
async def waitlist(db, local_tz):
    return WaitlistService(
        db,
        offer_ttl=timedelta(hours=6),
        local_tz=local_tz,
    )


@pytest.fixture
def now_utc():
    return datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


async def create_host(db: Database, user_id: int = 1) -> int:
    await db.get_or_create_user(user_id, "Host User", "host")
    return user_id


async def create_meeting(
    db: Database,
    host_id: int,
    *,
    max_participants: int = 2,
    start_at_utc: datetime | None = None,
    end_at_utc: datetime | None = None,
    registration_starts_at_utc: datetime | None = None,
) -> int:
    meeting = await db.create_meeting(
        host_id=host_id,
        topic="Test Meeting",
        description="Test description",
        start_at_utc=start_at_utc or datetime(2026, 7, 1, 18, 0, 0, tzinfo=timezone.utc),
        end_at_utc=end_at_utc,
        registration_starts_at_utc=registration_starts_at_utc,
        max_participants=max_participants,
        location="Berlin",
    )
    return meeting.id


async def fill_meeting(db: Database, meeting_id: int, user_ids: list[int]) -> None:
    for i, uid in enumerate(user_ids):
        await db.get_or_create_user(uid, f"User {uid}", f"user{uid}")
        ok, _, _ = await db.register(meeting_id, uid)
        assert ok, f"failed to register user {uid}"
