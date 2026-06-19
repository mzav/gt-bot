"""Tests for bot.log_context helpers."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telegram import Chat, Update, User as TgUser

from bot.log_context import (
    clear_flow_id,
    ensure_flow_id,
    flow_id,
    format_username,
    log_event,
    user_fields,
    user_log_fields,
)


def test_format_username():
    assert format_username(None) == "-"
    assert format_username("alice") == "@alice"
    assert format_username("@alice") == "@alice"


def test_user_fields_from_update():
    user = TgUser(id=42, is_bot=False, first_name="Alice", username="alice")
    chat = Chat(id=99, type="private")
    update = Update(update_id=123, message=MagicMock(from_user=user, chat=chat))
    fields = user_fields(update)
    assert fields["update_id"] == 123
    assert fields["user_id"] == 42
    assert fields["username"] == "@alice"
    assert fields["name"] == "Alice"
    assert fields["chat_id"] == 99


def test_user_fields_empty_update():
    assert user_fields(None) == {}


def test_flow_id_lifecycle():
    context = MagicMock()
    context.user_data = {}
    assert flow_id(context) is None
    fid = ensure_flow_id(context)
    assert len(fid) == 12
    assert flow_id(context) == fid
    assert ensure_flow_id(context) == fid
    clear_flow_id(context)
    assert flow_id(context) is None


def test_user_log_fields():
    fields = user_log_fields(user_id=1, username="bob", name="Bob")
    assert fields == {"user_id": 1, "username": "@bob", "name": "Bob"}


def test_log_event(caplog):
    user = TgUser(id=7, is_bot=False, first_name="T", username="tester")
    update = Update(update_id=5, message=MagicMock(from_user=user, chat=Chat(id=1, type="private")))
    context = MagicMock()
    context.user_data = {}
    ensure_flow_id(context)
    logger = __import__("logging").getLogger("test.log_context")
    with caplog.at_level("INFO", logger="test.log_context"):
        log_event(logger, __import__("logging").INFO, "test_action", update=update, context=context, meeting_id=3)
    assert len(caplog.records) == 1
    msg = caplog.records[0].message
    assert "action=test_action" in msg
    assert "user_id=7" in msg
    assert "username=@tester" in msg
    assert "update_id=5" in msg
    assert "flow_id=" in msg
    assert "meeting_id=3" in msg
