"""Scheduling utilities for reminders and announcements using APScheduler."""
from __future__ import annotations

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

from .announce_schedule import (
    _announcement_window,
    _covered_by_future_announcement,
    compute_urgent_announce_at,
)
from .links import build_meeting_deep_link, build_telegram_user_link, meeting_channel_cta_keyboard
from .log_context import log_event, user_log_fields
from .storage import Database
from .meeting_format import format_meeting_time, format_month_year_russian
from .models import Meeting, User
from .utils import ensure_utc, utc_now

if TYPE_CHECKING:
    from .waitlist import WaitlistService

log = logging.getLogger(__name__)

# Telegram message character limit
_TG_MAX_MESSAGE_LEN = 4096


def _format_spots_line(available: int, *, emoji: str = "🌻") -> str:
    """Format the free-spots line for a digest card."""
    if available <= 0:
        return f"{emoji} Мест нет"
    n = available
    if n % 10 == 1 and n % 100 != 11:
        word = "место"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "места"
    else:
        word = "мест"
    return f"{emoji} Осталось {n} {word}"


def _host_display_name(user: User) -> str:
    if user.name:
        return user.name
    if user.username:
        return f"@{user.username}"
    return f"User#{user.id}"


def _format_host_line(host: User) -> str:
    name = _host_display_name(host)
    link = build_telegram_user_link(host.id, host.username)
    return f'Ведет <a href="{link}">{name}</a>.'


def _format_organizer_line(host: User) -> str:
    name = _host_display_name(host)
    link = build_telegram_user_link(host.id, host.username)
    return f'🤍 Организует <a href="{link}">{name}</a>'


def _registration_link_line(available: int, deep_link: str) -> str:
    label = "Регистрация" if available > 0 else "Встать в waitlist"
    return f'✏️<a href="{deep_link}">{label}</a>'


def _format_meeting_card(
    meeting: Meeting,
    available: int,
    host: User | None,
    local_tz,
    *,
    deep_link: str | None = None,
) -> str:
    """Format a single meeting as a rich text card (HTML)."""
    when = format_meeting_time(meeting, local_tz, style="card")
    lines = [
        f"<b>{when}</b>",
        meeting.topic,
    ]
    if host:
        lines.append(_format_host_line(host))
    lines.append(_format_spots_line(available))
    if meeting.location:
        lines.append(f"📍 {meeting.location}")
    if deep_link:
        lines.append(_registration_link_line(available, deep_link))
    return "\n".join(line for line in lines if line)


def _format_today_card(
    meeting: Meeting,
    available: int,
    host: User | None,
    local_tz,
    *,
    deep_link: str | None = None,
) -> str:
    """Format a meeting card for the daily 'today' announcement."""
    when = format_meeting_time(meeting, local_tz, style="today")
    lines = [
        f"🔔 <b>Встреча сегодня — {when}</b>",
        f"<b>{meeting.topic}</b>",
    ]
    if meeting.description:
        lines.append(meeting.description)
    if host:
        lines.append(f"<i>{_format_organizer_line(host)}</i>")
    lines.append(f"<i>{_format_spots_line(available, emoji='🌼')}</i>")
    if meeting.location:
        lines.append(f"📍 {meeting.location}")
    if deep_link:
        lines.append(_registration_link_line(available, deep_link))
    return "\n".join(line for line in lines if line)


