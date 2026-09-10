"""Flag from_domain rules whose target label is a poor fit for that domain.

Compares each rule's target label against the label distribution the mailbox
had *before* MailBrain ever wrote to it: for messages a run touched, the
historic set is that run's earliest labels_before; untouched messages keep
their current labels. A domain whose top historic label is not the rule's
target, or holds less than the threshold share, is an overfit like
iodigital.com -> Administratie/leaseauto.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from email.utils import parseaddr
from pathlib import Path

from sqlalchemy import select

from mailbrain.config import load_rules
from mailbrain.labels import name_to_id
from mailbrain.paths import db_path
from mailbrain.storage.db import session_factory
from mailbrain.storage.models import Message, Mutation

SHARE_THRESHOLD = 0.70
MIN_LABELLED = 3
SYSTEM_PREFIXES = ("CATEGORY_",)
SYSTEM_LABELS = {
    "INBOX", "UNREAD", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT", "CHAT",
    "YELLOW_STAR", "BLUE_STAR", "RED_CIRCLE", "GREEN_CIRCLE",
    "to-review", "to-be-removed",
}


def is_user_label(label: str) -> bool:
    return label not in SYSTEM_LABELS and not label.startswith(SYSTEM_PREFIXES)


def sender_domain(sender: str) -> str:
    return parseaddr(sender)[1].partition("@")[2].lower()


def historic_labels_by_domain(session) -> dict[str, Counter[str]]:
    earliest_before: dict[str, tuple[int, list[str]]] = {}
    for gmail_id, run_id, before in session.execute(
        select(Mutation.message_gmail_id, Mutation.run_id, Mutation.labels_before)
    ).all():
        seen = earliest_before.get(gmail_id)
        if seen is None or run_id < seen[0]:
            earliest_before[gmail_id] = (run_id, json.loads(before))

    id_to_name = {gid: name for name, gid in name_to_id(session).items()}
    per_domain: dict[str, Counter[str]] = defaultdict(Counter)
    rows = session.execute(
        select(Message.gmail_id, Message.sender, Message.label_ids)
    ).all()
    for gmail_id, sender, label_ids in rows:
        touched = earliest_before.get(gmail_id)
        raw = touched[1] if touched else json.loads(label_ids or "[]")
        names = [id_to_name.get(lid, lid) for lid in raw]
        for name in filter(is_user_label, names):
            per_domain[sender_domain(sender or "")][name] += 1
    return per_domain


def main() -> None:
    rules = load_rules(Path("config/rules.yaml")).rules
    with session_factory(db_path())() as session:
        per_domain = historic_labels_by_domain(session)

    findings: list[tuple[float, str, str, str, int, str, int]] = []
    for rule in rules:
        targets = set(rule.actions.add_labels)
        if not targets or not rule.match.from_domain or rule.match.subject_contains:
            continue
        for domain in rule.match.from_domain:
            counts = per_domain.get(domain.lower(), Counter())
            total = sum(counts.values())
            if total < MIN_LABELLED:
                continue
            hit = sum(count for name, count in counts.items() if name in targets)
            share = hit / total
            if share >= SHARE_THRESHOLD:
                continue
            top_name, top_count = counts.most_common(1)[0]
            findings.append(
                (share, domain, rule.id, sorted(targets)[0], total, top_name, top_count)
            )

    findings.sort()
    print(f"{len(findings)} domain rules below {SHARE_THRESHOLD:.0%} historic agreement\n")
    print(
        f"{'share':>6}  {'domain':32s} {'rule':34s} "
        f"{'rule label':28s} {'n':>4}  top historic label"
    )
    for share, domain, rule_id, target, total, top_name, top_count in findings:
        print(
            f"{share:6.0%}  {domain:32s} {rule_id:34s} {target:28s} {total:4d}  "
            f"{top_name} ({top_count})"
        )


if __name__ == "__main__":
    main()
