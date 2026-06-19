"""Unit tests for access control helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.access_control import has_community_access, is_channel_member
from bot.config import Settings


def _settings(*, admin_ids: list[int] | None = None) -> Settings:
    return Settings(
        telegram_bot_token="token",
        admin_user_ids=admin_ids or [],
    )


def test_has_community_access_admin_bypass():
    settings = _settings(admin_ids=[99])
    assert has_community_access(settings, 99, is_member=False) is True


def test_has_community_access_member():
    settings = _settings()
    assert has_community_access(settings, 1, is_member=True) is True


def test_has_community_access_non_member():
    settings = _settings()
    assert has_community_access(settings, 1, is_member=False) is False


@pytest.mark.asyncio
async def test_is_channel_member_active():
    bot = MagicMock()
    member = MagicMock()
    member.status = "member"
    bot.get_chat_member = AsyncMock(return_value=member)
    assert await is_channel_member(bot, -100123, 1) is True


@pytest.mark.asyncio
async def test_is_channel_member_left():
    bot = MagicMock()
    member = MagicMock()
    member.status = "left"
    bot.get_chat_member = AsyncMock(return_value=member)
    assert await is_channel_member(bot, -100123, 1) is False


@pytest.mark.asyncio
async def test_is_channel_member_kicked():
    bot = MagicMock()
    member = MagicMock()
    member.status = "kicked"
    bot.get_chat_member = AsyncMock(return_value=member)
    assert await is_channel_member(bot, -100123, 1) is False


@pytest.mark.asyncio
async def test_is_channel_member_error_fails_closed():
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(side_effect=RuntimeError("network"))
    assert await is_channel_member(bot, -100123, 1) is False
