"""Fetch Gmail metadata via a client and cache it to SQLite, plus read-back."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.rules.models import MessageMeta
from mailbrain.storage.models import Label, Message


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
) -> int:
    """Fetch all message metadata for `query`, cache messages + labels. Returns count."""
    ids = client.list_message_ids(query)
    labels = client.list_labels()
    with session_factory() as session:
        _upsert_labels(session, labels)
        for message_id in ids:
            _upsert_message(session, client.get_metadata(message_id))
        session.commit()
    return len(ids)


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
            messages.append(
                MessageMeta(
                    gmail_id=row.gmail_id,
                    sender=row.sender or "",
                    subject=row.subject or "",
                    internal_date=row.internal_date or datetime.fromtimestamp(0, tz=UTC),
                    current_labels=tuple(sorted(names)),
                )
            )
    return messages, current
