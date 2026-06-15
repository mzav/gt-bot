"""Tests for Telegram text/entity conversion helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from telegram import Chat, Message, MessageEntity, User as TgUser
from telegram.constants import MessageEntityType

from bot.utils import message_text_as_html, telegram_html_to_plain


def _make_message(*, text: str, entities: list[MessageEntity] | None = None) -> Message:
    user = TgUser(id=1, is_bot=False, first_name="Test")
    chat = Chat(id=1, type="private")
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        text=text,
        entities=entities or [],
        from_user=user,
    )


def test_message_text_as_html_text_link():
    text = "подробнее"
    message = _make_message(
        text=text,
        entities=[
            MessageEntity(
                type=MessageEntityType.TEXT_LINK,
                offset=0,
                length=len(text),
                url="https://example.com",
            )
        ],
    )
    assert message_text_as_html(message) == '<a href="https://example.com">подробнее</a>'


def test_message_text_as_html_plain_text():
    message = _make_message(text="обычный текст")
    assert message_text_as_html(message) == "обычный текст"


def test_telegram_html_to_plain_link():
    html = '<a href="https://example.com">подробнее</a>'
    assert telegram_html_to_plain(html) == "подробнее (https://example.com)"


def test_telegram_html_to_plain_bold_and_link():
    html = 'Текст <b>жирный</b> и <a href="https://maps.example.com">карта</a>'
    assert telegram_html_to_plain(html) == "Текст жирный и карта (https://maps.example.com)"


def test_telegram_html_to_plain_unchanged_plain_text():
    assert telegram_html_to_plain("Berlin Cafe") == "Berlin Cafe"
