# MailBrain Phase 1b — Scan, Classify & Dry-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-side pipeline — fetch Gmail metadata, classify with the rules engine, compute the desired-state diff, and print a dry-run report of what *would* change — with zero mailbox mutations.

**Architecture:** Pure logic (rule matching, classification, desired-state diff) lives in `rules/` and `planner.py` and is exhaustively unit-tested with no I/O. Gmail I/O is isolated behind a thin `GmailClient` wrapper (dependency-injected so scan logic is testable with a fake client). A backoff helper wraps transient Gmail errors. The scanner caches message metadata + the label id↔name map into SQLite; `classify` runs entirely offline on that cache, so the whole classify→report path is testable without live credentials.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, google-api-python-client, Typer, Rich, pytest. Builds on Phase 1a (`paths`, `config`, `storage`, `gmail.auth`, `cli`).

---

## Scope

Covers v0.1 milestones 5–7 (Gmail scanner, rule engine, dry-run reporting). **No mutations** — apply (milestone 8) and rollback (milestone 9) are Plan 1c. AI is Phase 2.

**Design decisions locked for this plan:**
- **Boolean actions (`archive`, `mark_read`) are OR-unioned** across matching rules. Because the rule schema has no negative/"keep" assertion, genuine conflicts cannot arise, so conflict detection and `review_queue` population are deferred (not dead-coded now). `review_queue` stays empty until a negative action or AI (Phase 2) exists.
- **`add_labels` are unioned** across matching rules (a message may get several labels).
- **Idempotency via desired-state diff:** the planner emits a mutation only for the delta vs the message's current Gmail state. A message already in its desired state produces no planned mutation.
- **Full-list scan** using `users.messages.list` + batched metadata `get`. Incremental `historyId` sync is deferred (a later optimization; `sync_state` table already exists).
- Carry-over from 1a: `subject_regex` patterns are validated at config load (added here, Task 4).

## Notes carried to Plan 1c (from Phase 1b review)

- **Label name→ID resolution + creation belongs in `apply`.** `PlannedMutation.add_labels` carries label *names* (from rules YAML). Gmail's `messages.modify` needs label *IDs*. Per design v0.2, 1c's `apply` must resolve names→IDs via the `Label` cache and **auto-create missing (nested) labels first**, updating the cache. This is intended, not rework — but apply must not assume every rule label already has a `gmail_id`.
- **`Label.name` is no longer unique** (dropped in 1b for rename-safety). The name→ID reverse lookup in apply must handle the (rare) duplicate-name case deterministically.
- **Missing `internalDate` → epoch 1970.** `GmailClient.get_metadata` defaults absent `internalDate` to `"0"` → 1970, which spuriously satisfies `older_than_days`. Real `in:inbox` messages always have it, so this only affects edge cases (e.g. drafts). 1c/scan hardening: treat missing as a skip or warn rather than epoch.
- **Label name matching is case-sensitive** in the planner (`label not in current`). If rules YAML casing differs from the Gmail label's actual case, the planner re-emits the add every run. Document that rule label names must match Gmail casing, or normalise.
- **Stale label ID leak:** if a Gmail label is deleted/renamed between `list_labels` and use, `load_cached` falls back to the raw ID as the "name". Harmless in the dry-run path; apply should tolerate it.

## File Structure

- `src/mailbrain/gmail/backoff.py` — `with_backoff(fn, ...)` retry helper for transient API errors. Pure, injectable `sleep`.
- `src/mailbrain/gmail/client.py` — `GmailClient` wrapping the Gmail service: `list_message_ids`, `get_metadata`, `list_labels`. Thin; mocked in tests.
- `src/mailbrain/rules/__init__.py`
- `src/mailbrain/rules/models.py` — frozen dataclasses: `MessageMeta`, `Classification`.
- `src/mailbrain/rules/engine.py` — pure functions: `rule_matches`, `classify`. No I/O.
- `src/mailbrain/planner.py` — `PlannedMutation` dataclass + `plan_mutations` (desired-state diff). Pure.
- `src/mailbrain/scan.py` — `scan_mailbox(client, settings, session_factory)` fetch + cache; `load_cached(session_factory)` read-back helpers.
- `src/mailbrain/report.py` — `render_plan(plans, messages)` Rich table + `Metrics` summary.
- CLI additions in `src/mailbrain/cli.py`: `scan` (live fetch → cache) and `classify` (offline cache → plan → report).
- Tests mirror each module under `tests/`.

Each file has one responsibility; pure logic is separated from Gmail I/O so the bulk of the pipeline is testable offline.

---

### Task 1: Backoff helper

**Files:**
- Create: `src/mailbrain/gmail/backoff.py`
- Test: `tests/gmail/test_backoff.py`

- [ ] **Step 1: Write the failing test**

