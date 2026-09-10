"""Fetch Gmail metadata via a client and cache it to SQLite, plus read-back."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.rules.models import MessageMeta
from mailbrain.storage.models import Label, Message

logger = logging.getLogger(__name__)


class SupportsGmailReads(Protocol):
    def list_message_ids(self, query: str) -> list[str]: ...
    def get_metadata(self, message_id: str) -> dict[str, Any]: ...
    def list_labels(self) -> dict[str, str]: ...


def _upsert_labels(session: Session, id_to_name: dict[str, str]) -> None:
    existing = {lbl.gmail_id: lbl for lbl in session.scalars(select(Label)).all()}
    for gmail_id, name in id_to_name.items():
        row = existing.get(gmail_id)
        if row is None:
            session.add(Label(gmail_id=gmail_id, name=name))
        else:
            row.name = name


def _upsert_message(session: Session, meta: dict[str, Any]) -> None:
    row = session.scalar(select(Message).where(Message.gmail_id == meta["gmail_id"]))
    if row is None:
        row = Message(gmail_id=meta["gmail_id"])
        session.add(row)
    row.thread_gmail_id = meta.get("thread_id")
    row.sender = meta.get("sender")
    row.subject = meta.get("subject")
    row.snippet = meta.get("snippet")
    row.label_ids = json.dumps(meta.get("label_ids", []))
    row.internal_date = datetime.fromtimestamp(meta["internal_date_ms"] / 1000, tz=UTC)


def scan_mailbox(
    client: SupportsGmailReads,
    query: str,
    session_factory: sessionmaker[Session],
    batch_size: int = 200,
    skip_cached: bool = False,
) -> int:
    """Fetch message metadata for `query`, cache messages + labels. Returns the
    number of messages fetched and cached this run.

    Commits labels first, then messages in batches of `batch_size`, so an
    interrupt (Ctrl-C) keeps everything fetched so far instead of discarding
    the whole scan. Progress is logged at INFO.

    A message whose metadata fetch fails (e.g. a Gmail 400 "failedPrecondition"
    on one id) is logged and skipped rather than aborting the whole scan.

    With ``skip_cached=True`` any id already present in the cache is skipped, so
    a re-run resumes an interrupted scan cheaply instead of refetching
    everything. Default False refetches and refreshes every message.
    """
    logger.info("Listing message ids for query %r ...", query)
    ids = client.list_message_ids(query)
    labels = client.list_labels()
    total = len(ids)
    logger.info("Found %d messages and %d labels; fetching metadata ...", total, len(labels))
    with session_factory() as session:
        _upsert_labels(session, labels)
        session.commit()  # labels available even if the message loop is interrupted
        already = set(session.scalars(select(Message.gmail_id)).all()) if skip_cached else set()
        cached = 0
        failed = 0
        for i, message_id in enumerate(ids, 1):
            if message_id in already:
                continue
            try:
                meta = client.get_metadata(message_id)
            except Exception as exc:  # noqa: BLE001 - one bad id must not abort a bulk scan
                failed += 1
                logger.warning("skipping message %s: %s", message_id, exc)
                continue
            _upsert_message(session, meta)
            cached += 1
            if cached % batch_size == 0:
                session.commit()
                logger.info("cached %d messages (%d/%d scanned)", cached, i, total)
        session.commit()
    logger.info("Done: cached %d messages this run (%d skipped)", cached, failed)
    return cached


def load_cached(
    session_factory: sessionmaker[Session],
) -> tuple[list[MessageMeta], dict[str, set[str]]]:
    """Read cached messages back as MessageMeta + a gmail_id -> current label NAMES map."""
    with session_factory() as session:
        id_to_name = {lbl.gmail_id: lbl.name for lbl in session.scalars(select(Label)).all()}
        messages: list[MessageMeta] = []
        current: dict[str, set[str]] = {}
        for row in session.scalars(select(Message)).all():
            label_ids: list[str] = json.loads(row.label_ids) if row.label_ids else []
            names = {id_to_name.get(lid, lid) for lid in label_ids}
            current[row.gmail_id] = names
            raw_date = row.internal_date
            if raw_date is None:
                internal_date = datetime.fromtimestamp(0, tz=UTC)
            elif raw_date.tzinfo is None:
                internal_date = raw_date.replace(tzinfo=UTC)
            else:
                internal_date = raw_date
            messages.append(
                MessageMeta(
                    gmail_id=row.gmail_id,
                    sender=row.sender or "",
                    subject=row.subject or "",
                    internal_date=internal_date,
                    current_labels=tuple(sorted(names)),
                )
            )
    return messages, current
