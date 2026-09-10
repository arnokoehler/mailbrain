"""Remove labels a past run added that the current ruleset no longer assigns.

`classify` is additive by design, so a rule fix does not undo labels already
written to Gmail. This reconciles them: for every message a run touched, it
re-runs the current rules and drops any label that run added but no rule
justifies any more. Labels the user set by hand are never touched, because only
labels absent from `labels_before` are considered.

    uv run python scripts/reconcile.py <run-id> [--apply]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from mailbrain.config import load_rules
from mailbrain.gmail.auth import build_service, load_credentials
from mailbrain.gmail.client import GmailClient
from mailbrain.labels import name_to_id
from mailbrain.paths import credentials_path, db_path, token_path
from mailbrain.rules.engine import rule_matches
from mailbrain.rules.models import MessageMeta
from mailbrain.storage.db import session_factory
from mailbrain.storage.models import Label, Message, Mutation

BATCH_SIZE = 1000


def unjustified_archives(session, run_id: int) -> list[str]:
    """Messages the run archived that no current rule would archive any more."""
    rules = load_rules(Path("config/rules.yaml")).rules
    now = datetime.now(UTC).replace(tzinfo=None)
    id_to_name = {lbl.gmail_id: lbl.name for lbl in session.scalars(select(Label)).all()}

    rows = session.execute(
        select(
            Mutation.message_gmail_id,
            Mutation.archived_before,
            Mutation.archived_after,
            Message.sender,
            Message.subject,
            Message.internal_date,
            Message.label_ids,
        )
        .join(Message, Message.gmail_id == Mutation.message_gmail_id)
        .where(Mutation.run_id == run_id)
    ).all()

    out = []
    for row in rows:
        gmail_id, in_inbox_before, in_inbox_after = row[0], row[1], row[2]
        sender, subject, internal_date, label_ids = row[3], row[4], row[5], row[6]
        run_archived_it = bool(in_inbox_before) and not bool(in_inbox_after)
        if not run_archived_it:
            continue
        names = [id_to_name.get(lid, lid) for lid in json.loads(label_ids or "[]")]
        if "INBOX" in names:
            continue
        meta = MessageMeta(
            gmail_id=gmail_id,
            sender=sender or "",
            subject=subject or "",
            internal_date=internal_date or now,
            current_labels=tuple(names),
        )
        if not any(r.actions.archive for r in rules if rule_matches(r, meta, now)):
            out.append(gmail_id)
    return out


def stale_labels(session, run_id: int) -> dict[str, set[str]]:
    """message id -> labels the run added that no current rule assigns."""
    rules = load_rules(Path("config/rules.yaml")).rules
    now = datetime.now(UTC).replace(tzinfo=None)
    id_to_name = {lbl.gmail_id: lbl.name for lbl in session.scalars(select(Label)).all()}

    rows = session.execute(
        select(
            Mutation.message_gmail_id,
            Mutation.labels_before,
            Mutation.labels_after,
            Message.sender,
            Message.subject,
            Message.internal_date,
            Message.label_ids,
        ).join(Message, Message.gmail_id == Mutation.message_gmail_id)
        .where(Mutation.run_id == run_id)
    ).all()

    stale: dict[str, set[str]] = {}
    for gmail_id, before, after, sender, subject, internal_date, label_ids in rows:
        added = set(json.loads(after)) - set(json.loads(before))
        if not added:
            continue
        current = [id_to_name.get(lid, lid) for lid in json.loads(label_ids or "[]")]
        meta = MessageMeta(
            gmail_id=gmail_id,
            sender=sender or "",
            subject=subject or "",
            internal_date=internal_date or now,
            current_labels=tuple(current),
        )
        justified: set[str] = set()
        for rule in rules:
            if rule_matches(rule, meta, now):
                justified.update(rule.actions.add_labels)
        still_present = set(current)
        unjustified = {
            lbl
            for lbl in added - justified
            if lbl not in {"INBOX", "UNREAD"} and lbl in still_present
        }
        if unjustified:
            stale[gmail_id] = unjustified
    return stale


def main() -> None:
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(positional) != 1:
        print(__doc__)
        raise SystemExit(2)
    run_id = int(positional[0])
    live = "--apply" in sys.argv

    factory = session_factory(db_path())
    with factory() as session:
        stale = stale_labels(session, run_id)
        unarchive = unjustified_archives(session, run_id)
        label_ids = name_to_id(session)

    by_label: Counter[str] = Counter()
    for labels in stale.values():
        by_label.update(labels)

    print(f"run {run_id}: {len(stale)} messages carry a label no current rule assigns\n")
    for label, count in by_label.most_common():
        known = "" if label in label_ids else "  (not in label cache - skipped)"
        print(f"{count:5d}  {label}{known}")
    print(f"\n{len(unarchive)} messages archived by this run that no rule would archive now")
    if not live:
        print("\ndry run - pass --apply to repair these in Gmail")
        return

    if unarchive:
        client = GmailClient(build_service(load_credentials(credentials_path(), token_path())))
        for start in range(0, len(unarchive), BATCH_SIZE):
            batch = unarchive[start : start + BATCH_SIZE]
            client.batch_modify(message_ids=batch, add_label_ids=["INBOX"], remove_label_ids=[])
        with factory() as session:
            for row in session.scalars(select(Message).where(Message.gmail_id.in_(unarchive))):
                labels = json.loads(row.label_ids or "[]")
                if "INBOX" not in labels:
                    row.label_ids = json.dumps([*labels, "INBOX"])
            session.commit()
        print(f"restored {len(unarchive)} messages to the inbox")

    per_label: dict[str, list[str]] = defaultdict(list)
    for gmail_id, labels in stale.items():
        for label in labels:
            if label in label_ids:
                per_label[label].append(gmail_id)

    client = GmailClient(build_service(load_credentials(credentials_path(), token_path())))
    removed = 0
    for label, message_ids in per_label.items():
        gmail_label = label_ids[label]
        for start in range(0, len(message_ids), BATCH_SIZE):
            batch = message_ids[start : start + BATCH_SIZE]
            client.batch_modify(
                message_ids=batch, add_label_ids=[], remove_label_ids=[gmail_label]
            )
        removed += len(message_ids)

        with factory() as session:
            for row in session.scalars(select(Message).where(Message.gmail_id.in_(message_ids))):
                kept = [lid for lid in json.loads(row.label_ids or "[]") if lid != gmail_label]
                row.label_ids = json.dumps(kept)
            session.commit()

    print(f"\nremoved {removed} label assignments and synced the cache")


if __name__ == "__main__":
    main()
