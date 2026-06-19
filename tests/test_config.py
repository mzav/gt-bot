"""Tests for configuration loading."""
from __future__ import annotations

from datetime import time

import pytest

from bot.config import Settings, load_settings


def test_parse_time_valid():
    assert Settings.parse_time("14:30", time(9, 0)) == time(14, 30)


def test_parse_time_invalid_returns_default():
    assert Settings.parse_time("invalid", time(9, 0)) == time(9, 0)
    assert Settings.parse_time("", time(10, 0)) == time(10, 0)


def test_announce_config_defaults():
    settings = Settings(telegram_bot_token="token")
    config = settings.announce_config()
    assert config.days == [1, 15]
    assert config.time_of_day == time(10, 0)


def test_announce_config_custom():
    settings = Settings(
        telegram_bot_token="token",
        announce_days=[5, 20],
        announce_time="08:15",
    )
    config = settings.announce_config()
    assert config.days == [5, 20]
    assert config.time_of_day == time(8, 15)


def test_announce_config_filters_invalid_days():
    settings = Settings(
        telegram_bot_token="token",
        announce_days=[0, 32, 10],
    )
    config = settings.announce_config()
    assert config.days == [10]


def test_daily_check_time_parsed():
    settings = Settings(telegram_bot_token="token", daily_check_time="07:45")
    assert settings.daily_check_time_parsed() == time(7, 45)


def test_tzinfo():
    settings = Settings(telegram_bot_token="token", tz="Europe/Berlin")
    assert str(settings.tzinfo()) == "Europe/Berlin"


def test_load_settings(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("BOT_USERNAME", "@mybot")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setenv("ANNOUNCEMENTS_CHANNEL_ID", "-100999")
    monkeypatch.setenv("ANNOUNCE_DAYS", "1,15,20")
    monkeypatch.setenv("ANNOUNCE_TIME", "11:30")
    monkeypatch.setenv("DAILY_CHECK_TIME", "08:00")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("NOTIFY_BATCH_THRESHOLD", "5")
    monkeypatch.setenv("NOTIFY_BATCH_INTERVAL_MINUTES", "15")
    monkeypatch.setenv("ADMIN_USER_IDS", "1,2")
    monkeypatch.setenv("WAITLIST_OFFER_EXPIRY_HOURS", "12")
    monkeypatch.setenv("WAITLIST_OFFER_CHECK_INTERVAL_MINUTES", "10")

    settings = load_settings()
    assert settings.telegram_bot_token == "test-token-123"
    assert settings.telegram_bot_username == "mybot"
    assert settings.database_url == "sqlite+aiosqlite:///:memory:"
    assert settings.tz == "UTC"
    assert settings.announcements_channel_id == -100999
    assert settings.announce_days == [1, 15, 20]
    assert settings.announce_time == "11:30"
    assert settings.daily_check_time == "08:00"
    assert settings.log_level == "DEBUG"
    assert settings.notify_batch_threshold == 5
    assert settings.notify_batch_interval_minutes == 15
    assert settings.admin_user_ids == [1, 2]
    assert settings.waitlist_offer_expiry_hours == 12
    assert settings.waitlist_offer_check_interval_minutes == 10


def test_load_settings_missing_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "")
    settings = load_settings()
    assert settings.telegram_bot_token == ""
