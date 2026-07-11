"""SQLAlchemy ORM models — the full v0.2 schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(unique=True)


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_id: Mapped[str] = mapped_column(unique=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_id: Mapped[str] = mapped_column(unique=True)
    thread_gmail_id: Mapped[str | None] = mapped_column(default=None)
    sender: Mapped[str | None] = mapped_column(default=None)
    subject: Mapped[str | None] = mapped_column(default=None)
    snippet: Mapped[str | None] = mapped_column(default=None)
    history_id: Mapped[str | None] = mapped_column(default=None)
    internal_date: Mapped[datetime | None] = mapped_column(default=None)
    label_ids: Mapped[str | None] = mapped_column(default=None)  # JSON array of gmail label ids


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    dry_run: Mapped[bool] = mapped_column(default=True)
    scanned: Mapped[int] = mapped_column(default=0)
    labeled: Mapped[int] = mapped_column(default=0)
    archived: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)


class Mutation(Base):
    __tablename__ = "mutations"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    message_gmail_id: Mapped[str] = mapped_column()
    labels_before: Mapped[str] = mapped_column(default="[]")  # JSON array
    labels_after: Mapped[str] = mapped_column(default="[]")  # JSON array
    archived_before: Mapped[bool | None] = mapped_column(default=None)
    archived_after: Mapped[bool | None] = mapped_column(default=None)
    read_before: Mapped[bool | None] = mapped_column(default=None)
    read_after: Mapped[bool | None] = mapped_column(default=None)
    applied: Mapped[bool] = mapped_column(default=False)


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    iso_week: Mapped[str] = mapped_column(unique=True)  # one digest per ISO week, e.g. "2026-W28"
    notion_page_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime | None] = mapped_column(default=None)


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    message_gmail_id: Mapped[str] = mapped_column()
    suggested: Mapped[str] = mapped_column(default="{}")  # JSON: labels + booleans
    source: Mapped[str] = mapped_column(default="conflict")  # conflict | ai (Phase 2)
    reason: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="pending")  # pending | resolved


class SyncState(Base):
    """Singleton row holding the Gmail incremental-scan cursor.

    The application maintains exactly one row here (id=1); code should
    upsert rather than insert new rows.
    """

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    last_history_id: Mapped[str | None] = mapped_column(default=None)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
