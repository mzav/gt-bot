"""Structured logging helpers with user and flow correlation context."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

_FLOW_ID_KEY = "flow_id"


def format_username(username: str | None) -> str:
    """Return @username or '-' when missing."""
    if not username:
        return "-"
    return username if username.startswith("@") else f"@{username}"


def user_fields(update: Update | None) -> dict[str, str | int]:
    """Extract user and update identifiers from a Telegram update."""
    fields: dict[str, str | int] = {}
    if update is None:
        return fields
    if update.update_id is not None:
        fields["update_id"] = update.update_id
    user = update.effective_user
    if user is not None:
        fields["user_id"] = user.id
        fields["username"] = format_username(user.username)
        if user.full_name:
            fields["name"] = user.full_name
    chat = update.effective_chat
    if chat is not None:
        fields["chat_id"] = chat.id
    return fields


def flow_id(context: ContextTypes.DEFAULT_TYPE | None) -> str | None:
    """Return the current multi-step flow correlation id, if any."""
    if context is None:
        return None
    value = context.user_data.get(_FLOW_ID_KEY)
    return str(value) if value else None


def ensure_flow_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Create or return the flow correlation id for a multi-step conversation."""
    existing = flow_id(context)
    if existing:
        return existing
    new_id = uuid.uuid4().hex[:12]
    context.user_data[_FLOW_ID_KEY] = new_id
    return new_id


def clear_flow_id(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove the flow correlation id when a conversation ends."""
    context.user_data.pop(_FLOW_ID_KEY, None)


def _format_fields(fields: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def log_event(
    logger: logging.Logger,
    level: int,
    action: str,
    message: str = "",
    *,
    update: Update | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
    exc_info: bool = False,
    **fields: Any,
) -> None:
    """Log a single line with action, user context, and optional extra fields."""
    payload: dict[str, Any] = {"action": action}
    payload.update(user_fields(update))
    flow = flow_id(context)
    if flow:
        payload["flow_id"] = flow
    payload.update(fields)
    text = _format_fields(payload)
    if message:
        text = f"{text} {message}"
    logger.log(level, text, exc_info=exc_info)


def user_log_fields(
    *,
    user_id: int | None = None,
    username: str | None = None,
    name: str | None = None,
) -> dict[str, str | int]:
    """Build user fields for background jobs without a Telegram update."""
    fields: dict[str, str | int] = {}
    if user_id is not None:
        fields["user_id"] = user_id
    fields["username"] = format_username(username)
    if name:
        fields["name"] = name
    return fields
