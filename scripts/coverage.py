"""Report which inbox senders no rule currently matches, by volume."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path

from sqlalchemy import select

from mailbrain.config import load_rules
from mailbrain.gmail.auth import build_service, load_credentials
from mailbrain.gmail.client import GmailClient
from mailbrain.paths import credentials_path, db_path, token_path
from mailbrain.rules.engine import rule_matches
from mailbrain.rules.models import MessageMeta
from mailbrain.storage.db import session_factory
from mailbrain.storage.models import Message

TOP_N = 60


def live_inbox_ids() -> set[str]:
    creds = load_credentials(credentials_path(), token_path())
    client = GmailClient(build_service(creds))
    return set(client.list_message_ids("in:inbox"))


def sender_domain(sender: str) -> str:
    _, address = parseaddr(sender)
    return address.partition("@")[2].lower()


def main() -> None:
    rules = load_rules(Path("config/rules.yaml")).rules
    now = datetime.now(UTC).replace(tzinfo=None)

    inbox_ids = live_inbox_ids()

    unmatched: Counter[str] = Counter()
    unmatched_examples: dict[str, str] = {}
    matched_total = 0
    inbox_total = 0

    with session_factory(db_path())() as session:
        rows = session.execute(
            select(
                Message.gmail_id,
                Message.sender,
                Message.subject,
                Message.internal_date,
                Message.label_ids,
            )
        ).all()

    for gmail_id, sender, subject, internal_date, label_ids in rows:
        if gmail_id not in inbox_ids:
            continue
        labels = json.loads(label_ids or "[]")
        inbox_total += 1
        meta = MessageMeta(
            gmail_id=gmail_id,
            sender=sender or "",
            subject=subject or "",
            internal_date=internal_date or now,
            current_labels=tuple(labels),
        )
        if any(rule_matches(r, meta, now) for r in rules):
            matched_total += 1
        else:
            domain = sender_domain(meta.sender) or "(no domain)"
            unmatched[domain] += 1
            unmatched_examples.setdefault(domain, meta.subject[:60])

    print(
        f"live inbox {len(inbox_ids)}  in cache {inbox_total}  "
        f"matched {matched_total}  unmatched {sum(unmatched.values())}"
    )
    print(f"unmatched domains: {len(unmatched)}")
    print()
    for domain, count in unmatched.most_common(TOP_N):
        print(f"{count:5d}  {domain:42s} {unmatched_examples[domain]}")


if __name__ == "__main__":
    main()
