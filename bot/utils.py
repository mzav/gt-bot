"""Shared utility functions for the bot package."""
from __future__ import annotations

from datetime import datetime

from dateutil import tz


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