`tests/gmail/test_backoff.py`:
```python
import pytest

from mailbrain.gmail import backoff


def test_returns_value_without_retry():
    calls = []
    result = backoff.with_backoff(lambda: 42, sleep=lambda d: calls.append(d))
    assert result == 42
    assert calls == []


def test_retries_then_succeeds():
    attempts = {"n": 0}
    slept: list[float] = []

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("transient")
        return "ok"

    result = backoff.with_backoff(
        flaky,
        base_delay=1.0,
        sleep=slept.append,
        is_retryable=lambda exc: isinstance(exc, ValueError),
    )
    assert result == "ok"
    assert attempts["n"] == 3
    assert slept == [1.0, 2.0]  # exponential: 1*2^0, 1*2^1


def test_non_retryable_raises_immediately():
    attempts = {"n": 0}

    def boom():
        attempts["n"] += 1
        raise KeyError("fatal")

    with pytest.raises(KeyError):
        backoff.with_backoff(
            boom, sleep=lambda d: None, is_retryable=lambda exc: False
        )
    assert attempts["n"] == 1


def test_gives_up_after_max_attempts():
    attempts = {"n": 0}

    def always_fail():
        attempts["n"] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError):
        backoff.with_backoff(
            always_fail,
            max_attempts=4,
            sleep=lambda d: None,
            is_retryable=lambda exc: True,
        )
    assert attempts["n"] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gmail/test_backoff.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.gmail.backoff'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/gmail/backoff.py`:
```python
"""Retry-with-exponential-backoff for transient Gmail API errors."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Gmail returns these for rate limiting / transient server issues.
RETRYABLE_STATUS = {403, 429, 500, 503}


def _status_of(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    return status if isinstance(status, int) else None


def default_is_retryable(exc: Exception) -> bool:
    return _status_of(exc) in RETRYABLE_STATUS


def with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    is_retryable: Callable[[Exception], bool] = default_is_retryable,
) -> T:
    """Call `fn`, retrying on retryable exceptions with exponential backoff."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised unless retryable
            attempt += 1
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            sleep(base_delay * 2 ** (attempt - 1))
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/gmail/test_backoff.py -v && uv run ruff check src/mailbrain/gmail/backoff.py && uv run mypy`
Expected: 4 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/gmail/backoff.py tests/gmail/test_backoff.py
git commit -m "feat: exponential backoff helper for transient Gmail errors"
```

---

### Task 2: Rules domain models

**Files:**
- Create: `src/mailbrain/rules/__init__.py` (empty), `src/mailbrain/rules/models.py`
- Test: `tests/rules/__init__.py` (empty), `tests/rules/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/rules/__init__.py`: (empty)

`tests/rules/test_models.py`:
```python
from datetime import UTC, datetime

from mailbrain.rules.models import Classification, MessageMeta


def test_message_meta_is_frozen():
    m = MessageMeta(
        gmail_id="m1",
        sender="Booking <no-reply@booking.com>",
        subject="Receipt",
        internal_date=datetime(2026, 7, 1, tzinfo=UTC),
        current_labels=("INBOX", "UNREAD"),
    )
    assert m.gmail_id == "m1"
    assert m.current_labels == ("INBOX", "UNREAD")


def test_classification_defaults():
    c = Classification(gmail_id="m1", matched_rule_ids=("booking-payment",), add_labels=("Reizen",))
    assert c.archive is False
    assert c.mark_read is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.rules'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/rules/__init__.py`: (empty)

`src/mailbrain/rules/models.py`:
```python
"""Immutable domain objects for the rules engine (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class MessageMeta:
    """Metadata for one message, as the engine sees it."""

    gmail_id: str
    sender: str
    subject: str
    internal_date: datetime
    current_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classification:
    """The desired outcome for one message after rule matching."""

    gmail_id: str
    matched_rule_ids: tuple[str, ...]
    add_labels: tuple[str, ...]
    archive: bool = False
    mark_read: bool = False
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/rules/test_models.py -v && uv run ruff check src/mailbrain/rules/models.py && uv run mypy`
Expected: 2 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/rules/__init__.py src/mailbrain/rules/models.py tests/rules/__init__.py tests/rules/test_models.py
git commit -m "feat: rules engine domain models (MessageMeta, Classification)"
```

---

### Task 3: Single-rule matching

**Files:**
- Create: `src/mailbrain/rules/engine.py`
- Test: `tests/rules/test_matching.py`

- [ ] **Step 1: Write the failing test**

`tests/rules/test_matching.py`:
```python
from datetime import UTC, datetime, timedelta

from mailbrain.config import Rule, RuleActions, RuleMatch
from mailbrain.rules.engine import rule_matches
from mailbrain.rules.models import MessageMeta

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _msg(sender="x@booking.com", subject="hello", days_old=0):
    return MessageMeta(
        gmail_id="m1",
        sender=sender,
        subject=subject,
        internal_date=NOW - timedelta(days=days_old),
    )


