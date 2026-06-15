"""Cancellation reason flow — messages, keyboards, and preflight."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .meeting_format import format_meeting_time
from .models import Meeting
from .registration_confirmation import format_unavailable

if TYPE_CHECKING:
    from .storage import Database

MAX_OTHER_REASON_LEN = 500

LEAVE_OTHER_PENDING_KEY = "leave_other_pending"
LEAVE_OTHER_TEXT_KEY = "leave_other_text"


class CancellationReasonType:
    ILL = "ill"
    FAMILY = "family"
    OTHER = "other"

    ALL = frozenset({ILL, FAMILY, OTHER})


REASON_LABELS: dict[str, str] = {
    CancellationReasonType.ILL: "Я заболела",
    CancellationReasonType.FAMILY: "Семейные обстоятельства",
    CancellationReasonType.OTHER: "Другое, объясню далее",
}


@dataclass
class PreflightResult:
    meeting: Meeting | None
    error: str | None = None


def format_reason_prompt(meeting: Meeting, local_tz) -> str:
    when = format_meeting_time(meeting, local_tz, style="short")
    return (
        "Жаль, что планы изменились 🌿\n\n"
        f"📌 {meeting.topic}\n"
        f"📅 {when}\n\n"
        "Подскажи, пожалуйста, почему не получится прийти?"
    )


def format_other_reason_prompt() -> str:
    return (
        "Напиши коротко, почему не получается прийти.\n\n"
        f"Можно до {MAX_OTHER_REASON_LEN} символов."
    )


def format_final_confirm() -> str:
    return (
        "Спасибо, что предупредила 🌿\n\n"
        "Мы понимаем, что планы меняются и всякое случается. "
        "При этом хост готовится заранее и рассчитывает на участниц, "
        "поэтому частые отмены могут быть чувствительны для всей встречи.\n\n"
        "Чтобы встречи оставались комфортными и честными для всех, "
        "мы ведём статистику отмен. Если станет заметно, что человек часто "
        "записывается и потом отменяет участие, запись на новые встречи "
        "может быть временно ограничена.\n\n"
        "Отменить участие во встрече?"
    )


def format_stayed_registered() -> str:
    return (
        "Хорошо 🌿 Твоя запись на встречу сохранена.\n\n"
        "Если планы изменятся, пожалуйста, отмени участие заранее — "
        "так место сможет занять кто-то ещё."
    )


def build_reason_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            text=REASON_LABELS[CancellationReasonType.ILL],
            callback_data=f"leave_r:{CancellationReasonType.ILL}:{meeting_id}",
        )],
        [InlineKeyboardButton(
            text=REASON_LABELS[CancellationReasonType.FAMILY],
            callback_data=f"leave_r:{CancellationReasonType.FAMILY}:{meeting_id}",
        )],
        [InlineKeyboardButton(
            text=REASON_LABELS[CancellationReasonType.OTHER],
            callback_data=f"leave_r:{CancellationReasonType.OTHER}:{meeting_id}",
        )],
        [InlineKeyboardButton(
            text="Вернуться ко встрече",
            callback_data=f"details:{meeting_id}",
        )],
    ])


def build_other_reason_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="Отмена",
            callback_data=f"leave_other_abort:{meeting_id}",
        ),
    ]])


def build_final_confirm_keyboard(meeting_id: int, reason_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            text="Да, отменить участие",
            callback_data=f"leave_confirm:{reason_type}:{meeting_id}",
        )],
        [InlineKeyboardButton(
            text="Нет, оставить запись",
            callback_data=f"leave_abort:{meeting_id}",
        )],
    ])


def build_stayed_keyboard(meeting_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            text="Вернуться ко встрече",
            callback_data=f"details:{meeting_id}",
        ),
    ]])


def parse_leave_reason_callback(data: str) -> tuple[str, int] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "leave_r":
        return None
    reason_type, meeting_id_str = parts[1], parts[2]
    if reason_type not in CancellationReasonType.ALL:
        return None
    try:
        return reason_type, int(meeting_id_str)
    except ValueError:
        return None


def parse_leave_confirm_callback(data: str) -> tuple[str, int] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "leave_confirm":
        return None
    reason_type, meeting_id_str = parts[1], parts[2]
    if reason_type not in CancellationReasonType.ALL:
        return None
    try:
        return reason_type, int(meeting_id_str)
    except ValueError:
        return None


async def preflight_leave(
    db: Database,
    meeting_id: int,
    user_id: int,
) -> PreflightResult:
    meeting = await db.get_meeting(meeting_id)
    if meeting is None or meeting.canceled_at is not None:
        return PreflightResult(None, format_unavailable())
    if not await db.is_registered(meeting_id, user_id):
        return PreflightResult(None, "Ты не зарегистрирован(а) на эту встречу.")
    return PreflightResult(meeting)
