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
    database_url: str = "sqlite+aiosqlite:///./gtbot.db"
    tz: str = "Europe/Berlin"

    announcements_channel_id: int | None = None
    admin_user_ids: List[int] = Field(default_factory=list)
    bot_owner_id: int | None = None

    announce_days: List[int] = Field(default_factory=lambda: [1, 15])
    announce_time: str = "10:00"

    log_level: str = "INFO"

    def tzinfo(self) -> zoneinfo.ZoneInfo:
        """Return ZoneInfo instance for the configured time zone."""
        return zoneinfo.ZoneInfo(self.tz)

    def announce_config(self) -> AnnounceConfig:
        """Build an AnnounceConfig from the flat settings values.

        Returns:
            AnnounceConfig: Validated config with parsed time and filtered days.
        """
        # Parse HH:MM
        hh, mm = (self.announce_time.split(":") + ["0"])[:2]
        try:
            t = time(int(hh), int(mm))
        except Exception:
            t = time(10, 0)
        # Validate days (1..31)
        days: List[int] = []
        for part in self.announce_days:
            try:
                d = int(part)
                if 1 <= d <= 31:
                    days.append(d)
            except Exception:
                continue
        if not days:
            days = [1, 15]
        return AnnounceConfig(days=days, time_of_day=t)


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
    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./gtbot.db")
    tz_name = os.getenv("TIMEZONE", "Europe/Berlin")
    channel_id = os.getenv("ANNOUNCEMENTS_CHANNEL_ID")
    channel_id_int = int(channel_id) if channel_id and channel_id.strip() else None
    # admin_ids_env = os.getenv("ADMIN_USER_IDS", "")
    # admin_ids = [int(x) for x in admin_ids_env.split(",") if x.strip().isdigit()]
    # owner_id_env = os.getenv("BOT_OWNER_ID")
    # owner_id = int(owner_id_env) if owner_id_env and owner_id_env.strip().isdigit() else None
    announce_days_env = os.getenv("ANNOUNCE_DAYS", "1,15")
    announce_days = [int(x) for x in announce_days_env.split(",") if x.strip().isdigit()]
    announce_time = os.getenv("ANNOUNCE_TIME", "10:00")
    log_level = os.getenv("LOG_LEVEL", "INFO")

    return Settings(
        telegram_bot_token=token,
        database_url=db_url,
        tz=tz_name,
        announcements_channel_id=channel_id_int,
        # admin_user_ids=admin_ids,
        # bot_owner_id=owner_id,
        announce_days=announce_days,
        announce_time=announce_time,
        log_level=log_level,
    )