def _rule(**match_kwargs):
    return Rule(
        id="r",
        match=RuleMatch(**match_kwargs),
        actions=RuleActions(add_labels=["L"]),
    )


def test_from_domain_exact_match():
    assert rule_matches(_rule(from_domain=["booking.com"]), _msg(sender="a@booking.com"), NOW)


def test_from_domain_with_display_name():
    assert rule_matches(
        _rule(from_domain=["booking.com"]),
        _msg(sender="Booking.com <no-reply@booking.com>"),
        NOW,
    )


def test_from_domain_subdomain_matches():
    assert rule_matches(
        _rule(from_domain=["booking.com"]), _msg(sender="x@mail.booking.com"), NOW
    )


def test_from_domain_no_match():
    assert not rule_matches(_rule(from_domain=["booking.com"]), _msg(sender="x@other.com"), NOW)


def test_subject_contains_case_insensitive():
    assert rule_matches(_rule(subject_contains=["receipt"]), _msg(subject="Your RECEIPT"), NOW)


def test_subject_regex():
    assert rule_matches(
        _rule(subject_regex=["(?i)password reset"]), _msg(subject="Password Reset Requested"), NOW
    )


def test_older_than_days_true():
    assert rule_matches(_rule(older_than_days=3), _msg(days_old=5), NOW)


def test_older_than_days_false():
    assert not rule_matches(_rule(older_than_days=3), _msg(days_old=1), NOW)


def test_empty_match_never_matches():
    # A rule with no criteria must not match everything (safety).
    assert not rule_matches(_rule(), _msg(), NOW)


def test_all_present_criteria_must_hold():
    # from_domain matches but subject_contains does not -> overall no match (AND).
    rule = _rule(from_domain=["booking.com"], subject_contains=["invoice"])
    assert not rule_matches(rule, _msg(sender="a@booking.com", subject="receipt"), NOW)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/test_matching.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.rules.engine'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/rules/engine.py`:
```python
"""Pure rule-matching and classification logic (no I/O)."""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parseaddr

from mailbrain.config import Rule, RuleMatch
from mailbrain.rules.models import MessageMeta


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

    if m.subject_regex:
        if not any(re.search(pat, msg.subject) for pat in m.subject_regex):
            return False

    if m.older_than_days is not None:
        age_days = (now - msg.internal_date).days
        if age_days < m.older_than_days:
            return False

    return True
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/rules/test_matching.py -v && uv run ruff check src/mailbrain/rules/engine.py && uv run mypy`
Expected: 10 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/rules/engine.py tests/rules/test_matching.py
git commit -m "feat: single-rule matching (domain/subject/regex/age, AND semantics)"
```

---

### Task 4: Validate regex patterns at config load

**Files:**
- Modify: `src/mailbrain/config.py`
- Test: `tests/test_config_regex.py`

This closes a 1a carry-over: a bad `subject_regex` should fail at load, not mid-classify.

- [ ] **Step 1: Write the failing test**

`tests/test_config_regex.py`:
```python
import pytest
from pydantic import ValidationError

from mailbrain import config


def test_invalid_regex_rejected(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: bad\n"
        "    match:\n"
        "      subject_regex: ['[unterminated']\n"
        "    actions:\n"
        "      add_labels: [X]\n"
    )
    with pytest.raises(ValidationError):
        config.load_rules(p)


