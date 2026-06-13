"""Scheduling utilities for reminders and announcements using APScheduler."""
from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from typing import Callable, Awaitable, Literal, Sequence, TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dateutil import tz
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from .links import build_meeting_deep_link, meeting_channel_cta_keyboard
from .storage import Database
from .models import Meeting, User
from .utils import ensure_utc

if TYPE_CHECKING:
    from .waitlist import WaitlistService

log = logging.getLogger(__name__)

# Days before the second (and further) announce day to start the coverage window.
# E.g. announce on 15th → cover meetings from 18th onward.
_NEXT_WINDOW_OFFSET_DAYS = 3

# Telegram message character limit
_TG_MAX_MESSAGE_LEN = 4096


def _announcement_window(announce_days: list[int], today: date) -> tuple[date, date]:
    """Return the (from_date, to_date) coverage window for today's announcement.

    First announce day of the month → whole month.
    Subsequent days → today + _NEXT_WINDOW_OFFSET_DAYS through end of month.
    """
    month_start = today.replace(day=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if today.day == min(announce_days):
        return month_start, month_end
    from_date = today + timedelta(days=_NEXT_WINDOW_OFFSET_DAYS)
    return from_date, month_end


def _covered_by_future_announcement(meeting_date: date, today: date, announce_days: list[int]) -> bool:
    """Return True if meeting_date will appear in a scheduled digest before it occurs.

    Looks ahead up to 2 months (current + next). An announce day on or after the meeting itself
    doesn't count — same-day is handled by the daily check job.
    """
    sorted_days = sorted(announce_days)
    year, month = today.year, today.month
    for _ in range(2):
        for d in sorted_days:
            try:
                announce_date = date(year, month, d)
            except ValueError:
                continue
            if announce_date <= today or announce_date >= meeting_date:
                continue
            from_date, to_date = _announcement_window(sorted_days, announce_date)
            if from_date <= meeting_date <= to_date:
                return True
        month += 1
        if month > 12:
            month, year = 1, year + 1
        if date(year, month, 1) > meeting_date:
            break
    return False


def _format_meeting_card(
    meeting: Meeting,
    confirmed: int,
    hosts: int,
    host: User | None,
    local_tz,
    *,
    deep_link: str | None = None,
) -> str:
    """Format a single meeting as a rich text card (HTML)."""
    when_local = ensure_utc(meeting.start_at_utc).astimezone(local_tz)
    lines = [
        f"<b>{when_local:%d.%m (%A) %H:%M}</b>",
        f"<b>{meeting.topic}</b>",
        f"Ведет {_display_name(host)}." if host else "",
        f"🌻 Registered: {confirmed} / {meeting.max_participants} participants (+ведущих: {hosts})",
    ]
    if meeting.location:
        lines.append(f"📍 {meeting.location}")
    if deep_link:
        lines.append(f'👉 <a href="{deep_link}">Записаться / Подробности</a>')
    return "\n".join(line for line in lines if line)


def _format_today_card(meeting: Meeting, participants: int, local_tz) -> str:
    """Format a meeting card for the daily 'today' announcement."""
    when_local = ensure_utc(meeting.start_at_utc).astimezone(local_tz)
    lines = [
        f"🔔 <b>Meeting today — {when_local:%H:%M}</b>",
        f"<b>{meeting.topic}</b>",
        meeting.description or "",
        f"👥 {participants} participant{'s' if participants != 1 else ''}",
    ]
    if meeting.location:
        lines.append(f"📍 {meeting.location}")
    return "\n".join(line for line in lines if line)


def _format_new_meeting_card(meeting: Meeting, participants: int, local_tz) -> str:
    """Format an immediate announcement for a newly created meeting (HTML)."""
    when_local = ensure_utc(meeting.start_at_utc).astimezone(local_tz)
    lines = [
        "🆕 <b>New meeting added!</b>",
        f"<b>{meeting.topic}</b>",
        meeting.description or "",
        f"📆 {when_local:%d %B at %H:%M}",
        f"👥 {participants} participant{'s' if participants != 1 else ''}",
    ]
    if meeting.location:
        lines.append(f"📍 {meeting.location}")
    return "\n".join(line for line in lines if line)


def _split_messages(header: str, cards: list[str], max_len: int = _TG_MAX_MESSAGE_LEN) -> list[str]:
    """Pack meeting cards into messages under max_len, header prepended to the first."""
    messages: list[str] = []
    current = header
    for card in cards:
        chunk = f"\n\n{card}"
        if len(current) + len(chunk) > max_len:
            messages.append(current)
            current = card
        else:
            current += chunk
    if current:
        messages.append(current)
    return messages


@dataclass
class _ParticipantEvent:
    user_name: str
    event: Literal["joined", "left"]
    at: datetime = field(default_factory=datetime.utcnow)


def _display_name(user: User) -> str:
    """Format a display name, appending @username when available."""
    if user.username:
        return f"{user.name} (@{user.username})" if user.name else f"@{user.username}"
    return user.name or f"User#{user.id}"


class BotScheduler:
    """Wrapper around AsyncIOScheduler to manage bot jobs."""

    def __init__(
        self,
        db: Database,
        timezone,
        send_channel_card: Callable[..., Awaitable[None]],
        *,
        bot: Bot | None = None,
        notify_threshold: int = 10,
        bot_username: str | None = None,
        waitlist_service: "WaitlistService | None" = None,
    ):
        self.db = db
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self._tz = timezone
        self._send_channel_card = send_channel_card
        self._bot = bot
        self._notify_threshold = notify_threshold
        self._bot_username = bot_username
        self._waitlist_service = waitlist_service
        self._announce_days: list[int] = [1, 15]
        self._channel_id: int | None = None
        self._pending_events: dict[int, list[_ParticipantEvent]] = defaultdict(list)

    def start(self) -> None:
        """Start the underlying scheduler if not already running."""
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        """Stop the scheduler without waiting for running jobs."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _meeting_deep_link(self, meeting: Meeting) -> str | None:
        """Build a meeting deep link for channel announcements, if configured."""
        if not self._bot_username or not meeting.public_token:
            if self._channel_id:
                log.warning(
                    "Skipping meeting deep-link for meeting %s: bot username or public_token missing",
                    meeting.id,
                )
            return None
        try:
            return build_meeting_deep_link(self._bot_username, meeting.public_token)
        except ValueError:
            log.warning("Skipping meeting deep-link for meeting %s: invalid public_token", meeting.id)
            return None

    def _meeting_cta_keyboard(self, meeting: Meeting) -> InlineKeyboardMarkup | None:
        """Build a channel CTA button for a single-meeting post."""
        deep_link = self._meeting_deep_link(meeting)
        if not deep_link:
            return None
        return meeting_channel_cta_keyboard(deep_link)

    def schedule_daily_reminders(self, t: time, channel_id: int | None) -> None:
        """Schedule a daily job to announce meetings happening today."""
        if not channel_id:
            return
        self.scheduler.add_job(
            self._daily_meeting_job,
            trigger=CronTrigger(hour=t.hour, minute=t.minute),
            args=[channel_id],
            id="daily_meeting_job",
            replace_existing=True,
        )

    async def _daily_meeting_job(self, channel_id: int) -> None:
        """Send a separate announcement for each meeting scheduled today."""
        today_local = datetime.now(self._tz).date()
        from_utc = datetime.combine(today_local, time.min).replace(tzinfo=self._tz).astimezone(tz.UTC)
        to_utc = datetime.combine(today_local, time.max).replace(tzinfo=self._tz).astimezone(tz.UTC)

        meetings: Sequence[Meeting] = await self.db.list_meetings_in_range(from_utc, to_utc)
        for meeting in meetings:
            participants = await self.db.count_confirmed(meeting.id)
            await self._send_channel_card(
                channel_id,
                _format_today_card(meeting, participants, self._tz),
                meeting.photo_file_id,
                reply_markup=self._meeting_cta_keyboard(meeting),
            )

    def schedule_announcements(self, days: list[int], t: time, channel_id: int | None) -> None:
        """Schedule periodic digest announcements to the configured channel."""
        if not channel_id:
            return
        self._announce_days = days
        self._channel_id = channel_id
        self.scheduler.add_job(
            self._announcement_job,
            trigger=CronTrigger(day=",".join(str(d) for d in days), hour=t.hour, minute=t.minute),
            args=[channel_id],
            id="announcements_job",
            replace_existing=True,
        )

    async def run_announcement_now(self) -> None:
        """Trigger the announcement job immediately (for manual/admin use)."""
        if not self._channel_id:
            return
        await self._announcement_job(self._channel_id)

    async def _announcement_job(self, channel_id: int) -> None:
        """Compose and send a monthly meetings digest to the announcements channel."""
        today_local = datetime.now(self._tz).date()
        from_date, to_date = _announcement_window(self._announce_days, today_local)

        from_utc = datetime.combine(from_date, time.min).replace(tzinfo=self._tz).astimezone(tz.UTC)
        to_utc = datetime.combine(to_date, time.max).replace(tzinfo=self._tz).astimezone(tz.UTC)

        meetings: Sequence[Meeting] = await self.db.list_meetings_in_range(from_utc, to_utc)

        month_name = from_date.strftime("%B %Y")
        header = f"📅 <b>Meetings — {month_name}</b>"

        if not meetings:
            await self._send_channel_card(channel_id, f"{header}\n\nNo meetings scheduled.", None)
            return

        cards = []
        for m in meetings:
            confirmed = await self.db.count_confirmed(m.id)
            hosts = await self.db.count_hosts(m.id)
            host = await self.db.get_user(m.created_by)
            cards.append(
                _format_meeting_card(
                    m, confirmed, hosts, host, self._tz, deep_link=self._meeting_deep_link(m)
                )
            )

        for message in _split_messages(header, cards):
            await self._send_channel_card(channel_id, message, None)

    def schedule_host_notifications(self, interval_minutes: int) -> None:
        """Register a periodic job to flush batched participant-change DMs to hosts."""
        if not self._bot:
            return
        self.scheduler.add_job(
            self._flush_pending_notifications,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="host_notifications_flush",
            replace_existing=True,
        )

    def schedule_waitlist_expiration(self, interval_minutes: int) -> None:
        """Register a periodic job to expire stale waitlist offers."""
        if not self._bot or not self._waitlist_service:
            return
        self.scheduler.add_job(
            self._waitlist_expiration_job,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="waitlist_expiration_job",
            replace_existing=True,
        )

    async def _waitlist_expiration_job(self) -> None:
        from .waitlist import send_expired_notices, send_offer_dms

        if not self._bot or not self._waitlist_service:
            return
        now_utc = datetime.now(dt_timezone.utc)
        expired, offers = await self._waitlist_service.expire_stale_offers(now_utc)
        await send_expired_notices(self._bot, self._waitlist_service, expired)
        await send_offer_dms(self._bot, self._waitlist_service, offers)

    async def on_participant_change(
        self,
        meeting: Meeting,
        user: User,
        event: Literal["joined", "left"],
        confirmed_count: int,
    ) -> None:
        """Route a signup or cancellation to instant DM or the batch queue."""
        if not self._bot:
            return
        if confirmed_count < self._notify_threshold:
            await self._send_instant_notification(meeting, user, event, confirmed_count)
        else:
            self._pending_events[meeting.id].append(
                _ParticipantEvent(user_name=_display_name(user), event=event)
            )

    async def _send_instant_notification(
        self,
        meeting: Meeting,
        user: User,
        event: Literal["joined", "left"],
        confirmed_count: int,
    ) -> None:
        icon = "➕" if event == "joined" else "➖"
        action = "записалась на" if event == "joined" else "отменила участие в"
        text = (
            f"{icon} <b>{_display_name(user)}</b> {action} «{meeting.topic}»\n"
            f"Участников: {confirmed_count} / {meeting.max_participants}"
        )
        await self._send_host_dm(meeting.created_by, text)

    async def _flush_pending_notifications(self) -> None:
        """Send batched digest DMs for all meetings with queued events, then clear the queue."""
        if not self._pending_events:
            return
        # Snapshot and clear atomically before any awaits to avoid double-sending on slow runs
        pending = dict(self._pending_events)
        self._pending_events.clear()

        for meeting_id, events in pending.items():
            if not events:
                continue
            meeting = await self.db.get_meeting(meeting_id)
            if not meeting or meeting.canceled_at:
                continue

            joined = sum(1 for e in events if e.event == "joined")
            left = sum(1 for e in events if e.event == "left")
            confirmed = await self.db.count_confirmed(meeting_id)

            parts = []
            if joined:
                parts.append(f"+{joined} записались")
            if left:
                parts.append(f"−{left} отменили")

            text = (
                f"📊 <b>Обновление участников — «{meeting.topic}»</b>\n"
                f"{', '.join(parts)}\n"
                f"Сейчас: {confirmed} / {meeting.max_participants}"
            )
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Посмотреть список", callback_data=f"participants:{meeting_id}")
            ]])
            await self._send_host_dm(meeting.created_by, text, reply_markup=keyboard)

    async def _send_host_dm(self, host_id: int, text: str, *, reply_markup=None) -> None:
        """Send a DM to the meeting host, logging and suppressing send errors."""
        try:
            await self._bot.send_message(
                chat_id=host_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception:
            log.warning("Failed to send DM to host %d", host_id)

    async def maybe_announce_new_meeting(self, meeting: Meeting) -> None:
        """Send an immediate channel announcement if the meeting won't appear in any future digest.

        No-op if no channel is configured or the meeting will be covered by a scheduled announcement.
        """
        if not self._channel_id:
            return
        meeting_date = ensure_utc(meeting.start_at_utc).astimezone(self._tz).date()
        today = datetime.now(self._tz).date()
        if _covered_by_future_announcement(meeting_date, today, self._announce_days):
            return
        participants = await self.db.count_confirmed(meeting.id)
        await self._send_channel_card(
            self._channel_id,
            _format_new_meeting_card(meeting, participants, self._tz),
            meeting.photo_file_id,
            reply_markup=self._meeting_cta_keyboard(meeting),
        )
