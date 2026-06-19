"""Tests for meeting deep-link helpers."""
from __future__ import annotations

import re
import string

import pytest

from bot.links import (
    build_meeting_deep_link,
    build_telegram_user_link,
    generate_meeting_public_token,
    meeting_channel_cta_keyboard,
    parse_start_payload,
)

_TOKEN_ALPHABET = set(string.ascii_letters + string.digits + "_-")


def test_generate_meeting_public_token_default_length():
    token = generate_meeting_public_token()
    assert len(token) == 12
    assert set(token).issubset(_TOKEN_ALPHABET)


def test_generate_meeting_public_token_custom_length():
    token = generate_meeting_public_token(20)
    assert len(token) == 20


def test_generate_meeting_public_token_uniqueness():
    tokens = {generate_meeting_public_token() for _ in range(50)}
    assert len(tokens) == 50


def test_build_telegram_user_link_with_username():
    assert build_telegram_user_link(1, "@alice") == "https://t.me/alice"
    assert build_telegram_user_link(1, "alice") == "https://t.me/alice"


def test_build_telegram_user_link_without_username():
    assert build_telegram_user_link(42) == "tg://user?id=42"


def test_build_meeting_deep_link_valid():
    url = build_meeting_deep_link("@TestBot", "abc123_token")
    assert url == "https://t.me/TestBot?start=m_abc123_token"


def test_build_meeting_deep_link_strips_at_sign():
    url = build_meeting_deep_link("TestBot", "abc123")
    assert url == "https://t.me/TestBot?start=m_abc123"


def test_build_meeting_deep_link_missing_username():
    with pytest.raises(ValueError, match="bot username is required"):
        build_meeting_deep_link("", "abc123")


def test_build_meeting_deep_link_invalid_token():
    with pytest.raises(ValueError, match="invalid public token"):
        build_meeting_deep_link("TestBot", "bad token!")


def test_parse_start_payload_empty():
    result = parse_start_payload(None)
    assert result.type == "empty"
    assert result.public_token is None

    result = parse_start_payload("")
    assert result.type == "empty"


def test_parse_start_payload_meeting():
    result = parse_start_payload("m_abc123_token")
    assert result.type == "meeting"
    assert result.public_token == "abc123_token"


def test_parse_start_payload_unknown():
    result = parse_start_payload("hello")
    assert result.type == "unknown"
    assert result.raw == "hello"


def test_meeting_channel_cta_keyboard():
    markup = meeting_channel_cta_keyboard("https://t.me/bot?start=m_x", label="Join")
    btn = markup.inline_keyboard[0][0]
    assert btn.text == "Join"
    assert btn.url == "https://t.me/bot?start=m_x"
