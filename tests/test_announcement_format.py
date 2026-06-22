"""Tests for channel digest announcement formatting."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from dateutil import tz

from bot.models import Meeting, User
from bot.scheduler import (
    _format_meeting_card,
    _format_new_meeting_card,
    _format_spots_line,
    _format_today_card,
)


@pytest.fixture
def berlin_tz():
    return tz.gettz("Europe/Berlin")


def _make_meeting(**overrides) -> Meeting:
    defaults = {
        "id": 1,
        "topic": "Дойти и не сломаться: как пройти путь Камино",
        "description": "",
        "start_at_utc": datetime(2026, 8, 19, 17, 0, 0, tzinfo=timezone.utc),
        "end_at_utc": datetime(2026, 8, 19, 20, 0, 0, tzinfo=timezone.utc),
        "max_participants": 10,
        "location": None,
        "created_by": 42,
    }
    defaults.update(overrides)
    return Meeting(**defaults)


def _make_host(**overrides) -> User:
    defaults = {"id": 42, "name": "Таня Зайнуллина", "username": "tanya"}
    defaults.update(overrides)
    return User(**defaults)


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        (0, "🌻 Мест нет"),
        (1, "🌻 Осталось 1 место"),
        (2, "🌻 Осталось 2 места"),
        (4, "🌻 Осталось 4 места"),
        (5, "🌻 Осталось 5 мест"),
        (11, "🌻 Осталось 11 мест"),
        (21, "🌻 Осталось 21 место"),
    ],
)
def test_format_spots_line(available, expected):
    assert _format_spots_line(available) == expected


def test_format_meeting_card_with_spots(berlin_tz):
    meeting = _make_meeting()
    host = _make_host()
    card = _format_meeting_card(
        meeting, 2, host, berlin_tz, deep_link="https://t.me/bot?start=m_abc"
    )
    assert "<b>19.08 (среда) 19:00 - 22:00</b>" in card
    assert "Дойти и не сломаться" in card
    assert 'Ведет <a href="https://t.me/tanya">Таня Зайнуллина</a>.' in card
    assert "🌻 Осталось 2 места" in card
    assert '✏️<a href="https://t.me/bot?start=m_abc">Регистрация</a>' in card


def test_format_meeting_card_full_shows_waitlist(berlin_tz):
    meeting = _make_meeting()
    host = _make_host()
    card = _format_meeting_card(
        meeting, 0, host, berlin_tz, deep_link="https://t.me/bot?start=m_abc"
    )
    assert "🌻 Мест нет" in card
    assert '✏️<a href="https://t.me/bot?start=m_abc">Встать в waitlist</a>' in card


def test_format_meeting_card_includes_location_not_description(berlin_tz):
    meeting = _make_meeting(
        description="Каждый плетет/расписывает что хочет",
        location="Dorotheenstädtischer Friedhof I",
    )
    host = _make_host(username=None)
    card = _format_meeting_card(meeting, 5, host, berlin_tz)
    assert "Каждый плетет/расписывает что хочет" not in card
    assert "📍 Dorotheenstädtischer Friedhof I" in card
    assert 'href="tg://user?id=42"' in card


def test_format_new_meeting_card(berlin_tz):
    meeting = _make_meeting(
        topic="Проверим отправку анонса",
        description="проверяем описание",
        start_at_utc=datetime(2026, 6, 17, 9, 15, 0, tzinfo=timezone.utc),
        end_at_utc=datetime(2026, 6, 17, 11, 45, 0, tzinfo=timezone.utc),
        location="Cafe Cafe Cafe",
        max_participants=10,
    )
    host = _make_host()
    card = _format_new_meeting_card(meeting, 10, host, berlin_tz)
    assert card.startswith("<i>Среда, 17 июня 11:15 - 13:45</i>\n\n")
    assert "<b>Проверим отправку анонса</b>" in card
    assert "проверяем описание" in card
    assert '<i>🤍 Организует <a href="https://t.me/tanya">Таня Зайнуллина</a></i>' in card
    assert "<i>🌼 Осталось 10 мест</i>" in card
    assert "<i>📍 Cafe Cafe Cafe</i>" in card
    assert "New meeting added" not in card


def test_format_new_meeting_card_includes_registration_link(berlin_tz):
    meeting = _make_meeting()
    host = _make_host()
    card = _format_new_meeting_card(
        meeting, 10, host, berlin_tz, deep_link="https://t.me/bot?start=m_abc"
    )
    assert '<i>✏️<a href="https://t.me/bot?start=m_abc">Регистрация</a></i>' in card


def test_format_today_card_includes_registration_link(berlin_tz):
    meeting = _make_meeting(description="Bring snacks")
    host = _make_host()
    card = _format_today_card(
        meeting,
        2,
        host,
        berlin_tz,
        deep_link="https://t.me/bot?start=m_abc",
    )
    assert "🔔 <b>Встреча сегодня —" in card
    assert "Bring snacks" in card
    assert '<i>🤍 Организует <a href="https://t.me/tanya">Таня Зайнуллина</a></i>' in card
    assert "<i>🌼 Осталось 2 места</i>" in card
    assert "participants" not in card
    assert '✏️<a href="https://t.me/bot?start=m_abc">Регистрация</a>' in card


def test_format_today_card_shows_available_spots_not_participant_count(berlin_tz):
    meeting = _make_meeting(max_participants=10)
    host = _make_host()
    card = _format_today_card(meeting, 10, host, berlin_tz)
    assert "<i>🌼 Осталось 10 мест</i>" in card
    assert "👥" not in card


def test_format_today_card_full_meeting_shows_no_spots(berlin_tz):
    meeting = _make_meeting()
    host = _make_host()
    card = _format_today_card(meeting, 0, host, berlin_tz)
    assert "<i>🌼 Мест нет</i>" in card


def test_format_new_meeting_card_with_html_description_and_location(berlin_tz):
    meeting = _make_meeting(
        description='<a href="https://example.com">подробнее</a>',
        location='<a href="https://maps.example.com">Cafe</a>',
    )
    host = _make_host()
    card = _format_new_meeting_card(meeting, 5, host, berlin_tz)
    assert '<a href="https://example.com">подробнее</a>' in card
    assert '<i>📍 <a href="https://maps.example.com">Cafe</a></i>' in card