def test_valid_regex_accepted(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: good\n"
        "    match:\n"
        "      subject_regex: ['(?i)password reset']\n"
        "    actions:\n"
        "      add_labels: [X]\n"
    )
    rf = config.load_rules(p)
    assert rf.rules[0].match.subject_regex == ["(?i)password reset"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_regex.py -v`
Expected: FAIL — `test_invalid_regex_rejected` fails because no validator exists yet (bad regex is accepted).

- [ ] **Step 3: Add the validator to `RuleMatch` in `src/mailbrain/config.py`**

Add `import re` at the top of the file (after `from pathlib import Path`), add `field_validator` to the pydantic import line so it reads `from pydantic import BaseModel, ConfigDict, Field, field_validator`, and add this method inside the `RuleMatch` class (after its fields):
```python
    @field_validator("subject_regex")
    @classmethod
    def _validate_regex(cls, patterns: list[str]) -> list[str]:
        for pat in patterns:
            try:
                re.compile(pat)
            except re.error as exc:
                raise ValueError(f"invalid subject_regex {pat!r}: {exc}") from exc
        return patterns
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_config_regex.py tests/test_config.py tests/test_default_config.py -v && uv run ruff check src/mailbrain/config.py && uv run mypy`
Expected: all PASS (new 2 + existing config tests still green); ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/config.py tests/test_config_regex.py
git commit -m "feat: validate subject_regex patterns at config load"
```

---

### Task 5: Classify across multiple rules

**Files:**
- Modify: `src/mailbrain/rules/engine.py`
- Test: `tests/rules/test_classify.py`

- [ ] **Step 1: Write the failing test**

`tests/rules/test_classify.py`:
```python
from datetime import UTC, datetime

from mailbrain.config import Rule, RuleActions, RuleMatch
from mailbrain.rules.engine import classify
from mailbrain.rules.models import MessageMeta

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _rule(rid, labels, archive=False, mark_read=False, **match):
    return Rule(
        id=rid,
        match=RuleMatch(**match),
        actions=RuleActions(add_labels=labels, archive=archive, mark_read=mark_read),
    )


def _msg(gmail_id, sender="x@booking.com", subject="hi"):
    return MessageMeta(gmail_id=gmail_id, sender=sender, subject=subject, internal_date=NOW)


def test_unmatched_message_is_skipped():
    rules = [_rule("r", ["L"], from_domain=["nope.com"])]
    assert classify([_msg("m1")], rules, NOW) == []


def test_single_rule_classification():
    rules = [_rule("booking", ["Reizen"], archive=True, from_domain=["booking.com"])]
    result = classify([_msg("m1")], rules, NOW)
    assert len(result) == 1
    c = result[0]
    assert c.gmail_id == "m1"
    assert c.matched_rule_ids == ("booking",)
    assert c.add_labels == ("Reizen",)
    assert c.archive is True
    assert c.mark_read is False


def test_multiple_rules_union_labels_and_or_booleans():
    rules = [
        _rule("a", ["Reizen"], archive=False, from_domain=["booking.com"]),
        _rule("b", ["Administratie"], archive=True, subject_contains=["hi"]),
    ]
    result = classify([_msg("m1")], rules, NOW)
    assert len(result) == 1
    c = result[0]
    assert c.matched_rule_ids == ("a", "b")
    assert c.add_labels == ("Administratie", "Reizen")  # sorted, deduped union
    assert c.archive is True  # OR: any True wins


def test_duplicate_labels_deduped():
    rules = [
        _rule("a", ["Reizen"], from_domain=["booking.com"]),
        _rule("b", ["Reizen"], subject_contains=["hi"]),
    ]
    c = classify([_msg("m1")], rules, NOW)[0]
    assert c.add_labels == ("Reizen",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/rules/test_classify.py -v`
Expected: FAIL with `ImportError: cannot import name 'classify'`

- [ ] **Step 3: Add `classify` to `src/mailbrain/rules/engine.py`**

Add these imports at the top (extend the existing import block): `from collections.abc import Iterable` and `from mailbrain.config import Rule, RuleMatch` already present — also add `from mailbrain.rules.models import Classification, MessageMeta` (extend existing). Then append:
```python
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
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/rules/ -v && uv run ruff check src/mailbrain/rules/engine.py && uv run mypy`
Expected: all rules tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/rules/engine.py tests/rules/test_classify.py
git commit -m "feat: classify messages across rules (union labels, OR booleans)"
```

---

### Task 6: Desired-state diff planner

**Files:**
- Create: `src/mailbrain/planner.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write the failing test**

`tests/test_planner.py`:
```python
from mailbrain.planner import PlannedMutation, plan_mutations
from mailbrain.rules.models import Classification


def _c(gmail_id="m1", add_labels=("Reizen",), archive=False, mark_read=False):
    return Classification(
        gmail_id=gmail_id,
        matched_rule_ids=("r",),
        add_labels=add_labels,
        archive=archive,
        mark_read=mark_read,
    )


def test_adds_only_missing_labels():
    current = {"m1": {"Reizen", "INBOX"}}
    plans = plan_mutations([_c(add_labels=("Reizen", "Administratie"))], current)
    assert len(plans) == 1
    assert plans[0].add_labels == ("Administratie",)  # Reizen already present


def test_archive_only_when_in_inbox():
    current = {"m1": {"INBOX"}}
    plans = plan_mutations([_c(add_labels=(), archive=True)], current)
    assert plans[0].archive is True

    current2 = {"m1": set()}  # already archived
    plans2 = plan_mutations([_c(add_labels=(), archive=True)], current2)
    assert plans2 == []  # nothing to do


def test_mark_read_only_when_unread():
    current = {"m1": {"UNREAD"}}
    plans = plan_mutations([_c(add_labels=(), mark_read=True)], current)
    assert plans[0].mark_read is True

    current2 = {"m1": set()}
    plans2 = plan_mutations([_c(add_labels=(), mark_read=True)], current2)
    assert plans2 == []


def test_noop_when_already_in_desired_state():
    current = {"m1": {"Reizen"}}
    plans = plan_mutations([_c(add_labels=("Reizen",))], current)
    assert plans == []


def test_missing_current_state_treats_as_empty():
    # message not in the current map -> treat as having no labels
    plans = plan_mutations([_c(add_labels=("Reizen",))], {})
    assert plans[0].add_labels == ("Reizen",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.planner'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/planner.py`:
```python
"""Desired-state diff: turn classifications into the minimal set of mutations.

A mutation is emitted only for the delta between the desired state and the
message's current Gmail state, so re-running with no changes is a no-op
(idempotency by construction). System labels INBOX/UNREAD model archive/read:
archiving removes INBOX, marking-read removes UNREAD.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mailbrain.rules.models import Classification


@dataclass(frozen=True)
class PlannedMutation:
    gmail_id: str
    add_labels: tuple[str, ...]
    archive: bool
    mark_read: bool

    def is_noop(self) -> bool:
        return not self.add_labels and not self.archive and not self.mark_read


def plan_mutations(
    classifications: list[Classification],
    current_labels: Mapping[str, set[str]],
) -> list[PlannedMutation]:
    plans: list[PlannedMutation] = []
    for c in classifications:
        current = current_labels.get(c.gmail_id, set())
        to_add = tuple(sorted(label for label in c.add_labels if label not in current))
        archive = c.archive and "INBOX" in current
        mark_read = c.mark_read and "UNREAD" in current
        mutation = PlannedMutation(
            gmail_id=c.gmail_id,
            add_labels=to_add,
            archive=archive,
            mark_read=mark_read,
        )
        if not mutation.is_noop():
            plans.append(mutation)
    return plans
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_planner.py -v && uv run ruff check src/mailbrain/planner.py && uv run mypy`
Expected: 5 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/planner.py tests/test_planner.py
git commit -m "feat: desired-state diff planner (idempotent mutation deltas)"
```

---

### Task 7: GmailClient wrapper

**Files:**
- Create: `src/mailbrain/gmail/client.py`
- Test: `tests/gmail/test_client.py`

The client is a thin translation layer over the Gmail service dict-API. Tests use a fake service object (MagicMock) — no live network.

- [ ] **Step 1: Write the failing test**

`tests/gmail/test_client.py`:
```python
from unittest.mock import MagicMock

from mailbrain.gmail.client import GmailClient


def _service_with_pages(pages):
    """Build a fake Gmail service whose users().messages().list_next paginates."""
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    list_calls = [MagicMock(execute=MagicMock(return_value=p)) for p in pages]
    messages_api.list.side_effect = list_calls
    # list_next returns a request for pages after the first, then None
    messages_api.list_next.side_effect = list_calls[1:] + [None]
    return service, messages_api


def test_list_message_ids_paginates():
    pages = [
        {"messages": [{"id": "a"}, {"id": "b"}]},
        {"messages": [{"id": "c"}]},
    ]
    service, _ = _service_with_pages(pages)
    client = GmailClient(service)
    assert client.list_message_ids("in:inbox") == ["a", "b", "c"]


def test_list_message_ids_empty():
    service, _ = _service_with_pages([{}])
    client = GmailClient(service)
    assert client.list_message_ids("in:inbox") == []


def test_get_metadata_extracts_headers():
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    messages_api.get.return_value.execute.return_value = {
        "id": "m1",
        "threadId": "t1",
        "snippet": "hello there",
        "internalDate": "1751328000000",  # ms epoch
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Booking <no-reply@booking.com>"},
                {"name": "Subject", "value": "Your receipt"},
            ]
        },
    }
    client = GmailClient(service)
    meta = client.get_metadata("m1")
    assert meta["sender"] == "Booking <no-reply@booking.com>"
    assert meta["subject"] == "Your receipt"
    assert meta["label_ids"] == ["INBOX", "UNREAD"]
    assert meta["thread_id"] == "t1"
    assert meta["snippet"] == "hello there"
    assert meta["internal_date_ms"] == 1751328000000


