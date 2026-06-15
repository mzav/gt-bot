"""Mindful registration confirmation flow — messages, keyboards, and preflight."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .models import Meeting
from .storage import is_registration_open
from .utils import ensure_utc

if TYPE_CHECKING:
    from .storage import Database


@dataclass
class PreflightResult:
    meeting: Meeting | None
    error: str | None = None


def format_step1(meeting: Meeting, local_tz) -> str:
    start_local = ensure_utc(meeting.start_at_utc).astimezone(local_tz)
    date_str = start_local.strftime("%d.%m.%Y")
    time_str = start_local.strftime("%H:%M")
    location = meeting.location or "TBA"
    return (
        "Перед записью давай быстро проверим пару вещей 🌿\n\n"
        "Проверь, пожалуйста, что в это время у тебя правда нет других планов.\n\n"
        f"🗓 {date_str}, {time_str}\n"
        f"📍 {location}\n\n"
        "Ты свободна в это время?"
    )


def format_step2() -> str:
    return (
        "Иногда мы записываемся с вдохновением, а потом понимаем, "
        "что сил на встречу уже не осталось - и это очень по-человечески.\n\n"
        "Пожалуйста, подумай: тебе правда сейчас окей добавить эту встречу в свои планы?"
    )


def format_step3() -> str:
    return (
        "Спасибо 💛\n\n"
        "Последнее: хост готовится заранее, планирует место и вкладывает своё время. "
        "Если планы изменятся, пожалуйста, отмени участие в боте как можно раньше - "
        "так место сможет занять кто-то ещё.\n\n"
        "Записать тебя на встречу?"
    )


def format_cancelled() -> str:
    return (
        "Всё хорошо 🌿 Лучше записаться тогда, когда ты уверена.\n\n"
        "Ты можешь вернуться к этой встрече позже, если места ещё будут."
    )


def format_unavailable() -> str:
    return (
        "К сожалению, эта встреча больше недоступна для записи.\n"
        "Возможно, она отменена, уже прошла или регистрация закрыта."
    )


def format_full() -> str:
    return (
        "К сожалению, пока ты думала, все места заняли 😔\n\n"
        "Но ты можешь встать в waitlist — если место освободится, бот напишет тебе."
    )


def format_offer_short_confirm() -> str:
    return (
        "Перед подтверждением проверь, пожалуйста: "
        "ты точно свободна в это время и у тебя есть силы на эту встречу?"
    )


def build_step1_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text="Да, я свободна", callback_data=f"reg_s1_yes:{meeting_id}"),
        InlineKeyboardButton(text="Нет, вернуться", callback_data=f"reg_s1_no:{meeting_id}"),
    ]])


def build_step2_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text="Да, я хочу прийти", callback_data=f"reg_s2_yes:{meeting_id}"),
        InlineKeyboardButton(text="Пока не уверена", callback_data=f"reg_s2_no:{meeting_id}"),
    ]])


def build_step3_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text="Да, записаться", callback_data=f"reg_s3_yes:{meeting_id}"),
        InlineKeyboardButton(text="Нет, вернуться", callback_data=f"reg_s3_no:{meeting_id}"),
    ]])


def build_cancelled_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text="Вернуться ко встрече", callback_data=f"details:{meeting_id}"),
        InlineKeyboardButton(text="Показать другие встречи", callback_data="show_upcoming"),
    ]])


def build_unavailable_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text="Показать другие встречи", callback_data="show_upcoming"),
    ]])


def build_full_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text="В waitlist", callback_data=f"waitlist_join:{meeting_id}"),
        InlineKeyboardButton(text="Показать другие встречи", callback_data="show_upcoming"),
    ]])


def build_offer_confirm_keyboard(entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text="Да, записаться", callback_data=f"offer_confirm:{entry_id}"),
        InlineKeyboardButton(text="Нет, спасибо", callback_data=f"offer_decline:{entry_id}"),
    ]])


async def preflight_registration(
    db: Database,
    meeting_id: int,
    user_id: int,
    local_tz,
) -> PreflightResult:
    """Check whether the user can start the registration confirmation flow."""
    meeting = await db.get_meeting(meeting_id)
    if meeting is None or meeting.canceled_at is not None:
        return PreflightResult(None, format_unavailable())
    now_utc = datetime.now(timezone.utc)
    if not await db.is_meeting_open(meeting_id, now_utc):
        return PreflightResult(None, format_unavailable())
    if not is_registration_open(meeting, now_utc):
        when = ensure_utc(meeting.registration_starts_at_utc).astimezone(local_tz)
        return PreflightResult(
            None,
            f"Регистрация откроется {when:%d.%m.%Y %H:%M}.",
        )
    if await db.is_registered(meeting_id, user_id):
        return PreflightResult(None, "You are already registered.")
    available = await db.available_spots(meeting_id)
    if available <= 0:
        return PreflightResult(None, format_full())
    return PreflightResult(meeting)
