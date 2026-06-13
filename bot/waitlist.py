"""Waitlist domain service for meeting spot offers and queue management."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from telegram import Bot, InlineKeyboardMarkup

from .models import Meeting, WaitlistEntry, WaitlistStatus, Registration, RegistrationStatus
from .storage import Database, is_registration_open
from .utils import ensure_utc

log = logging.getLogger(__name__)

WaitlistState = Literal["waiting", "offered"] | None

_STATUS_LABELS = {
    WaitlistStatus.WAITING: "в очереди",
    WaitlistStatus.OFFERED: "предложено место",
    WaitlistStatus.ACCEPTED: "принято",
    WaitlistStatus.DECLINED: "отказ",
    WaitlistStatus.CANCELLED: "отменено",
    WaitlistStatus.EXPIRED: "истекло",
}


@dataclass
class WaitlistResult:
    ok: bool
    message: str
    entry: WaitlistEntry | None = None
    position: int | None = None
    notifications: list[OfferNotification] = field(default_factory=list)


@dataclass
class OfferNotification:
    entry: WaitlistEntry
    meeting: Meeting
    user_id: int


@dataclass
class ExpiredOfferNotice:
    user_id: int
    meeting: Meeting


class WaitlistService:
    """Orchestrates waitlist join, cancel, offer, accept, decline, and expiry."""

    def __init__(self, db: Database, *, offer_ttl: timedelta, local_tz):
        self.db = db
        self.offer_ttl = offer_ttl
        self.local_tz = local_tz

    async def get_user_waitlist_state(self, meeting_id: int, user_id: int) -> WaitlistState:
        entry = await self.db.get_active_waitlist_entry(meeting_id, user_id)
        if entry is None:
            return None
        if entry.status == WaitlistStatus.WAITING:
            return "waiting"
        if entry.status == WaitlistStatus.OFFERED:
            return "offered"
        return None

    async def join_waitlist(self, meeting_id: int, user_id: int, now_utc: datetime) -> WaitlistResult:
        meeting = await self.db.get_meeting(meeting_id)
        if meeting is None or meeting.canceled_at is not None:
            return WaitlistResult(False, "Встреча недоступна для waitlist.")
        if not is_registration_open(meeting, now_utc):
            return WaitlistResult(False, "Регистрация на эту встречу ещё не открыта.")
        if not await self.db.is_meeting_open(meeting_id, now_utc):
            return WaitlistResult(False, "Встреча недоступна для waitlist.")
        if await self.db.is_registered(meeting_id, user_id):
            return WaitlistResult(False, "Вы уже записаны на эту встречу.")
        if await self.db.get_active_waitlist_entry(meeting_id, user_id):
            return WaitlistResult(False, "Вы уже в waitlist на эту встречу.")
        available = await self.db.available_spots(meeting_id)
        if available > 0:
            return WaitlistResult(False, "На встрече есть свободные места — запишитесь напрямую.")
        entry = await self.db.create_waitlist_entry(meeting_id, user_id)
        position = await self.db.get_queue_position(entry.id)
        log.info("waitlist join meeting_id=%s entry_id=%s position=%s", meeting_id, entry.id, position)
        return WaitlistResult(True, "Вы добавлены в waitlist.", entry=entry, position=position)

    async def cancel_waitlist(self, meeting_id: int, user_id: int) -> WaitlistResult:
        async with self.db.session() as s:
            res = await s.execute(
                select(WaitlistEntry).where(
                    WaitlistEntry.meeting_id == meeting_id,
                    WaitlistEntry.user_id == user_id,
                    WaitlistEntry.status == WaitlistStatus.WAITING,
                )
            )
            entry = res.scalar_one_or_none()
            if entry is None:
                return WaitlistResult(False, "Активная запись в waitlist не найдена.")
            entry.status = WaitlistStatus.CANCELLED
            await s.commit()
            log.info("waitlist cancel meeting_id=%s entry_id=%s", meeting_id, entry.id)
            return WaitlistResult(True, "Вы удалены из waitlist.", entry=entry)

    async def process_available_spots(self, meeting_id: int, now_utc: datetime) -> list[OfferNotification]:
        """Offer open spots to the next users in queue. Returns entries needing DM."""
        notifications: list[OfferNotification] = []
        while True:
            offered = await self._offer_next_spot(meeting_id, now_utc)
            if offered is None:
                break
            notifications.append(offered)
        return notifications

    async def _offer_next_spot(self, meeting_id: int, now_utc: datetime) -> OfferNotification | None:
        async with self.db.session() as s:
            m = await s.get(Meeting, meeting_id)
            if m is None or m.canceled_at is not None:
                return None
            confirmed = await self.db._count_confirmed_in_session(s, meeting_id)
            reserved = await self.db._count_offered_in_session(s, meeting_id)
            if confirmed + reserved >= m.max_participants:
                return None
            res = await s.execute(
                select(WaitlistEntry)
                .where(
                    WaitlistEntry.meeting_id == meeting_id,
                    WaitlistEntry.status == WaitlistStatus.WAITING,
                )
                .order_by(WaitlistEntry.created_at.asc())
                .limit(1)
            )
            entry = res.scalar_one_or_none()
            if entry is None:
                return None
            expires = now_utc + self.offer_ttl
            entry.status = WaitlistStatus.OFFERED
            entry.offered_at = now_utc
            entry.offer_expires_at = expires
            await s.commit()
            await s.refresh(entry)
            log.info(
                "waitlist offer meeting_id=%s entry_id=%s expires_at=%s",
                meeting_id, entry.id, expires.isoformat(),
            )
            return OfferNotification(entry=entry, meeting=m, user_id=entry.user_id)

    async def accept_offer(self, entry_id: int, user_id: int, now_utc: datetime) -> WaitlistResult:
        now_utc = ensure_utc(now_utc)
        async with self.db.session() as s:
            entry = await s.get(WaitlistEntry, entry_id)
            if entry is None:
                return WaitlistResult(False, "Предложение не найдено.")
            if entry.user_id != user_id:
                return WaitlistResult(False, "Это предложение не для вас.")
            if entry.status != WaitlistStatus.OFFERED:
                return WaitlistResult(False, "Предложение больше недействительно.")
            if entry.offer_expires_at and ensure_utc(entry.offer_expires_at) < now_utc:
                entry.status = WaitlistStatus.EXPIRED
                await s.commit()
                return WaitlistResult(False, "Срок предложения истёк.")
            m = await s.get(Meeting, entry.meeting_id)
            if m is None or m.canceled_at is not None:
                return WaitlistResult(False, "Встреча недоступна.")
            start = ensure_utc(m.start_at_utc)
            if start < now_utc:
                return WaitlistResult(False, "Встреча уже прошла.")
            confirmed = await self.db._count_confirmed_in_session(s, entry.meeting_id)
            if confirmed >= m.max_participants:
                return WaitlistResult(False, "Место больше недоступно.")
            res = await s.execute(
                select(Registration).where(
                    Registration.meeting_id == entry.meeting_id,
                    Registration.user_id == user_id,
                    Registration.status == RegistrationStatus.CONFIRMED,
                )
            )
            if res.scalar_one_or_none():
                entry.status = WaitlistStatus.ACCEPTED
                await s.commit()
                return WaitlistResult(False, "Вы уже записаны на эту встречу.")
            reg = Registration(
                meeting_id=entry.meeting_id,
                user_id=user_id,
                status=RegistrationStatus.CONFIRMED,
            )
            s.add(reg)
            entry.status = WaitlistStatus.ACCEPTED
            await s.commit()
            log.info("waitlist accept meeting_id=%s entry_id=%s", entry.meeting_id, entry_id)
            return WaitlistResult(True, "Вы записаны на встречу!", entry=entry)

    async def decline_offer(self, entry_id: int, user_id: int, now_utc: datetime) -> WaitlistResult:
        async with self.db.session() as s:
            entry = await s.get(WaitlistEntry, entry_id)
            if entry is None:
                return WaitlistResult(False, "Предложение не найдено.")
            if entry.user_id != user_id:
                return WaitlistResult(False, "Это предложение не для вас.")
            if entry.status != WaitlistStatus.OFFERED:
                return WaitlistResult(False, "Предложение больше недействительно.")
            meeting_id = entry.meeting_id
            entry.status = WaitlistStatus.DECLINED
            await s.commit()
            log.info("waitlist decline meeting_id=%s entry_id=%s", meeting_id, entry_id)
        notifications = await self.process_available_spots(meeting_id, now_utc)
        msg = "Вы отказались от места."
        if notifications:
            msg += " Место предложено следующему в очереди."
        return WaitlistResult(True, msg, notifications=notifications)

    async def expire_stale_offers(
        self, now_utc: datetime
    ) -> tuple[list[ExpiredOfferNotice], list[OfferNotification]]:
        """Expire old offers. Returns (expiry notices, new offer notifications)."""
        now_utc = ensure_utc(now_utc)
        expired_entries = await self.db.get_expired_offers(now_utc)
        expired_notices: list[ExpiredOfferNotice] = []
        all_notifications: list[OfferNotification] = []
        for entry in expired_entries:
            async with self.db.session() as s:
                locked = await s.get(WaitlistEntry, entry.id)
                if locked is None or locked.status != WaitlistStatus.OFFERED:
                    continue
                if locked.offer_expires_at and ensure_utc(locked.offer_expires_at) >= now_utc:
                    continue
                meeting_id = locked.meeting_id
                m = await s.get(Meeting, meeting_id)
                locked.status = WaitlistStatus.EXPIRED
                await s.commit()
                if m:
                    expired_notices.append(ExpiredOfferNotice(user_id=locked.user_id, meeting=m))
                log.info("waitlist expired meeting_id=%s entry_id=%s", meeting_id, entry.id)
            notifications = await self.process_available_spots(meeting_id, now_utc)
            all_notifications.extend(notifications)
        return expired_notices, all_notifications

    def format_join_confirmation(self, meeting: Meeting, position: int | None) -> str:
        pos_line = f"\nВаше место в очереди: {position}." if position else ""
        return (
            f'Вы в waitlist на встречу "{meeting.topic}" ✨\n\n'
            f"Если место освободится, бот напишет вам первым делом."
            f"{pos_line}"
        )

    def format_offer_dm(self, meeting: Meeting, entry: WaitlistEntry) -> str:
        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        expires_local = None
        if entry.offer_expires_at:
            expires_local = ensure_utc(entry.offer_expires_at).astimezone(self.local_tz)
        lines = [
            f'🎟 <b>Вам предложено место на встрече «{meeting.topic}»</b>',
            "",
            f"📅 {when_local:%d.%m.%Y %H:%M}",
            f"📍 {meeting.location or 'TBA'}",
        ]
        if expires_local:
            lines.append(f"⏰ Ответьте до {expires_local:%d.%m.%Y %H:%M}")
        return "\n".join(lines)

    def format_expired_notice(self, meeting: Meeting) -> str:
        return (
            f'⏰ Срок предложения места на встречу «{meeting.topic}» истёк.\n'
            "Место передано следующему в очереди."
        )

    def format_host_waitlist(self, meeting: Meeting, rows) -> str:
        if not rows:
            return (
                f'<b>Waitlist — «{meeting.topic}»</b>\n\n'
                "Waitlist пуст."
            )
        lines = [f'<b>Waitlist — «{meeting.topic}»</b>\n']
        for i, row in enumerate(rows, start=1):
            entry, user = row
            name = user.name or ""
            username_part = f" (@{user.username})" if user.username else ""
            status_label = _STATUS_LABELS.get(entry.status, entry.status)
            lines.append(f"{i}. {name}{username_part} — {status_label}")
        lines.append(f"\nВсего: {len(rows)}")
        return "\n".join(lines)

    def format_details_waitlist_line(
        self, entry: WaitlistEntry, position: int | None
    ) -> str:
        if entry.status == WaitlistStatus.WAITING and position:
            return f"⏳ Вы в waitlist (место в очереди: {position})"
        if entry.status == WaitlistStatus.OFFERED and entry.offer_expires_at:
            expires_local = ensure_utc(entry.offer_expires_at).astimezone(self.local_tz)
            return f"✉️ Вам предложено место (до {expires_local:%H:%M})"
        if entry.status == WaitlistStatus.WAITING:
            return "⏳ Вы в waitlist"
        return ""


def build_offer_keyboard(entry_id: int, meeting_id: int) -> "InlineKeyboardMarkup":
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text="Принять место", callback_data=f"offer_accept:{entry_id}"),
            InlineKeyboardButton(text="Отказаться", callback_data=f"offer_decline:{entry_id}"),
        ],
        [
            InlineKeyboardButton(text="Подробности", callback_data=f"details:{meeting_id}"),
        ],
    ])


async def send_offer_dms(
    bot: "Bot",
    waitlist: WaitlistService,
    notifications: list[OfferNotification],
) -> None:
    for note in notifications:
        text = waitlist.format_offer_dm(note.meeting, note.entry)
        keyboard = build_offer_keyboard(note.entry.id, note.meeting.id)
        try:
            await bot.send_message(
                chat_id=note.user_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            log.exception("Failed to send waitlist offer DM entry_id=%s", note.entry.id)


async def send_expired_notices(
    bot: "Bot",
    waitlist: WaitlistService,
    notices: list[ExpiredOfferNotice],
) -> None:
    for notice in notices:
        text = waitlist.format_expired_notice(notice.meeting)
        try:
            await bot.send_message(chat_id=notice.user_id, text=text, parse_mode="HTML")
        except Exception:
            log.exception("Failed to send waitlist expiry notice meeting_id=%s", notice.meeting.id)
