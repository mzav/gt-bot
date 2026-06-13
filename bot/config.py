"""Configuration loading for the Girl Talk Berlin bot.

Defines Pydantic models for settings and reads environment variables (via
python-dotenv when available).
"""
from __future__ import annotations

import logging
import os
from typing import List

from pydantic import BaseModel, Field
from datetime import time
import zoneinfo
import os
from dotenv import load_dotenv


class AnnounceConfig(BaseModel):
    """Configuration for twice-monthly announcement scheduling.

    Attributes:
        days: Days of month (1..31) to send announcements.
        time_of_day: Local time of day to post the announcement.
    """
    days: List[int] = Field(default_factory=lambda: [1, 15])
    time_of_day: time = time(10, 0)


class Settings(BaseModel):
    """Typed configuration values for the bot.

    Values are primarily read from environment variables. See README for the
    list of supported variables.
    """
    telegram_bot_token: str
    telegram_bot_username: str | None = None
    database_url: str = "sqlite+aiosqlite:////data/gtbot.db"
    tz: str = "Europe/Berlin"

    announcements_channel_id: int | None = None

    announce_days: List[int] = Field(default_factory=lambda: [1, 15])
    announce_time: str = "10:00"
    daily_check_time: str = "09:00"

    log_level: str = "INFO"

    # Participant change notifications: send instant DMs below this threshold,
    # batch into a digest at/above it.
    notify_batch_threshold: int = 10
    notify_batch_interval_minutes: int = 30

    # Telegram user IDs allowed to use privileged commands (e.g. /force_summary).
    admin_user_ids: List[int] = Field(default_factory=list)

    waitlist_offer_expiry_hours: int = 6
    waitlist_offer_check_interval_minutes: int = 15

    def tzinfo(self) -> zoneinfo.ZoneInfo:
        """Return ZoneInfo instance for the configured time zone."""
        return zoneinfo.ZoneInfo(self.tz)

    @staticmethod
    def parse_time(value: str, default: time) -> time:
        """Parse an HH:MM string, returning default on failure."""
        try:
            hh, mm = (value.split(":") + ["0"])[:2]
            return time(int(hh), int(mm))
        except Exception:
            return default

    def announce_config(self) -> AnnounceConfig:
        """Build an AnnounceConfig from the flat settings values."""
        t = self.parse_time(self.announce_time, time(10, 0))
        days: List[int] = [
            d for part in self.announce_days
            if (d := int(part)) and 1 <= d <= 31
        ]
        return AnnounceConfig(days=days or [1, 15], time_of_day=t)

    def daily_check_time_parsed(self) -> time:
        """Return the parsed daily check time, defaulting to 09:00."""
        return self.parse_time(self.daily_check_time, time(9, 0))


def load_settings() -> Settings:
    """Load Settings from environment variables (optionally from .env).

    Returns:
        Settings: Parsed configuration model.
    """
    # Load from .env if present
    load_dotenv()
    # Parse env
    token = os.getenv("BOT_TOKEN")
    if not token:
        logging.getLogger(__name__).warning("BOT_TOKEN is not set; the bot will not start properly.")
        token = ""  # allow object creation; app will fail to start if used
    bot_username = os.getenv("BOT_USERNAME", "").strip().lstrip("@") or None
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./gtbot.db")
    tz_name = os.getenv("TIMEZONE", "Europe/Berlin")
    channel_id = os.getenv("ANNOUNCEMENTS_CHANNEL_ID")
    channel_id_int = int(channel_id) if channel_id and channel_id.strip() else None
    announce_days_env = os.getenv("ANNOUNCE_DAYS", "1,15")
    announce_days = [int(x) for x in announce_days_env.split(",") if x.strip().isdigit()]
    announce_time = os.getenv("ANNOUNCE_TIME", "10:00")
    daily_check_time = os.getenv("DAILY_CHECK_TIME", "09:00")
    log_level = os.getenv("LOG_LEVEL", "INFO")
    notify_batch_threshold = int(os.getenv("NOTIFY_BATCH_THRESHOLD", "10"))
    notify_batch_interval_minutes = int(os.getenv("NOTIFY_BATCH_INTERVAL_MINUTES", "30"))
    admin_ids_env = os.getenv("ADMIN_USER_IDS", "")
    admin_user_ids = [int(x) for x in admin_ids_env.split(",") if x.strip().lstrip("-").isdigit()]
    waitlist_offer_expiry_hours = int(os.getenv("WAITLIST_OFFER_EXPIRY_HOURS", "6"))
    waitlist_offer_check_interval_minutes = int(os.getenv("WAITLIST_OFFER_CHECK_INTERVAL_MINUTES", "15"))

    return Settings(
        telegram_bot_token=token,
        telegram_bot_username=bot_username,
        database_url=db_url,
        tz=tz_name,
        announcements_channel_id=channel_id_int,
        announce_days=announce_days,
        announce_time=announce_time,
        daily_check_time=daily_check_time,
        log_level=log_level,
        notify_batch_threshold=notify_batch_threshold,
        notify_batch_interval_minutes=notify_batch_interval_minutes,
        admin_user_ids=admin_user_ids,
        waitlist_offer_expiry_hours=waitlist_offer_expiry_hours,
        waitlist_offer_check_interval_minutes=waitlist_offer_check_interval_minutes,
    )
