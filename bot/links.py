"""Deep-link helpers for meeting navigation."""
from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from typing import Literal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_PUBLIC_TOKEN_ALPHABET = string.ascii_letters + string.digits + "_-"
_PUBLIC_TOKEN_LENGTH = 12
_PUBLIC_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_MEETING_PAYLOAD_RE = re.compile(r"^m_([A-Za-z0-9_-]{6,64})$")


def generate_meeting_public_token(length: int = _PUBLIC_TOKEN_LENGTH) -> str:
    """Return a short, URL-safe, unpredictable meeting token."""
    return "".join(secrets.choice(_PUBLIC_TOKEN_ALPHABET) for _ in range(length))


def build_telegram_user_link(user_id: int, username: str | None = None) -> str:
    """Build a link to a Telegram user profile."""
    if username:
        return f"https://t.me/{username.lstrip('@')}"
    return f"tg://user?id={user_id}"


def build_meeting_deep_link(bot_username: str, public_token: str) -> str:
    """Build a Telegram deep link that opens the bot for a specific meeting."""
    username = bot_username.lstrip("@")
    if not username:
        raise ValueError("bot username is required to build meeting deep links")
    if not _PUBLIC_TOKEN_RE.match(public_token):
        raise ValueError("invalid public token for deep link")
    return f"https://t.me/{username}?start=m_{public_token}"


@dataclass(frozen=True)
class StartPayload:
    """Parsed /start deep-link payload."""

    type: Literal["empty", "meeting", "unknown"]
    public_token: str | None = None
    raw: str | None = None


def parse_start_payload(payload: str | None) -> StartPayload:
    """Parse a Telegram /start payload into a typed result."""
    if not payload:
        return StartPayload(type="empty")
    match = _MEETING_PAYLOAD_RE.match(payload)
    if match:
        return StartPayload(type="meeting", public_token=match.group(1))
    return StartPayload(type="unknown", raw=payload)


def meeting_channel_cta_keyboard(deep_link: str, *, label: str = "Регистрация") -> InlineKeyboardMarkup:
    """Inline keyboard with a single URL button for channel announcements."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text=label, url=deep_link)]])
