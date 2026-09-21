"""SQLAlchemy ORM models — the full v0.2 schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column()


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
    last_scan_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), default=None)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'invalidated')",
            name="ck_scan_runs_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column()
    started_at: Mapped[datetime] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="running", server_default="running")
    listed_count: Mapped[int] = mapped_column(default=0, server_default="0")
    fetched_count: Mapped[int] = mapped_column(default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(default=None)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("run_type IN ('apply', 'rollback')", name="ck_runs_run_type"),
        CheckConstraint(
            "status IN ('prepared', 'running', 'succeeded', 'partial', 'failed', "
            "'needs_review', 'legacy', 'abandoned')",
            name="ck_runs_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    dry_run: Mapped[bool] = mapped_column(default=True)
    scanned: Mapped[int] = mapped_column(default=0)
    labeled: Mapped[int] = mapped_column(default=0)
    archived: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
    run_type: Mapped[str] = mapped_column(default="apply", server_default="apply")
    status: Mapped[str] = mapped_column(default="legacy", server_default="legacy")
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"), default=None)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), default=None)
    rules_hash: Mapped[str | None] = mapped_column(default=None)
    safety_hash: Mapped[str | None] = mapped_column(default=None)
    prepared_at: Mapped[datetime | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
    intended_count: Mapped[int] = mapped_column(default=0, server_default="0")
    applied_count: Mapped[int] = mapped_column(default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(default=0, server_default="0")
    uncertain_count: Mapped[int] = mapped_column(default=0, server_default="0")
    conflict_count: Mapped[int] = mapped_column(default=0, server_default="0")
    abandoned_at: Mapped[datetime | None] = mapped_column(default=None)
    abandonment_reason: Mapped[str | None] = mapped_column(default=None)


class Mutation(Base):
    __tablename__ = "mutations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_flight', 'applied', 'failed', 'uncertain', "
            "'conflict', 'legacy', 'abandoned')",
            name="ck_mutations_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    message_gmail_id: Mapped[str] = mapped_column()
    labels_before: Mapped[str] = mapped_column(default="[]", server_default="[]")  # JSON array
    labels_after: Mapped[str] = mapped_column(default="[]", server_default="[]")  # JSON array
    archived_before: Mapped[bool | None] = mapped_column(default=None)
    archived_after: Mapped[bool | None] = mapped_column(default=None)
    read_before: Mapped[bool | None] = mapped_column(default=None)
    read_after: Mapped[bool | None] = mapped_column(default=None)
    applied: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(default="legacy", server_default="legacy")
    batch_id: Mapped[str | None] = mapped_column(default=None)
    original_mutation_id: Mapped[int | None] = mapped_column(
        ForeignKey("mutations.id"), default=None
    )
    matched_rule_ids: Mapped[str] = mapped_column(default="[]", server_default="[]")
    gmail_label_ids_before: Mapped[str | None] = mapped_column(default=None)
    gmail_label_ids_add: Mapped[str | None] = mapped_column(default=None)
    gmail_label_ids_remove: Mapped[str | None] = mapped_column(default=None)
    prepared_at: Mapped[datetime | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    reconciled_at: Mapped[datetime | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
    abandoned_at: Mapped[datetime | None] = mapped_column(default=None)
    abandonment_reason: Mapped[str | None] = mapped_column(default=None)


class LabelCreationIntent(Base):
    __tablename__ = "label_creation_intents"
    __table_args__ = (
        Index("uq_label_creation_intents_run_name", "run_id", "label_name", unique=True),
        CheckConstraint(
            "status IN ('pending', 'in_flight', 'resolved', 'failed', 'uncertain', 'abandoned')",
            name="ck_label_creation_intents_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    label_name: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="pending", server_default="pending")
    gmail_label_id: Mapped[str | None] = mapped_column(default=None)
    prepared_at: Mapped[datetime] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    reconciled_at: Mapped[datetime | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)


class Digest(Base):
    __tablename__ = "digests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'failed', 'uncertain', 'published')",
            name="ck_digests_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    iso_week: Mapped[str] = mapped_column(unique=True)  # one digest per ISO week, e.g. "2026-W28"
    notion_page_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="pending", server_default="pending")
    last_error: Mapped[str | None] = mapped_column(default=None)
    last_attempt_at: Mapped[datetime | None] = mapped_column(default=None)
    published_at: Mapped[datetime | None] = mapped_column(default=None)


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