def test_get_metadata_missing_headers_default_empty():
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    messages_api.get.return_value.execute.return_value = {
        "id": "m2",
        "internalDate": "0",
        "labelIds": [],
        "payload": {"headers": []},
    }
    client = GmailClient(service)
    meta = client.get_metadata("m2")
    assert meta["sender"] == ""
    assert meta["subject"] == ""


def test_list_labels_maps_id_to_name():
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": [
            {"id": "Label_1", "name": "Reizen"},
            {"id": "INBOX", "name": "INBOX"},
        ]
    }
    client = GmailClient(service)
    assert client.list_labels() == {"Label_1": "Reizen", "INBOX": "INBOX"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gmail/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.gmail.client'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/gmail/client.py`:
```python
"""Thin wrapper over the Gmail REST service (metadata reads only)."""

from __future__ import annotations

from typing import Any

USER_ID = "me"
_METADATA_HEADERS = ["From", "Subject"]


class GmailClient:
    """Read-only Gmail access used by the scanner. I/O boundary — mock in tests."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_message_ids(self, query: str) -> list[str]:
        api = self._service.users().messages()
        ids: list[str] = []
        request = api.list(userId=USER_ID, q=query)
        while request is not None:
            response = request.execute()
            ids.extend(m["id"] for m in response.get("messages", []))
            request = api.list_next(request, response)
        return ids

    def get_metadata(self, message_id: str) -> dict[str, Any]:
        api = self._service.users().messages()
        msg = api.get(
            userId=USER_ID,
            id=message_id,
            format="metadata",
            metadataHeaders=_METADATA_HEADERS,
        ).execute()
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        return {
            "gmail_id": msg["id"],
            "thread_id": msg.get("threadId"),
            "snippet": msg.get("snippet"),
            "sender": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "label_ids": msg.get("labelIds", []),
            "internal_date_ms": int(msg.get("internalDate", "0")),
        }

    def list_labels(self) -> dict[str, str]:
        response = self._service.users().labels().list(userId=USER_ID).execute()
        return {lbl["id"]: lbl["name"] for lbl in response.get("labels", [])}
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/gmail/test_client.py -v && uv run ruff check src/mailbrain/gmail/client.py && uv run mypy`
Expected: 5 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/gmail/client.py tests/gmail/test_client.py
git commit -m "feat: read-only GmailClient wrapper (list ids, metadata, labels)"
```

---

### Task 8: Scanner (fetch + cache)

**Files:**
- Create: `src/mailbrain/scan.py`
- Test: `tests/test_scan.py`

The scanner is dependency-injected with a `GmailClient` (or any object exposing the same three methods), so it is fully testable with a fake.

- [ ] **Step 1: Write the failing test**

`tests/test_scan.py`:
```python
from unittest.mock import MagicMock

from sqlalchemy import select

from mailbrain.scan import load_cached, scan_mailbox
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message


def _fake_client():
    client = MagicMock()
    client.list_message_ids.return_value = ["m1", "m2"]
    client.list_labels.return_value = {"INBOX": "INBOX", "Label_1": "Reizen"}
    client.get_metadata.side_effect = [
        {
            "gmail_id": "m1",
            "thread_id": "t1",
            "snippet": "hi",
            "sender": "a@booking.com",
            "subject": "Receipt",
            "label_ids": ["INBOX", "Label_1"],
            "internal_date_ms": 1751328000000,
        },
        {
            "gmail_id": "m2",
            "thread_id": "t2",
            "snippet": "yo",
            "sender": "b@degiro.nl",
            "subject": "Statement",
            "label_ids": ["INBOX"],
            "internal_date_ms": 1751328000000,
        },
    ]
    return client


def test_scan_caches_messages_and_labels(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)

    count = scan_mailbox(_fake_client(), "in:inbox", factory)
    assert count == 2

    with factory() as s:
        assert s.scalar(select(Message).where(Message.gmail_id == "m1")) is not None
        assert s.scalar(select(Label).where(Label.name == "Reizen")) is not None


def test_scan_is_idempotent_upsert(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    scan_mailbox(_fake_client(), "in:inbox", factory)
    scan_mailbox(_fake_client(), "in:inbox", factory)  # second scan, same ids
    with factory() as s:
        rows = s.scalars(select(Message)).all()
        assert len(rows) == 2  # no duplicates


def test_load_cached_returns_meta_and_current_labels(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    scan_mailbox(_fake_client(), "in:inbox", factory)

    messages, current = load_cached(factory)
    by_id = {m.gmail_id: m for m in messages}
    assert by_id["m1"].sender == "a@booking.com"
    # current labels resolved to NAMES via the labels cache
    assert current["m1"] == {"INBOX", "Reizen"}
    assert current["m2"] == {"INBOX"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.scan'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/scan.py`:
```python
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
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_scan.py -v && uv run ruff check src/mailbrain/scan.py && uv run mypy`
Expected: 3 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/scan.py tests/test_scan.py
git commit -m "feat: Gmail scanner caches messages + labels to SQLite"
```

---

### Task 9: Dry-run report

**Files:**
- Create: `src/mailbrain/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing test**

`tests/test_report.py`:
```python
from mailbrain.planner import PlannedMutation
from mailbrain.report import Metrics, summarize


def test_summarize_counts():
    plans = [
        PlannedMutation(gmail_id="m1", add_labels=("Reizen",), archive=True, mark_read=False),
        PlannedMutation(gmail_id="m2", add_labels=("Beleggen",), archive=False, mark_read=True),
        PlannedMutation(gmail_id="m3", add_labels=(), archive=True, mark_read=False),
    ]
    metrics = summarize(plans, scanned=10)
    assert metrics == Metrics(scanned=10, planned=3, labeled=2, archived=2, marked_read=1)


def test_summarize_empty():
    assert summarize([], scanned=5) == Metrics(
        scanned=5, planned=0, labeled=0, archived=0, marked_read=0
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.report'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/report.py`:
```python
"""Dry-run reporting: metrics summary + Rich table rendering."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from mailbrain.planner import PlannedMutation
from mailbrain.rules.models import MessageMeta


@dataclass(frozen=True)
class Metrics:
    scanned: int
    planned: int
    labeled: int
    archived: int
    marked_read: int


def summarize(plans: list[PlannedMutation], scanned: int) -> Metrics:
    return Metrics(
        scanned=scanned,
        planned=len(plans),
        labeled=sum(1 for p in plans if p.add_labels),
        archived=sum(1 for p in plans if p.archive),
        marked_read=sum(1 for p in plans if p.mark_read),
    )


def render_plan(
    plans: list[PlannedMutation],
    messages: list[MessageMeta],
    console: Console | None = None,
) -> None:
    console = console or Console()
    subjects = {m.gmail_id: m.subject for m in messages}
    table = Table(title="MailBrain — planned changes (dry-run)")
    table.add_column("Subject", overflow="ellipsis", max_width=48)
    table.add_column("Add labels")
    table.add_column("Archive")
    table.add_column("Read")
    for p in plans:
        table.add_row(
            subjects.get(p.gmail_id, p.gmail_id),
            ", ".join(p.add_labels),
            "yes" if p.archive else "",
            "yes" if p.mark_read else "",
        )
    console.print(table)


def render_metrics(metrics: Metrics, console: Console | None = None) -> None:
    console = console or Console()
    console.print(
        f"[bold]scanned[/] {metrics.scanned}  "
        f"[bold]planned[/] {metrics.planned}  "
        f"[bold]labeled[/] {metrics.labeled}  "
        f"[bold]archived[/] {metrics.archived}  "
        f"[bold]read[/] {metrics.marked_read}"
    )
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_report.py -v && uv run ruff check src/mailbrain/report.py && uv run mypy`
Expected: 2 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/report.py tests/test_report.py
git commit -m "feat: dry-run metrics summary and Rich plan table"
```

---

### Task 10: CLI `classify` command (offline)

**Files:**
- Modify: `src/mailbrain/cli.py`
- Test: `tests/test_cli_classify.py`

`classify` runs entirely on cached data (no Gmail), so it is end-to-end testable. It loads rules, classifies, plans, and renders.

- [ ] **Step 1: Write the failing test**

`tests/test_cli_classify.py`:
```python
import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from mailbrain.cli import app
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message

runner = CliRunner()


def _seed(dbp):
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(
            Message(
                gmail_id="m1",
                sender="a@booking.com",
                subject="Your receipt",
                snippet="x",
                label_ids=json.dumps(["INBOX"]),
                internal_date=datetime(2026, 7, 1, tzinfo=UTC),
            )
        )
        s.commit()


def test_classify_reports_planned_changes(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home / "state.db")

    # point classify at the repo's built-in rules
    result = runner.invoke(app, ["classify", "--rules", "config/rules.yaml"])
    assert result.exit_code == 0, result.output
    # booking-payment rule -> Reizen label + archive
    assert "Reizen" in result.output


def test_classify_empty_db_is_clean(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    result = runner.invoke(app, ["classify", "--rules", "config/rules.yaml"])
    assert result.exit_code == 0, result.output
    assert "planned 0" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_classify.py -v`
Expected: FAIL — no `classify` command (Typer exits non-zero / "No such command").

- [ ] **Step 3: Add the command to `src/mailbrain/cli.py`**

Add these imports to the existing import block:
```python
from datetime import UTC, datetime
from pathlib import Path

from mailbrain import config
from mailbrain.planner import plan_mutations
from mailbrain.report import render_metrics, render_plan, summarize
from mailbrain.rules.engine import classify as classify_messages
from mailbrain.scan import load_cached
from mailbrain.storage.db import session_factory
```
Then append this command (after `init`):
```python
@app.command()
def classify(
    rules: Path = typer.Option(  # noqa: B008 - Typer option factory
        Path("config/rules.yaml"), "--rules", help="Path to rules YAML."
    ),
) -> None:
    """Classify cached mail and print a dry-run report (no changes applied)."""
    rules_file = config.load_rules(rules)
    factory = session_factory(paths.db_path())
    messages, current = load_cached(factory)
    classifications = classify_messages(messages, rules_file.rules, datetime.now(UTC))
    plans = plan_mutations(classifications, current)
    metrics = summarize(plans, scanned=len(messages))
    render_plan(plans, messages, console=console)
    render_metrics(metrics, console=console)
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_cli_classify.py -v && uv run ruff check src/mailbrain/cli.py && uv run mypy`
Expected: 2 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/cli.py tests/test_cli_classify.py
git commit -m "feat: mailbrain classify command (offline dry-run report)"
```

---

### Task 11: CLI `scan` command (live wiring)

**Files:**
- Modify: `src/mailbrain/cli.py`
- Test: `tests/test_cli_scan.py`

`scan` wires real auth + client, so it cannot run end-to-end without credentials. The test verifies the wiring by monkeypatching auth + client construction, confirming `scan_mailbox` is invoked with the configured query. This proves the command's plumbing without a live network.

- [ ] **Step 1: Write the failing test**

`tests/test_cli_scan.py`:
```python
from unittest.mock import MagicMock

from typer.testing import CliRunner

import mailbrain.cli as cli
from mailbrain.cli import app
from mailbrain.storage import db

runner = CliRunner()


def test_scan_wires_auth_client_and_scanner(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")

    monkeypatch.setattr(cli, "load_credentials", lambda c, t: MagicMock())
    monkeypatch.setattr(cli, "build_service", lambda creds: MagicMock())

    captured: dict[str, object] = {}

    def fake_scan(client, query, factory):
        captured["query"] = query
        return 7

    monkeypatch.setattr(cli, "scan_mailbox", fake_scan)

    result = runner.invoke(app, ["scan", "--query", "in:inbox"])
    assert result.exit_code == 0, result.output
    assert captured["query"] == "in:inbox"
    assert "7" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_scan.py -v`
Expected: FAIL — no `scan` command yet.

- [ ] **Step 3: Add the command + imports to `src/mailbrain/cli.py`**

Extend the import block with:
```python
from mailbrain.gmail.auth import build_service, load_credentials
from mailbrain.gmail.client import GmailClient
from mailbrain.scan import scan_mailbox
```
Append this command:
```python
@app.command()
def scan(
    query: str = typer.Option("in:inbox", "--query", help="Gmail search query."),
) -> None:
    """Fetch Gmail metadata for the query and cache it locally."""
    creds = load_credentials(paths.credentials_path(), paths.token_path())
    service = build_service(creds)
    client = GmailClient(service)
    count = scan_mailbox(client, query, session_factory(paths.db_path()))
    console.print(f"[green]Scanned and cached[/] {count} messages")
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_cli_scan.py -v && uv run ruff check src/mailbrain/cli.py && uv run mypy`
Expected: 1 test PASS; ruff clean; mypy Success.

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: entire suite PASS; ruff clean; mypy Success.

```bash
git add src/mailbrain/cli.py tests/test_cli_scan.py
git commit -m "feat: mailbrain scan command (live Gmail fetch to cache)"
```

---

## Final verification

- [ ] **Whole suite + lint + types green**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all tests PASS; ruff "All checks passed!"; mypy "Success".

- [ ] **Manual smoke of the offline path** (no credentials needed)

Run: `MAILBRAIN_HOME=$(mktemp -d) uv run mailbrain init && MAILBRAIN_HOME=<same dir> uv run mailbrain classify --rules config/rules.yaml`
Expected: prints an empty plan table + "planned 0" (no cached mail yet). Confirms the command runs.

---

## Self-Review (completed during planning)

**Spec coverage (v0.2 + v0.1 milestones 5–7):**
- Scanner (fetch metadata, paginate, `format=metadata`, cache) → Tasks 7, 8. Rule engine (domain/subject/regex/age matching, union labels, OR booleans) → Tasks 3, 5. Desired-state diff / idempotency → Task 6. Dry-run report + metrics → Task 9. Backoff for quota → Task 1. Regex validation carry-over → Task 4. CLI `scan` + `classify` → Tasks 10, 11. Batch-size chunking + `historyId` incremental + `apply`/`rollback` deliberately deferred to Plan 1c (noted in Scope). Conflict detection deferred with rationale (Scope).

**Placeholder scan:** none — every step has complete code + exact commands with expected output.

**Type consistency:** `MessageMeta` (Task 2: `gmail_id, sender, subject, internal_date, current_labels`) used consistently in engine (3,5), scan (8), report (9). `Classification` (Task 2: `add_labels, archive, mark_read`) consumed unchanged by `plan_mutations` (6). `PlannedMutation` (Task 6) consumed by `summarize`/`render_plan` (9). `GmailClient` methods (`list_message_ids`, `get_metadata`, `list_labels`, Task 7) match the `SupportsGmailReads` Protocol and fake clients in scan tests (8). `session_factory`/`init_db` (1a) reused in 8/10/11. `load_credentials`/`build_service` (1a) reused in 11.
