"""Shared pytest fixtures for gt-bot tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from dateutil import tz
from telegram import User as TgUser

from bot.config import Settings
from bot.handlers import BotApp
from bot.models import WaitlistStatus
from bot.storage import Database
from bot.waitlist import WaitlistService

TEST_CHANNEL_ID = -100123


def make_context(*, status: str = "member", raise_error: Exception | None = None):
    """Build a mock PTB context with get_chat_member configured."""
    context = MagicMock()
    context.user_data = {}
    context.args = []
    if raise_error is not None:
        context.bot.get_chat_member = AsyncMock(side_effect=raise_error)
    else:
        member = MagicMock()
        member.status = status
        context.bot.get_chat_member = AsyncMock(return_value=member)
    return context


def make_callback_update(callback_data: str, *, user_id: int = 10):
    user = TgUser(
        id=user_id,
        is_bot=False,
        first_name="Test",
        username="testuser",
    )
    message = MagicMock()
    message.reply_text = AsyncMock()
    message.edit_text = AsyncMock()
    message.edit_message_text = AsyncMock()
    cq = MagicMock()
    cq.data = callback_data
    cq.message = message
    cq.answer = AsyncMock()
    cq.edit_message_text = AsyncMock()
    cq.edit_message_reply_markup = AsyncMock()
    update = MagicMock()
    update.callback_query = cq
    update.effective_user = user
    return update


def make_message_update(text: str, *, user_id: int = 10):
    user = TgUser(
        id=user_id,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser",
    )
    message = MagicMock()
    message.text = text
    message.text_html = None
    message.reply_text = AsyncMock()
    message.edit_text = AsyncMock()
    update = MagicMock()
    update.effective_message = message
    update.effective_user = user
    update.message = message
    return update


def make_command_update(*args: str, user_id: int = 10):
    context = make_context()
    context.args = list(args)
    update = make_message_update("", user_id=user_id)
    return update, context


def make_app(
    db,
    waitlist,
    *,
    admin_user_ids: list[int] | None = None,
    channel_id: int | None = TEST_CHANNEL_ID,
    scheduler=None,
) -> BotApp:
    settings = Settings(
        telegram_bot_token="test-token",
        tz="Europe/Berlin",
        announcements_channel_id=channel_id,
        admin_user_ids=admin_user_ids or [],
    )
    if scheduler is None:
        scheduler = MagicMock()
        scheduler.on_participant_change = AsyncMock()
        scheduler.plan_urgent_announcement = AsyncMock()
        scheduler.run_announcement_now = AsyncMock()
        scheduler._bot = MagicMock()
    app = BotApp(settings, db, scheduler, waitlist)
    app.bot_username = "TestBot"
    return app


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
    topic: str = "Test Meeting",
    max_participants: int = 2,
    start_at_utc: datetime | None = None,
    end_at_utc: datetime | None = None,
    registration_starts_at_utc: datetime | None = None,
) -> int:
    meeting = await db.create_meeting(
        host_id=host_id,
        topic=topic,
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
