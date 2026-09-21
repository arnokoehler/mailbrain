"""Reverse one wrongly-added label from a single run, scoped by sender domain.

Only messages where the given run actually added the label are touched, so
labels the user set by hand are left alone. Usage:

    uv run python scripts/unlabel.py <run-id> <label-name> <sender-domain>

Write mode is disabled; use ``mailbrain rollback`` for audited repair.
"""

from __future__ import annotations

import json
import sys

from sqlalchemy import select

from mailbrain.labels import name_to_id
from mailbrain.locking import LockUnavailableError, process_lock
from mailbrain.paths import db_path
from mailbrain.storage.db import session_factory
from mailbrain.storage.models import Message, Mutation


def added_by_run(session, run_id: int, label: str, domain: str) -> list[str]:
    rows = session.execute(
        select(Mutation.message_gmail_id, Mutation.labels_before, Mutation.labels_after)
        .join(Message, Message.gmail_id == Mutation.message_gmail_id)
        .where(Mutation.run_id == run_id, Message.sender.like(f"%@{domain}%"))
    ).all()
    return [
        gmail_id
        for gmail_id, before, after in rows
        if label in json.loads(after) and label not in json.loads(before)
    ]


def strip_from_cache(session, gmail_ids: list[str], label_id: str) -> None:
    for row in session.scalars(select(Message).where(Message.gmail_id.in_(gmail_ids))):
        labels = [lid for lid in json.loads(row.label_ids or "[]") if lid != label_id]
        row.label_ids = json.dumps(labels)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    if len(args) != 3:
        print(__doc__)
        raise SystemExit(2)
    run_id, label, domain = int(args[0]), args[1], args[2]
    live = "--apply" in sys.argv
    if live:
        print(
            "write mode is disabled; use 'mailbrain rollback' so safety, audit, "
            "and live checks apply"
        )
        raise SystemExit(2)

    try:
        with process_lock():
            factory = session_factory(db_path())
            with factory() as session:
                targets = added_by_run(session, run_id, label, domain)
                label_id = name_to_id(session).get(label)
    except LockUnavailableError as error:
        print(error)
        raise SystemExit(2) from error

    print(f"run {run_id}: {len(targets)} messages from @{domain} got '{label}' (id {label_id})")
    print("read-only analysis; write mode is disabled")


if __name__ == "__main__":
    main()
