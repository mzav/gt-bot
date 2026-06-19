"""Channel membership access control for community bot features."""
from __future__ import annotations

import logging

from telegram import Bot

from .log_context import log_event, user_log_fields

from .config import Settings

logger = logging.getLogger(__name__)

ALLOWED_MEMBER_STATUSES = frozenset({"creator", "administrator", "member"})


def has_community_access(settings: Settings, user_id: int, *, is_member: bool) -> bool:
    """Return whether the user may use community bot features."""
    if user_id in settings.admin_user_ids:
        return True
    return is_member


async def is_channel_member(bot: Bot, channel_id: int, user_id: int) -> bool:
    """Check whether user_id is an active member of the announcement channel."""
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return str(member.status) in ALLOWED_MEMBER_STATUSES
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "access_check_failed",
            channel_id=channel_id,
            error=type(exc).__name__,
            **user_log_fields(user_id=user_id),
        )
        return False
