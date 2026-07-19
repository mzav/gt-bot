"""Tests for the community calendar feed HTTP endpoint."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.calendar_feed_server import create_calendar_feed_app
from bot.config import Settings
from tests.conftest import create_host, create_meeting

FEED_TOKEN = "test-feed-token-secret"
BASE_URL = "https://example.test"
NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _feed_settings(**overrides) -> Settings:
    defaults = {
        "telegram_bot_token": "test-token",
        "calendar_feed_token": FEED_TOKEN,
        "calendar_feed_base_url": BASE_URL,
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
async def feed_client(db):
    settings = _feed_settings()
    app = create_calendar_feed_app(db=db, settings=settings, bot_username="TestBot")
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_valid_token_returns_ics(feed_client, db):
    host_id = await create_host(db)
    await create_meeting(db, host_id, topic="Upcoming Meetup")

    async with feed_client.get(f"/calendar/community/{FEED_TOKEN}.ics") as resp:
        assert resp.status == 200
        assert resp.content_type == "text/calendar"
        body = await resp.text()
        assert "BEGIN:VCALENDAR" in body
        assert "BEGIN:VEVENT" in body
        assert "Upcoming Meetup" in body


@pytest.mark.asyncio
async def test_invalid_token_returns_404(feed_client):
    async with feed_client.get("/calendar/community/wrong-token.ics") as resp:
        assert resp.status == 404


@pytest.mark.asyncio
async def test_missing_token_returns_404(feed_client):
    async with feed_client.get("/calendar/community/.ics") as resp:
        assert resp.status == 404


@pytest.mark.asyncio
async def test_canceled_meeting_excluded(feed_client, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, topic="Canceled Meetup")
    await db.cancel_meeting(meeting_id, NOW)

    async with feed_client.get(f"/calendar/community/{FEED_TOKEN}.ics") as resp:
        body = await resp.text()
        assert "Canceled Meetup" not in body
        assert "BEGIN:VEVENT" not in body


@pytest.mark.asyncio
async def test_past_meeting_excluded(feed_client, db):
    host_id = await create_host(db)
    await create_meeting(
        db,
        host_id,
        topic="Past Meetup",
        start_at_utc=datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    await create_meeting(
        db,
        host_id,
        topic="Future Meetup",
        start_at_utc=datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc),
    )

    async with feed_client.get(f"/calendar/community/{FEED_TOKEN}.ics") as resp:
        body = await resp.text()
        assert "Past Meetup" not in body
        assert "Future Meetup" in body


@pytest.mark.asyncio
async def test_edited_meeting_keeps_same_uid(feed_client, db):
    host_id = await create_host(db)
    meeting_id = await create_meeting(db, host_id, topic="Original Title")
    meeting = await db.get_meeting(meeting_id)
    assert meeting is not None
    token = meeting.public_token
    expected_uid = f"meeting-{token}@girltalkberlin.bot"

    await db.update_meeting(
        meeting_id,
        topic="Edited Title",
        description=meeting.description,
        start_at_utc=meeting.start_at_utc,
        end_at_utc=meeting.end_at_utc,
        registration_starts_at_utc=meeting.registration_starts_at_utc,
        max_participants=meeting.max_participants,
        location=meeting.location,
        photo_file_id=meeting.photo_file_id,
    )

    async with feed_client.get(f"/calendar/community/{FEED_TOKEN}.ics") as resp:
        body = await resp.text()
        assert f"UID:{expected_uid}" in body
        assert "SUMMARY:Edited Title" in body
