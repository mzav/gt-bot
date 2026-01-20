"""SQLAlchemy ORM models for users, meetings, and registrations."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, Text, Integer, BigInteger, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarative class for SQLAlchemy models."""
    pass


class User(Base):
    """Represents a Telegram user interacting with the bot."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    name: Mapped[str] = mapped_column(String(255))
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    registrations: Mapped[list[Registration]] = relationship("Registration", back_populates="user", cascade="all, delete-orphan")
    hosted_meetings: Mapped[list[Meeting]] = relationship("Meeting", back_populates="host")


class Meeting(Base):
    """A scheduled meeting created/hosted by a user."""
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    start_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    max_participants: Mapped[int] = mapped_column(Integer)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    host: Mapped[User] = relationship("User", back_populates="hosted_meetings")
    registrations: Mapped[list[Registration]] = relationship("Registration", back_populates="meeting", cascade="all, delete-orphan")


class RegistrationStatus:
    """Constants representing a participant's registration status."""
    CONFIRMED = "confirmed"
    WAITLISTED = "waitlisted"
    CANCELED = "canceled"


class Registration(Base):
    """Join table linking users to meetings with a status."""
    __tablename__ = "registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default=RegistrationStatus.CONFIRMED, index=True)
    is_host: Mapped[bool] = mapped_column(default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    meeting: Mapped[Meeting] = relationship("Meeting", back_populates="registrations")
    user: Mapped[User] = relationship("User", back_populates="registrations")
