"""Scheduling utilities for reminders and announcements using APScheduler."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Callable, Awaitable, Sequence

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from dateutil import tz

from .storage import Database
from .models import Meeting
from .utils import ensure_utc

@dataclass
class ReminderCallbacks:
    """Callback definitions used by reminder jobs."""
    send_reminder: Callable[[int, str], Awaitable[None]]  # chat_id, text


class BotScheduler:
    """Wrapper around AsyncIOScheduler to manage bot jobs."""
    def __init__(self, db: Database, timezone, send_channel_message: Callable[[int, str], Awaitable[None]]):
        self.db = db
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self._tz = timezone
        self._send_channel_message = send_channel_message

    def start(self) -> None:
        """Start the underlying scheduler if not already running."""
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        """Stop the scheduler without waiting for running jobs."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def schedule_meeting_reminders(self, meeting: Meeting) -> None:
        """Schedule reminder jobs relative to the meeting start time."""
        # Schedule 3 days and 1 day before
        start_utc = ensure_utc(meeting.start_at_utc)
        for days_before in (3, 1):
            run_at = start_utc - timedelta(days=days_before)
            now = datetime.now(tz.UTC)
            if run_at > now:
                self.scheduler.add_job(
                    self._reminder_job,
                    trigger=DateTrigger(run_date=run_at),
                    args=[meeting.id],
                    id=f"meeting_{meeting.id}_reminder_{days_before}",
                    replace_existing=True,
                )

    async def _reminder_job(self, meeting_id: int) -> None:
        """Reminder job placeholder; extend to DM participants/host."""
        # In a real implementation, we would DM participants. Minimal viable: no-op placeholder.
        # You can extend this to actually fetch participants and send messages via the bot.
        return None

    def schedule_announcements(self, days: list[int], t: time, channel_id: int | None) -> None:
        """Schedule periodic digest announcements to the configured channel."""
        if not channel_id:
            return
        # Schedule on specified days at time t in local tz
        self.scheduler.add_job(
            self._announcement_job,
            trigger=CronTrigger(day="{}".format(",".join(str(d) for d in days)), hour=t.hour, minute=t.minute),
            args=[channel_id],
            id="announcements_job",
            replace_existing=True,
        )

    async def _announcement_job(self, channel_id: int) -> None:
        """Compose and send an upcoming meetings digest to a channel."""
        # Minimal digest message
        now_utc = datetime.now(tz.UTC)
        meetings: Sequence[Meeting] = await self.db.list_upcoming_meetings(now_utc)
        if not meetings:
            text = "No upcoming meetings."
        else:
            lines = ["Upcoming meetings:"]
            for m in meetings[:10]:
                when_local = ensure_utc(m.start_at_utc).astimezone(self._tz)
                lines.append(f"#{m.id} {m.topic} — {when_local:%Y-%m-%d %H:%M} @ {m.location or 'TBA'}")
            text = "\n".join(lines)
        await self._send_channel_message(channel_id, text)
