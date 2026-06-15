"""Async database access layer using SQLAlchemy 2.0 ORM.

Exposes a thin CRUD wrapper around the models for use by handlers/scheduler.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select, func
from sqlalchemy.engine import Row

from .links import generate_meeting_public_token
from .messages import REGISTRATION_SUCCESS
from .models import Base, User, Meeting, Registration, RegistrationStatus, WaitlistEntry, WaitlistStatus
from .utils import ensure_utc

log = logging.getLogger(__name__)


def is_registration_open(meeting: Meeting, now_utc: datetime) -> bool:
    """Return True when non-host users may register for the meeting."""
    if meeting.registration_starts_at_utc is None:
        return True
    return ensure_utc(meeting.registration_starts_at_utc) <= ensure_utc(now_utc)


def _ensure_db_dir(url: str) -> None:
    """Create the parent directory for a SQLite URL if it doesn't exist."""
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            path_part = url[len(prefix):]
            # 4-slash absolute path starts with "/" after stripping prefix
            db_path = Path(path_part if path_part.startswith("/") else path_part)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            log.info("Database path: %s", db_path)
            return


class Database:
    """Simple async database facade for sessions and CRUD helpers."""
    def __init__(self, url: str):
        _ensure_db_dir(url)
        self.engine = create_async_engine(url, future=True, echo=False)
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.engine, expire_on_commit=False
        )

    async def create_all(self) -> None:
        """Create all tables if they don't exist yet."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Provide an async SQLAlchemy session context manager."""
        async with self._sessionmaker() as s:
            yield s

    # Users
    async def get_or_create_user(self, user_id: int, name: str, username: str | None) -> User:
        """Fetch a user by id or create it if missing."""
        async with self.session() as s:
            u = await s.get(User, user_id)
            if u is None:
                u = User(id=user_id, name=name, username=username)
                s.add(u)
                await s.commit()
            return u

    async def _generate_unique_public_token(self, session: AsyncSession) -> str:
        """Generate a meeting public_token that is not already in use."""
        for _ in range(10):
            token = generate_meeting_public_token()
            res = await session.execute(
                select(Meeting.id).where(Meeting.public_token == token)
            )
            if res.scalar_one_or_none() is None:
                return token
        raise RuntimeError("failed to generate unique meeting public_token")

    async def backfill_meeting_public_tokens(self) -> int:
        """Assign public_token to meetings that do not have one yet."""
        updated = 0
        async with self.session() as s:
            res = await s.execute(select(Meeting).where(Meeting.public_token.is_(None)))
            meetings = res.scalars().all()
            for meeting in meetings:
                meeting.public_token = await self._generate_unique_public_token(s)
                updated += 1
            if updated:
                await s.commit()
        return updated

    # Meetings
    async def create_meeting(
        self,
        *,
        host_id: int,
        topic: str,
        description: str,
        start_at_utc: datetime,
        max_participants: int,
        location: str | None,
        photo_file_id: str | None = None,
        end_at_utc: datetime | None = None,
        registration_starts_at_utc: datetime | None = None,
    ) -> Meeting:
        """Create a new meeting and register the host (is_host=True, doesn't count against max)."""
        async with self.session() as s:
            public_token = await self._generate_unique_public_token(s)
            m = Meeting(
                topic=topic,
                description=description,
                start_at_utc=start_at_utc,
                end_at_utc=end_at_utc,
                registration_starts_at_utc=registration_starts_at_utc,
                max_participants=max_participants,
                location=location,
                photo_file_id=photo_file_id,
                public_token=public_token,
                created_by=host_id,
            )
            s.add(m)
            await s.flush()
            # Auto-register host as confirmed (is_host=True, excluded from participant count)
            reg = Registration(meeting_id=m.id, user_id=host_id, status=RegistrationStatus.CONFIRMED, is_host=True)
            s.add(reg)
            await s.commit()
            await s.refresh(m)
            return m

    async def get_meeting(self, meeting_id: int) -> Meeting | None:
        """Return meeting by id or None if not found."""
        async with self.session() as s:
            return await s.get(Meeting, meeting_id)

    async def get_meeting_by_public_token(self, public_token: str) -> Meeting | None:
        """Return meeting by public deep-link token or None if not found."""
        async with self.session() as s:
            res = await s.execute(select(Meeting).where(Meeting.public_token == public_token))
            return res.scalar_one_or_none()

    async def list_meetings_in_range(self, from_utc: datetime, to_utc: datetime) -> Sequence[Meeting]:
        """List non-canceled meetings whose start time falls within [from_utc, to_utc]."""
        async with self.session() as s:
            res = await s.execute(
                select(Meeting)
                .where(
                    Meeting.canceled_at.is_(None),
                    Meeting.start_at_utc >= from_utc,
                    Meeting.start_at_utc <= to_utc,
                )
                .order_by(Meeting.start_at_utc.asc())
            )
            return res.scalars().all()

    async def list_upcoming_meetings(self, now_utc: datetime) -> Sequence[Meeting]:
        """List meetings starting at/after the given UTC datetime."""
        async with self.session() as s:
            res = await s.execute(
                select(Meeting).where(Meeting.canceled_at.is_(None), Meeting.start_at_utc >= now_utc).order_by(Meeting.start_at_utc.asc())
            )
            return res.scalars().all()

    async def list_upcoming_meetings_visible(
        self, now_utc: datetime, viewer_user_id: int | None = None
    ) -> Sequence[Meeting]:
        """List upcoming meetings visible to a viewer (registration-gated for non-hosts)."""
        meetings = await self.list_upcoming_meetings(now_utc)
        visible: list[Meeting] = []
        for meeting in meetings:
            if viewer_user_id is not None and meeting.created_by == viewer_user_id:
                visible.append(meeting)
            elif is_registration_open(meeting, now_utc):
                visible.append(meeting)
        return visible

    async def list_user_meetings(self, user_id: int, now_utc: datetime) -> Sequence[Meeting]:
        """List upcoming meetings the given user is confirmed to attend (or host)."""
        async with self.session() as s:
            res = await s.execute(
                select(Meeting)
                .join(Registration, Registration.meeting_id == Meeting.id)
                .where(
                    Registration.user_id == user_id,
                    Registration.status == RegistrationStatus.CONFIRMED,
                    Meeting.canceled_at.is_(None),
                    Meeting.start_at_utc >= now_utc,
                )
                .order_by(Meeting.start_at_utc.asc())
            )
            return res.scalars().all()

    async def count_confirmed(self, meeting_id: int) -> int:
        """Return the number of confirmed participants (excluding hosts)."""
        async with self.session() as s:
            return await self._count_confirmed_in_session(s, meeting_id)

    async def _count_confirmed_in_session(self, s: AsyncSession, meeting_id: int) -> int:
        res = await s.execute(
            select(func.count()).select_from(Registration).where(
                Registration.meeting_id == meeting_id,
                Registration.status == RegistrationStatus.CONFIRMED,
                Registration.is_host == False,  # noqa: E712
            )
        )
        return int(res.scalar_one())

    async def _count_offered_in_session(self, s: AsyncSession, meeting_id: int) -> int:
        res = await s.execute(
            select(func.count()).select_from(WaitlistEntry).where(
                WaitlistEntry.meeting_id == meeting_id,
                WaitlistEntry.status == WaitlistStatus.OFFERED,
            )
        )
        return int(res.scalar_one())

    async def count_reserved_spots(self, meeting_id: int) -> int:
        """Return the number of spots temporarily reserved by active offers."""
        async with self.session() as s:
            return await self._count_offered_in_session(s, meeting_id)

    async def available_spots(self, meeting_id: int) -> int:
        """Return open participant spots (excludes hosts and reserved offers)."""
        async with self.session() as s:
            m = await s.get(Meeting, meeting_id)
            if m is None:
                return 0
            confirmed = await self._count_confirmed_in_session(s, meeting_id)
            reserved = await self._count_offered_in_session(s, meeting_id)
            return max(m.max_participants - confirmed - reserved, 0)

    async def is_meeting_open(self, meeting_id: int, now_utc: datetime) -> bool:
        """Return True if the meeting exists, is not canceled, and has not started."""
        from .utils import ensure_utc as _ensure_utc

        now_utc = _ensure_utc(now_utc)
        async with self.session() as s:
            m = await s.get(Meeting, meeting_id)
            if m is None or m.canceled_at is not None:
                return False
            start = _ensure_utc(m.start_at_utc)
            return start >= now_utc

    async def count_hosts(self, meeting_id: int) -> int:
        """Return the number of confirmed hosts for the meeting."""
        async with self.session() as s:
            res = await s.execute(
                select(func.count()).select_from(Registration).where(
                    Registration.meeting_id == meeting_id,
                    Registration.status == RegistrationStatus.CONFIRMED,
                    Registration.is_host == True,
                )
            )
            return int(res.scalar_one())

    async def get_user_name(self, user_id: int) -> str | None:
        """Return a user's display name by id (full name preferred, fallback to username)."""
        async with self.session() as s:
            u = await s.get(User, user_id)
            if not u:
                return None
            return u.name or (u.username or None)

    async def get_user(self, user_id: int) -> User | None:
        """Return full User object by id or None if not found."""
        async with self.session() as s:
            return await s.get(User, user_id)

    # Registration
    async def list_confirmed_participants(self, meeting_id: int) -> Sequence[Row[tuple[Registration, User]]]:
        """Return confirmed non-host registrations joined with user data, ordered by signup time."""
        async with self.session() as s:
            res = await s.execute(
                select(Registration, User)
                .join(User, User.id == Registration.user_id)
                .where(
                    Registration.meeting_id == meeting_id,
                    Registration.status == RegistrationStatus.CONFIRMED,
                    Registration.is_host == False,  # noqa: E712
                )
                .order_by(Registration.created_at.asc())
            )
            return res.all()

    async def register(
        self,
        meeting_id: int,
        user_id: int,
        *,
        local_tz=None,
    ) -> tuple[bool, str, str | None]:
        """Register a user for a meeting when spots are available.

        Returns:
            Tuple of (success, message, registration_status). Status is None on failure.
        """
        async with self.session() as s:
            m = await s.get(Meeting, meeting_id)
            if m is None or m.canceled_at is not None:
                return False, "Meeting not found or canceled.", None
            now_utc = datetime.now(timezone.utc)
            start = ensure_utc(m.start_at_utc)
            if start < now_utc:
                return False, "Meeting not found or canceled.", None
            if not is_registration_open(m, now_utc):
                tz_obj = local_tz
                if tz_obj is None:
                    from dateutil import tz as dateutil_tz
                    tz_obj = dateutil_tz.gettz("Europe/Berlin")
                when = ensure_utc(m.registration_starts_at_utc).astimezone(tz_obj)
                return (
                    False,
                    f"Регистрация откроется {when:%d.%m.%Y %H:%M}.",
                    None,
                )
            res = await s.execute(
                select(Registration).where(
                    Registration.meeting_id == meeting_id,
                    Registration.user_id == user_id,
                    Registration.status == RegistrationStatus.CONFIRMED,
                )
            )
            if res.scalar_one_or_none():
                return False, "You are already registered.", None
            confirmed = await self._count_confirmed_in_session(s, meeting_id)
            reserved = await self._count_offered_in_session(s, meeting_id)
            if confirmed + reserved >= m.max_participants:
                return False, "Meeting is full. Join the waitlist if a spot opens.", None
            reg = Registration(
                meeting_id=meeting_id,
                user_id=user_id,
                status=RegistrationStatus.CONFIRMED,
            )
            s.add(reg)
            await s.commit()
            return True, REGISTRATION_SUCCESS, RegistrationStatus.CONFIRMED

    async def is_registered(self, meeting_id: int, user_id: int) -> bool:
        """Check if user has an active confirmed registration."""
        async with self.session() as s:
            res = await s.execute(
                select(Registration).where(
                    Registration.meeting_id == meeting_id,
                    Registration.user_id == user_id,
                    Registration.status == RegistrationStatus.CONFIRMED,
                )
            )
            return res.scalar_one_or_none() is not None

    async def unregister(self, meeting_id: int, user_id: int) -> tuple[bool, str]:
        """Cancel a user's confirmed registration for a meeting."""
        async with self.session() as s:
            res = await s.execute(
                select(Registration).where(
                    Registration.meeting_id == meeting_id,
                    Registration.user_id == user_id,
                    Registration.status == RegistrationStatus.CONFIRMED,
                )
            )
            reg = res.scalar_one_or_none()
            if not reg:
                return False, "You are not registered."
            reg.status = RegistrationStatus.CANCELED
            await s.commit()
            return True, "You have been unregistered."

    async def list_meeting_participants(self, meeting_id: int) -> Sequence[Registration]:
        """Return confirmed registrations for a meeting."""
        async with self.session() as s:
            res = await s.execute(
                select(Registration).where(Registration.meeting_id == meeting_id, Registration.status == RegistrationStatus.CONFIRMED)
            )
            return res.scalars().all()

    async def cancel_meeting(self, meeting_id: int, canceled_at: datetime) -> Meeting | None:
        """Mark a meeting as canceled by setting canceled_at timestamp.

        Returns the updated Meeting or None if not found.
        """
        async with self.session() as s:
            m = await s.get(Meeting, meeting_id)
            if m is None:
                return None
            m.canceled_at = canceled_at
            await s.commit()
            await s.refresh(m)
            return m

    async def update_meeting(
        self,
        meeting_id: int,
        topic: str | None = None,
        description: str | None = None,
        start_at_utc: datetime | None = None,
        end_at_utc: datetime | None = None,
        registration_starts_at_utc: datetime | None = None,
        max_participants: int | None = None,
        location: str | None = None,
        photo_file_id: str | None = None,
        *,
        clear_location: bool = False,
        clear_photo: bool = False,
        clear_registration_start: bool = False,
    ) -> Meeting | None:
        """Update meeting fields. Only provided values are updated.

        Returns the updated Meeting or None if not found.
        """
        async with self.session() as s:
            m = await s.get(Meeting, meeting_id)
            if m is None:
                return None
            if topic is not None:
                m.topic = topic
            if description is not None:
                m.description = description
            if start_at_utc is not None:
                m.start_at_utc = start_at_utc
            if end_at_utc is not None:
                m.end_at_utc = end_at_utc
            if clear_registration_start:
                m.registration_starts_at_utc = None
            elif registration_starts_at_utc is not None:
                m.registration_starts_at_utc = registration_starts_at_utc
            if max_participants is not None:
                m.max_participants = max_participants
            if clear_location:
                m.location = None
            elif location is not None:
                m.location = location
            if clear_photo:
                m.photo_file_id = None
            elif photo_file_id is not None:
                m.photo_file_id = photo_file_id
            await s.commit()
            await s.refresh(m)
            return m

    # Waitlist
    async def get_active_waitlist_entry(self, meeting_id: int, user_id: int) -> WaitlistEntry | None:
        """Return the user's active waitlist entry for a meeting, if any."""
        async with self.session() as s:
            res = await s.execute(
                select(WaitlistEntry).where(
                    WaitlistEntry.meeting_id == meeting_id,
                    WaitlistEntry.user_id == user_id,
                    WaitlistEntry.status.in_(WaitlistStatus.ACTIVE),
                )
            )
            return res.scalar_one_or_none()

    async def get_waitlist_entry(self, entry_id: int) -> WaitlistEntry | None:
        """Return a waitlist entry by id."""
        async with self.session() as s:
            return await s.get(WaitlistEntry, entry_id)

    async def create_waitlist_entry(self, meeting_id: int, user_id: int) -> WaitlistEntry:
        """Create a new waiting waitlist entry."""
        async with self.session() as s:
            entry = WaitlistEntry(
                meeting_id=meeting_id,
                user_id=user_id,
                status=WaitlistStatus.WAITING,
            )
            s.add(entry)
            await s.commit()
            await s.refresh(entry)
            return entry

    async def list_waitlist_for_meeting(
        self, meeting_id: int, *, include_all_statuses: bool = False
    ) -> Sequence[Row[tuple[WaitlistEntry, User]]]:
        """Return waitlist entries joined with user data, ordered by queue position."""
        async with self.session() as s:
            q = (
                select(WaitlistEntry, User)
                .join(User, User.id == WaitlistEntry.user_id)
                .where(WaitlistEntry.meeting_id == meeting_id)
                .order_by(WaitlistEntry.created_at.asc())
            )
            if not include_all_statuses:
                q = q.where(WaitlistEntry.status.in_(WaitlistStatus.ACTIVE))
            res = await s.execute(q)
            return res.all()

    async def count_active_waitlist(self, meeting_id: int) -> int:
        """Return the number of active waitlist entries for a meeting."""
        async with self.session() as s:
            res = await s.execute(
                select(func.count()).select_from(WaitlistEntry).where(
                    WaitlistEntry.meeting_id == meeting_id,
                    WaitlistEntry.status.in_(WaitlistStatus.ACTIVE),
                )
            )
            return int(res.scalar_one())

    async def get_queue_position(self, entry_id: int) -> int | None:
        """Return 1-based queue position among waiting entries."""
        async with self.session() as s:
            entry = await s.get(WaitlistEntry, entry_id)
            if entry is None or entry.status != WaitlistStatus.WAITING:
                return None
            res = await s.execute(
                select(func.count()).select_from(WaitlistEntry).where(
                    WaitlistEntry.meeting_id == entry.meeting_id,
                    WaitlistEntry.status == WaitlistStatus.WAITING,
                    WaitlistEntry.created_at < entry.created_at,
                )
            )
            return int(res.scalar_one()) + 1

    async def get_expired_offers(self, now_utc: datetime) -> Sequence[WaitlistEntry]:
        """Return offered entries whose offer has expired."""
        async with self.session() as s:
            res = await s.execute(
                select(WaitlistEntry).where(
                    WaitlistEntry.status == WaitlistStatus.OFFERED,
                    WaitlistEntry.offer_expires_at.is_not(None),
                    WaitlistEntry.offer_expires_at < now_utc,
                )
            )
            return res.scalars().all()
