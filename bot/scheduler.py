"""Scheduling utilities for reminders and announcements using APScheduler."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from typing import Callable, Awaitable, Sequence

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dateutil import tz

from .storage import Database
from .models import Meeting
from .utils import ensure_utc

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


def _format_meeting_card(meeting: Meeting, participants: int, local_tz) -> str:
    """Format a single meeting as a rich text card (HTML)."""
    when_local = ensure_utc(meeting.start_at_utc).astimezone(local_tz)
    lines = [
        f"<b>{meeting.topic}</b>",
        meeting.description or "",
        f"📆 {when_local:%d %B at %H:%M}",
        f"👥 {participants} participant{'s' if participants != 1 else ''}",
    ]
    if meeting.location:
        lines.append(f"📍 {meeting.location}")
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


class BotScheduler:
    """Wrapper around AsyncIOScheduler to manage bot jobs."""

    def __init__(self, db: Database, timezone, send_channel_message: Callable[[int, str], Awaitable[None]]):
        self.db = db
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self._tz = timezone
        self._send_channel_message = send_channel_message
        self._announce_days: list[int] = [1, 15]
        self._channel_id: int | None = None

    def start(self) -> None:
        """Start the underlying scheduler if not already running."""
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        """Stop the scheduler without waiting for running jobs."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

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
            await self._send_channel_message(channel_id, _format_today_card(meeting, participants, self._tz))

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
            await self._send_channel_message(channel_id, f"{header}\n\nNo meetings scheduled.")
            return

        cards = [
            _format_meeting_card(m, await self.db.count_confirmed(m.id), self._tz)
            for m in meetings
        ]
        for message in _split_messages(header, cards):
            await self._send_channel_message(channel_id, message)

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
        await self._send_channel_message(
            self._channel_id,
            _format_new_meeting_card(meeting, participants, self._tz),
        )
