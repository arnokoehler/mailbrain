"""Pure rule-matching and classification logic (no I/O)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from email.utils import parseaddr

from mailbrain.config import Rule, RuleMatch
from mailbrain.rules.models import Classification, MessageMeta


def _sender_domain(sender: str) -> str:
    _, address = parseaddr(sender)
    _, _, domain = address.partition("@")
    return domain.lower()


def _domain_matches(msg_domain: str, rule_domain: str) -> bool:
    rule_domain = rule_domain.lower()
    return msg_domain == rule_domain or msg_domain.endswith("." + rule_domain)


def _has_any_criterion(m: RuleMatch) -> bool:
    return bool(
        m.from_domain
        or m.subject_contains
        or m.subject_regex
        or m.older_than_days is not None
    )


def rule_matches(rule: Rule, msg: MessageMeta, now: datetime) -> bool:
    """True if the message satisfies ALL present criteria of the rule.

    A rule with no criteria never matches (a criterion-less rule that matched
    everything would be a footgun).

    Note: subject_contains is case-insensitive; subject_regex is matched with
    re.search and honours the pattern's own flags (use ``(?i)`` for
    case-insensitive regex). Domain matching is case-insensitive.
    """
    m = rule.match
    if not _has_any_criterion(m):
        return False

    if m.from_domain:
        domain = _sender_domain(msg.sender)
        if not any(_domain_matches(domain, d) for d in m.from_domain):
            return False

    if m.subject_contains:
        subject_lower = msg.subject.lower()
        if not any(s.lower() in subject_lower for s in m.subject_contains):
            return False

    if m.subject_regex and not any(re.search(pat, msg.subject) for pat in m.subject_regex):
        return False

    if m.older_than_days is not None:
        age_days = (now - msg.internal_date).days
        if age_days < m.older_than_days:
            return False

    return True


def classify(
    messages: Iterable[MessageMeta], rules: list[Rule], now: datetime
) -> list[Classification]:
    """Classify each message against all rules.

    Labels from every matching rule are unioned (deduped, sorted). Boolean
    actions are OR-combined (any matching rule requesting the action wins).
    Messages that match no rule are omitted from the result.
    """
    results: list[Classification] = []
    for msg in messages:
        matched = [r for r in rules if rule_matches(r, msg, now)]
        if not matched:
            continue
        labels: set[str] = set()
        archive = False
        mark_read = False
        for r in matched:
            labels.update(r.actions.add_labels)
            archive = archive or r.actions.archive
            mark_read = mark_read or r.actions.mark_read
        results.append(
            Classification(
                gmail_id=msg.gmail_id,
                matched_rule_ids=tuple(r.id for r in matched),
                add_labels=tuple(sorted(labels)),
                archive=archive,
                mark_read=mark_read,
            )
        )
    return results