def _format_new_meeting_card(
    meeting: Meeting,
    available: int,
    host: User | None,
    local_tz,
    *,
    deep_link: str | None = None,
) -> str:
    """Format an immediate announcement for a newly created meeting (HTML)."""
    when = format_meeting_time(meeting, local_tz, style="announce")
    parts = [f"<i>{when}</i>", "", f"<b>{meeting.topic}</b>"]
    if meeting.description:
        parts.extend(["", meeting.description])
    parts.append("")
    if host:
        parts.append(f"<i>{_format_organizer_line(host)}</i>")
    parts.append(f"<i>{_format_spots_line(available, emoji='🌼')}</i>")
    if meeting.location:
        parts.append(f"<i>📍 {meeting.location}</i>")
    if deep_link:
        parts.append(f"<i>{_registration_link_line(available, deep_link)}</i>")
    return "\n".join(parts)


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
    at: datetime = field(default_factory=utc_now)


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
                log_event(
                    log,
                    logging.WARNING,
                    "scheduler_skip_deep_link",
                    meeting_id=meeting.id,
                    reason="bot_username_or_token_missing",
                )
            return None
        try:
            return build_meeting_deep_link(self._bot_username, meeting.public_token)
        except ValueError:
            log_event(
                log,
                logging.WARNING,
                "scheduler_skip_deep_link",
                meeting_id=meeting.id,
                reason="invalid_public_token",
            )
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

    def schedule_participant_reminders(self, t: time) -> None:
        """Schedule a daily job to send 7/3/1-day reminder DMs to participants."""
        if not self._bot:
            return
        self.scheduler.add_job(
            self._participant_reminders_job,
            trigger=CronTrigger(hour=t.hour, minute=t.minute),
            id="participant_reminders_job",
            replace_existing=True,
        )

    async def _participant_reminders_job(self) -> None:
        from .meeting_reminders import process_participant_reminders

        if not self._bot:
            return
        await process_participant_reminders(
            self._bot,
            self.db,
            self._tz,
            self._bot_username,
        )

    async def _daily_meeting_job(self, channel_id: int) -> None:
        """Send a separate announcement for each meeting scheduled today."""
        today_local = datetime.now(self._tz).date()
        from_utc = datetime.combine(today_local, time.min).replace(tzinfo=self._tz).astimezone(tz.UTC)
        to_utc = datetime.combine(today_local, time.max).replace(tzinfo=self._tz).astimezone(tz.UTC)

        meetings: Sequence[Meeting] = await self.db.list_meetings_in_range(from_utc, to_utc)
        log_event(
            log,
            logging.INFO,
            "scheduler_daily_meetings",
            channel_id=channel_id,
            meeting_count=len(meetings),
        )
        for meeting in meetings:
            available = await self.db.available_spots(meeting.id)
            host = await self.db.get_user(meeting.created_by)
            await self._send_channel_card(
                channel_id,
                _format_today_card(
                    meeting,
                    available,
                    host,
                    self._tz,
                    deep_link=self._meeting_deep_link(meeting),
                ),
                meeting.photo_file_id,
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
        log_event(
            log,
            logging.INFO,
            "scheduler_announcement",
            channel_id=channel_id,
            meeting_count=len(meetings),
        )

        month_name = format_month_year_russian(
            datetime.combine(from_date, time.min).replace(tzinfo=self._tz)
        )
        header = f"📅 <b>Встречи — {month_name}</b>"

        if not meetings:
            await self._send_channel_card(channel_id, f"{header}\n\nНет запланированных встреч.", None)
            return

        cards = []
        for m in meetings:
            available = await self.db.available_spots(m.id)
            host = await self.db.get_user(m.created_by)
            cards.append(
                _format_meeting_card(
                    m, available, host, self._tz, deep_link=self._meeting_deep_link(m)
                )
            )

        for message in _split_messages(header, cards):
            await self._send_channel_card(channel_id, message, None)

    def schedule_urgent_announcements(self, t: time, channel_id: int | None) -> None:
        """Schedule a daily job to publish pending urgent meeting announcements."""
        if not channel_id:
            return
        self._channel_id = channel_id
        self.scheduler.add_job(
            self._urgent_announce_job,
            trigger=CronTrigger(hour=t.hour, minute=t.minute),
            id="urgent_announce_job",
            replace_existing=True,
        )

    async def _urgent_announce_job(self) -> None:
        """Publish all meetings whose urgent announcement is due."""
        if not self._channel_id:
            return
        now_utc = utc_now()
        meetings = await self.db.list_meetings_pending_urgent_announce(now_utc)
        log_event(
            log,
            logging.INFO,
            "scheduler_urgent_announce",
            channel_id=self._channel_id,
            meeting_count=len(meetings),
        )
        for meeting in meetings:
            await self._publish_urgent_announcement(meeting)
            await self.db.mark_urgent_announce_posted(meeting.id, now_utc)

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
        log_event(
            log,
            logging.INFO,
            "scheduler_waitlist_expiration",
            expired_count=len(expired),
            new_offer_count=len(offers),
        )
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
        log_event(
            log,
            logging.INFO,
            "scheduler_participant_change",
            meeting_id=meeting.id,
            event=event,
            confirmed_count=confirmed_count,
            **user_log_fields(user_id=user.id, username=user.username, name=user.name),
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
            host = await self.db.get_user(host_id)
            log_event(
                log,
                logging.WARNING,
                "scheduler_host_dm_failed",
                **user_log_fields(user_id=host_id, username=host.username if host else None),
            )

    async def plan_urgent_announcement(self, meeting: Meeting) -> None:
        """Compute, persist, and optionally publish an urgent channel announcement."""
        if not self._channel_id:
            return
        if meeting.urgent_announce_posted_at_utc is not None:
            return
        today = datetime.now(self._tz).date()
        at = compute_urgent_announce_at(
            meeting_start_at_utc=meeting.start_at_utc,
            registration_starts_at_utc=meeting.registration_starts_at_utc,
            reference_date=today,
            announce_days=self._announce_days,
            created_at_utc=ensure_utc(meeting.created_at),
            local_tz=self._tz,
        )
        await self.db.update_meeting_urgent_announce_schedule(meeting.id, at)
        if at is not None and at <= utc_now():
            await self._publish_urgent_announcement(meeting)
            await self.db.mark_urgent_announce_posted(meeting.id, utc_now())

    async def maybe_announce_new_meeting(self, meeting: Meeting) -> None:
        """Deprecated alias for plan_urgent_announcement."""
        await self.plan_urgent_announcement(meeting)

    async def _publish_urgent_announcement(self, meeting: Meeting) -> None:
        """Send an immediate channel announcement for a single meeting."""
        if not self._channel_id:
            return
        available = await self.db.available_spots(meeting.id)
        host = await self.db.get_user(meeting.created_by)
        await self._send_channel_card(
            self._channel_id,
            _format_new_meeting_card(
                meeting,
                available,
                host,
                self._tz,
                deep_link=self._meeting_deep_link(meeting),
            ),
            meeting.photo_file_id,
        )
