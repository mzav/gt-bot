"""Async database access layer using SQLAlchemy 2.0 ORM.

Exposes a thin CRUD wrapper around the models for use by handlers/scheduler.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select, func, update

from .models import Base, User, Meeting, Registration, RegistrationStatus


class Database:
    """Simple async database facade for sessions and CRUD helpers."""
    def __init__(self, url: str):
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

    # Meetings
    async def create_meeting(self, *, host_id: int, topic: str, description: str, start_at_utc: datetime,
                              max_participants: int, location: str | None) -> Meeting:
        """Create a new meeting and auto-register the host as confirmed."""
        async with self.session() as s:
            m = Meeting(
                topic=topic,
                description=description,
                start_at_utc=start_at_utc,
                max_participants=max_participants,
                location=location,
                created_by=host_id,
            )
            s.add(m)
            await s.flush()
            # Auto-register host as confirmed
            reg = Registration(meeting_id=m.id, user_id=host_id, status=RegistrationStatus.CONFIRMED)
            s.add(reg)
            await s.commit()
            await s.refresh(m)
            return m

    async def get_meeting(self, meeting_id: int) -> Meeting | None:
        """Return meeting by id or None if not found."""
        async with self.session() as s:
            return await s.get(Meeting, meeting_id)

    async def list_upcoming_meetings(self, now_utc: datetime) -> Sequence[Meeting]:
        """List meetings starting at/after the given UTC datetime."""
        async with self.session() as s:
            res = await s.execute(
                select(Meeting).where(Meeting.canceled_at.is_(None), Meeting.start_at_utc >= now_utc).order_by(Meeting.start_at_utc.asc())
            )
            return res.scalars().all()

    async def list_user_meetings(self, user_id: int) -> Sequence[Meeting]:
        """List meetings the given user is confirmed to attend (or host)."""
        async with self.session() as s:
            res = await s.execute(
                select(Meeting)
                .join(Registration, Registration.meeting_id == Meeting.id)
                .where(Registration.user_id == user_id, Registration.status == RegistrationStatus.CONFIRMED)
                .order_by(Meeting.start_at_utc.asc())
            )
            return res.scalars().all()

    async def count_confirmed(self, meeting_id: int) -> int:
        """Return the number of confirmed registrations for the meeting."""
        async with self.session() as s:
            res = await s.execute(
                select(func.count()).select_from(Registration).where(
                    Registration.meeting_id == meeting_id,
                    Registration.status == RegistrationStatus.CONFIRMED,
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
    async def register(self, meeting_id: int, user_id: int) -> tuple[bool, str]:
        """Register a user for a meeting, using waitlist if full.

        Returns:
            Tuple[bool, str]: Success flag and a human-readable message.
        """
        async with self.session() as s:
            m = await s.get(Meeting, meeting_id)
            if m is None or m.canceled_at is not None:
                return False, "Meeting not found or canceled."
            # Check if already registered
            res = await s.execute(
                select(Registration).where(Registration.meeting_id == meeting_id, Registration.user_id == user_id,
                                           Registration.status == RegistrationStatus.CONFIRMED)
            )
            existing = res.scalar_one_or_none()
            if existing:
                return False, "You are already registered."
            # Count current confirmed
            res = await s.execute(
                select(func.count()).select_from(Registration).where(
                    Registration.meeting_id == meeting_id, Registration.status == RegistrationStatus.CONFIRMED
                )
            )
            count = int(res.scalar_one())
            status = RegistrationStatus.CONFIRMED if count < m.max_participants else RegistrationStatus.WAITLISTED
            reg = Registration(meeting_id=meeting_id, user_id=user_id, status=status)
            s.add(reg)
            await s.commit()
            if status == RegistrationStatus.WAITLISTED:
                return True, "Meeting is full. You are added to the waitlist."
            return True, "Registered successfully."

    async def unregister(self, meeting_id: int, user_id: int) -> tuple[bool, str]:
        """Cancel a user's registration for a meeting.

        Returns:
            Tuple[bool, str]: Success flag and a human-readable message.
        """
        async with self.session() as s:
            res = await s.execute(
                select(Registration).where(
                    Registration.meeting_id == meeting_id, Registration.user_id == user_id,
                    Registration.status.in_([RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED])
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
