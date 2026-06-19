"""Shared utility functions for the bot package."""
from __future__ import annotations

import re
from datetime import datetime
from html import unescape

from dateutil import tz
from telegram import Message

_A_TAG_RE = re.compile(r'<a href="([^"]+)">([^<]*)</a>')
_TAG_RE = re.compile(r"<[^>]+>")


def utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(tz.UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime has UTC timezone attached.
    
    SQLite loses timezone info when storing datetimes, so we need to
    reattach UTC when reading them back.
    
    Args:
        dt: A datetime that may or may not have timezone info.
        
    Returns:
        The same datetime with UTC timezone attached if it was naive.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz.UTC)
    return dt


def message_text_as_html(message: Message) -> str:
    """Convert Telegram message text and entities to Telegram HTML."""
    html = message.text_html
    if html is not None:
        return html.strip()
    return (message.text or "").strip()


def telegram_html_to_plain(text: str) -> str:
    """Strip Telegram HTML formatting for plain-text contexts."""
    if not text:
        return text

    def _link_repl(match: re.Match[str]) -> str:
        url = unescape(match.group(1))
        label = unescape(match.group(2))
        if label.strip() == url.strip():
            return url
        return f"{label} ({url})"

    plain = _A_TAG_RE.sub(_link_repl, text)
    plain = _TAG_RE.sub("", plain)
    return unescape(plain).strip()

