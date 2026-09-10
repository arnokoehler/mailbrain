"""Surface the highest-risk classification decisions for a human read-through.

The domain-vs-history audit only catches whole-domain overfits. These five
sections catch the rest:

  archived-actionable  archived mail whose subject reads like something you
                       still have to act on - the most expensive error class
  personal-vendor      mail Gmail filed as CATEGORY_PERSONAL that got a
                       vendor label, i.e. a person treated as a shop
  cross-tree           one message labelled in two unrelated label trees
  subject-rule-reach   what each subject-only rule actually matched
  per-rule-sample      a sample of every firing rule, to eyeball the mapping

    uv run python scripts/review.py [section ...]
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path

from sqlalchemy import select

from mailbrain.config import Rule, load_rules
from mailbrain.paths import db_path
from mailbrain.rules.engine import rule_matches
from mailbrain.rules.models import MessageMeta
from mailbrain.storage.db import session_factory
from mailbrain.storage.models import Label, Message, Mutation

ACTIONABLE_SUBJECT = [
    "factuur", "invoice", "aanslag", "aangifte", "herinnering", "reminder", "afspraak",
    "appointment", "contract", "overeenkomst", "ondertekenen", "signature", "deadline",
    "vervalt", "verloopt", "expires", "betaal", "payment due", "openstaand", "achterstand",
    "incasso", "machtiging", "opzegging", "opzeggen", "termination", "wwft", "identificatie",
    "verzoek", "actie vereist", "action required", "reageer", "antwoord", "urgent",
    "laatste herinnering", "aanmaning", "boete", "naheffing", "bezwaar", "hypotheek",
    "koopovereenkomst", "taxatie", "notaris", "verzekering", "polis", "uitnodiging",
]
SAMPLE = 6
STAR_LABELS = {"YELLOW_STAR", "BLUE_STAR", "RED_CIRCLE", "GREEN_CIRCLE"}
HOUSEKEEPING = {"to-be-removed", "to-review"}


def load_messages(session) -> list[tuple[MessageMeta, list[str]]]:
    id_to_name = {lbl.gmail_id: lbl.name for lbl in session.scalars(select(Label)).all()}
    now = datetime.now(UTC).replace(tzinfo=None)
    out = []
    for row in session.scalars(select(Message)).all():
        names = [id_to_name.get(lid, lid) for lid in json.loads(row.label_ids or "[]")]
        meta = MessageMeta(
            gmail_id=row.gmail_id,
            sender=row.sender or "",
            subject=row.subject or "",
            internal_date=row.internal_date or now,
            current_labels=tuple(names),
        )
        out.append((meta, names))
    return out


def user_labels(names: list[str]) -> list[str]:
    return [
        n
        for n in names
        if not n.startswith("CATEGORY_")
        and n not in STAR_LABELS | HOUSEKEEPING
        and n
        not in {"INBOX", "UNREAD", "SENT", "DRAFT", "SPAM", "TRASH", "STARRED", "IMPORTANT", "CHAT"}
    ]


def sender_domain(sender: str) -> str:
    return parseaddr(sender)[1].partition("@")[2].lower()


def matching_rules(rules: list[Rule], meta: MessageMeta, now: datetime) -> list[Rule]:
    return [r for r in rules if rule_matches(r, meta, now)]


def section_archived_actionable(messages, rules, now, added) -> None:
    print("\n=== archived-actionable: archived, but the subject asks for action ===\n")
    hits = []
    for meta, names in messages:
        if "INBOX" in names:
            continue
        matched = matching_rules(rules, meta, now)
        if not any(r.actions.archive for r in matched):
            continue
        lowered = meta.subject.lower()
        tokens = [t for t in ACTIONABLE_SUBJECT if t in lowered]
        if tokens:
            hits.append((meta, tokens, [r.id for r in matched if r.actions.archive]))
    print(f"{len(hits)} archived messages with actionable wording\n")
    for meta, tokens, rule_ids in hits[:40]:
        print(f"  {meta.sender[:38]:38s} {meta.subject[:56]:56s}")
        print(f"      tokens={tokens[:3]} via {rule_ids}")


def section_personal_vendor(messages, rules, now, added) -> None:
    print("\n=== personal-vendor: CATEGORY_PERSONAL mail given a vendor label ===\n")
    vendor_prefixes = ("Administratie/Aankopen", "Reizen", "Daily/Films", "AlbertHeijn")
    by_rule: dict[str, list[MessageMeta]] = defaultdict(list)
    for meta, names in messages:
        if "CATEGORY_PERSONAL" not in names:
            continue
        for rule in matching_rules(rules, meta, now):
            if any(lbl.startswith(vendor_prefixes) for lbl in rule.actions.add_labels):
                by_rule[rule.id].append(meta)
    total = sum(len(v) for v in by_rule.values())
    print(f"{total} messages across {len(by_rule)} rules\n")
    for rule_id, metas in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(metas):4d}  {rule_id}")
        for meta in metas[:3]:
            print(f"          {meta.sender[:36]:36s} {meta.subject[:52]}")


def applied_by_runs(session) -> dict[str, set[str]]:
    """message id -> labels a run added (not labels the user already had)."""
    added: dict[str, set[str]] = defaultdict(set)
    for gmail_id, before, after in session.execute(
        select(Mutation.message_gmail_id, Mutation.labels_before, Mutation.labels_after)
    ).all():
        added[gmail_id] |= set(json.loads(after)) - set(json.loads(before))
    return added


def section_cross_tree(messages, rules, now, added) -> None:
    print("\n=== cross-tree: a rule put a label in a tree the mail already sat in ===\n")
    groups: dict[tuple[str, ...], list[tuple[MessageMeta, list[str], list[str]]]] = defaultdict(
        list
    )
    for meta, names in messages:
        still_present = set(names)
        ours = [
            lbl
            for lbl in user_labels(sorted(added.get(meta.gmail_id, set())))
            if lbl in still_present
        ]
        if not ours:
            continue
        theirs = [lbl for lbl in user_labels(names) if lbl not in ours]
        if not theirs:
            continue
        our_trees = {lbl.split("/")[0] for lbl in ours}
        their_trees = {lbl.split("/")[0] for lbl in theirs}
        if our_trees & their_trees:
            continue
        key = tuple(sorted(our_trees)) + ("<-",) + tuple(sorted(their_trees))
        groups[key].append((meta, ours, theirs))
    total = sum(len(v) for v in groups.values())
    print(f"{total} messages where our tree differs from the existing one\n")
    for key, entries in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:22]:
        ours_txt, theirs_txt = " ".join(key).split(" <- ")
        print(f"  {len(entries):4d}  we added [{ours_txt}]  existing [{theirs_txt}]")
        for meta, ours, theirs in entries[:2]:
            print(f"          {meta.sender[:32]:32s} {meta.subject[:40]:40s} {ours} vs {theirs}")


def section_subject_rule_reach(messages, rules, now, added) -> None:
    print("\n=== subject-rule-reach: what the subject-only rules matched ===\n")
    subject_only = [r for r in rules if not r.match.from_domain and not r.match.has_label]
    for rule in subject_only:
        matched = [m for m, _ in messages if rule_matches(rule, m, now)]
        print(f"  {len(matched):5d}  {rule.id} -> {rule.actions.add_labels}")
        for meta in matched[:SAMPLE]:
            print(f"          {meta.sender[:34]:34s} {meta.subject[:52]}")


def section_per_rule_sample(messages, rules, now, added) -> None:
    print("\n=== per-rule-sample ===\n")
    for rule in rules:
        matched = [m for m, _ in messages if rule_matches(rule, m, now)]
        if not matched:
            continue
        flags = []
        if rule.actions.archive:
            flags.append("ARCHIVE")
        print(
            f"  {len(matched):5d}  {rule.id} -> {rule.actions.add_labels} {' '.join(flags)}"
        )
        for meta in matched[:3]:
            print(f"          {meta.sender[:34]:34s} {meta.subject[:52]}")


MACHINE_LOCALS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "automail", "auto-confirm",
    "auto-bevestiging", "notificatie", "notification", "notifications", "newsletter",
    "nieuwsbrief", "mailing", "info", "service", "support", "klantenservice", "customercare",
    "hello", "contact", "team", "news", "email", "mail", "bounce", "reply", "alert", "update",
    "bestellingen", "orders", "facturen", "billing", "account", "verzending", "feedback",
    "marketing", "webmaster", "postmaster", "helpdesk", "administratie", "no.reply",
)


def looks_human(sender: str) -> bool:
    local = parseaddr(sender)[1].partition("@")[0].lower()
    if not local or any(tok in local for tok in MACHINE_LOCALS):
        return False
    return bool(re.fullmatch(r"[a-z]+[._-][a-z][a-z._-]*", local)) or local.isalpha()


def section_human_domains(messages, rules, now, added) -> None:
    print("\n=== human-domains: vendor rules on domains where real people write ===\n")
    by_domain: dict[str, list[MessageMeta]] = defaultdict(list)
    for meta, _ in messages:
        by_domain[sender_domain(meta.sender)].append(meta)

    findings = []
    for rule in rules:
        for domain in rule.match.from_domain:
            metas = by_domain.get(domain.lower(), [])
            if len(metas) < 4:
                continue
            humans = [m for m in metas if looks_human(m.sender)]
            share = len(humans) / len(metas)
            if share >= 0.25:
                findings.append((share, domain, rule.id, rule.actions.add_labels, humans))
    findings.sort(reverse=True)
    print(f"{len(findings)} rule/domain pairs where >=25% of mail comes from a person\n")
    for share, domain, rule_id, labels, humans in findings:
        print(f"  {share:4.0%} human  {domain:26s} {rule_id} -> {labels}")
        for meta in humans[:3]:
            print(f"            {meta.sender[:36]:36s} {meta.subject[:50]}")


def section_archived_by_run(messages, rules, now, added) -> None:
    """Every message a run took out of the inbox, grouped by the rule that did it."""
    print("\n=== archived-by-run: everything mailbrain removed from the inbox ===\n")
    by_id = {meta.gmail_id: (meta, names) for meta, names in messages}
    with session_factory(db_path())() as session:
        rows = session.execute(
            select(Mutation.run_id, Mutation.message_gmail_id)
            .where(Mutation.archived_before == True, Mutation.archived_after == False)  # noqa: E712
        ).all()

    per_rule: dict[str, list[tuple[int, MessageMeta, bool]]] = defaultdict(list)
    for run_id, gmail_id in rows:
        entry = by_id.get(gmail_id)
        if entry is None:
            continue
        meta, names = entry
        back_in_inbox = "INBOX" in names
        archivers = [r.id for r in matching_rules(rules, meta, now) if r.actions.archive]
        key = ", ".join(archivers) if archivers else "(no rule archives this any more)"
        per_rule[key].append((run_id, meta, back_in_inbox))

    total = sum(len(v) for v in per_rule.values())
    print(f"{total} messages, {len(per_rule)} archiving rule combinations\n")
    for key, entries in sorted(per_rule.items(), key=lambda kv: -len(kv[1])):
        restored = sum(1 for _, _, back in entries if back)
        note = f"  ({restored} already back in the inbox)" if restored else ""
        print(f"\n{'=' * 78}\n{len(entries):5d}  via {key}{note}\n{'=' * 78}")
        by_domain: dict[str, list[MessageMeta]] = defaultdict(list)
        for _, meta, back in entries:
            if not back:
                by_domain[sender_domain(meta.sender)].append(meta)
        for domain, metas in sorted(by_domain.items(), key=lambda kv: -len(kv[1])):
            print(f"\n  {len(metas):4d}  {domain}")
            for meta in metas:
                print(f"          {meta.internal_date:%Y-%m-%d}  {meta.subject[:74]}")


SECTIONS = {
    "archived-by-run": section_archived_by_run,
    "human-domains": section_human_domains,
    "archived-actionable": section_archived_actionable,
    "personal-vendor": section_personal_vendor,
    "cross-tree": section_cross_tree,
    "subject-rule-reach": section_subject_rule_reach,
    "per-rule-sample": section_per_rule_sample,
}


def main() -> None:
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")] or list(SECTIONS)
    unknown = [w for w in wanted if w not in SECTIONS]
    if unknown:
        print(f"unknown section(s): {unknown}\navailable: {list(SECTIONS)}")
        raise SystemExit(2)

    rules = load_rules(Path("config/rules.yaml")).rules
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_factory(db_path())() as session:
        messages = load_messages(session)
        added = applied_by_runs(session)
    print(f"{len(messages)} cached messages, {len(rules)} rules")

    for name in wanted:
        SECTIONS[name](messages, rules, now, added)


if __name__ == "__main__":
    main()
