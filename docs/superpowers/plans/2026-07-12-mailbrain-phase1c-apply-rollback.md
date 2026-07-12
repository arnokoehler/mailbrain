# MailBrain Phase 1c — Apply & Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the planned mutations against Gmail (labels/archive/mark_read, dry-run by default) with a full audit trail, and reverse any run with `rollback`.

**Architecture:** Every change is modelled as a **before→after label-name set** (archive = removing `INBOX`; mark_read = removing `UNREAD`), so execution and rollback are both "apply a set diff." The executor resolves label names to Gmail IDs (auto-creating missing nested labels), groups mutations by identical operation, and calls `batchModify` in ≤1000-id chunks. Each mutation is persisted to the `mutations` table against a `Run`, which is exactly the record `rollback` replays in reverse. Pure logic (set diff, grouping, chunking) is separated from Gmail I/O (mocked in tests); the DB path is exercised through a real SQLite file with a fake client.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, google-api-python-client, Typer, Rich, pytest. Builds on Phase 1a/1b.

---

## Scope

Covers v0.1 milestones 8–9 (apply mutations, rollback). Digest/`weekly` (milestone 10) and AI (Phase 2) remain out of scope. **The tool never deletes** — apply only adds labels and removes `INBOX`/`UNREAD`; it never trashes.

**Locked design decisions:**
- **Dry-run by default.** `apply` prints the plan and mutates nothing unless `--execute` is passed. This is the safety gate on the only mailbox-mutating command.
- **Unified label-set diff.** A mutation's `labels_before`/`labels_after` (JSON name arrays) fully describe the change. `to_add = after − before`, `to_remove = before − after`. `archive`/`mark_read` are just `INBOX`/`UNREAD` in the remove set. `archived_*`/`read_*` booleans are populated for human audit but execution is driven by the set diff.
- **Batching.** Group by identical `(add_ids, remove_ids)`; `batchModify` per group in ≤1000-id chunks.
- **Label resolution.** Names→IDs via the `Label` cache; missing labels auto-created nested (parents first) and cached. INBOX/UNREAD are their own IDs.
- **Rollback** replays a run's mutations in reverse (swap before/after). It executes real Gmail calls and prints metrics; it does not itself create a new rollback-able run (YAGNI).
- **Idempotency preserved.** `apply` computes a fresh plan (scan cache → classify → plan) each run; already-satisfied messages produce no mutation, so re-running `apply --execute` is a no-op.

## File Structure

- `src/mailbrain/gmail/client.py` (modify) — add `create_label(name) -> id` and `batch_modify(ids, add_label_ids, remove_label_ids)`. Now read+modify (still `gmail.modify`, no delete).
- `src/mailbrain/labels.py` (new) — `parent_paths(name)`, `ensure_label(client, session, name, cache)` (nested create + cache), `name_to_id(session)` cache builder.
- `src/mailbrain/apply.py` (new) — pure: `desired_labels`, `chunk`, `group_operations`; orchestration: `execute_plan`.
- `src/mailbrain/rollback.py` (new) — `rollback_run(client, session_factory, run_id)`.
- `src/mailbrain/cli.py` (modify) — `apply` (dry-run default, `--execute`) and `rollback` commands.
- Tests mirror each module.

---

### Task 1: GmailClient write methods

**Files:**
- Modify: `src/mailbrain/gmail/client.py`
- Test: `tests/gmail/test_client_write.py`

- [ ] **Step 1: Write the failing test**

`tests/gmail/test_client_write.py`:
```python
from unittest.mock import MagicMock

from mailbrain.gmail.client import GmailClient


def test_create_label_returns_id():
    service = MagicMock()
    labels_api = service.users.return_value.labels.return_value
    labels_api.create.return_value.execute.return_value = {"id": "Label_9", "name": "Reizen"}
    client = GmailClient(service)

    result = client.create_label("Reizen")

    assert result == "Label_9"
    _, kwargs = labels_api.create.call_args
    assert kwargs["userId"] == "me"
    assert kwargs["body"]["name"] == "Reizen"


def test_batch_modify_sends_body():
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    client = GmailClient(service)

    client.batch_modify(["m1", "m2"], add_label_ids=["Label_9"], remove_label_ids=["INBOX"])

    _, kwargs = messages_api.batchModify.call_args
    assert kwargs["userId"] == "me"
    assert kwargs["body"] == {
        "ids": ["m1", "m2"],
        "addLabelIds": ["Label_9"],
        "removeLabelIds": ["INBOX"],
    }
    messages_api.batchModify.return_value.execute.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gmail/test_client_write.py -v`
Expected: FAIL with `AttributeError: 'GmailClient' object has no attribute 'create_label'`

- [ ] **Step 3: Add the methods to `src/mailbrain/gmail/client.py`**

Add these two methods to the `GmailClient` class (after `list_labels`):
```python
    def create_label(self, name: str) -> str:
        """Create a label (nested paths use '/' in the name); return its Gmail id."""
        body = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        created = self._service.users().labels().create(userId=USER_ID, body=body).execute()
        return created["id"]

    def batch_modify(
        self,
        message_ids: list[str],
        add_label_ids: list[str],
        remove_label_ids: list[str],
    ) -> None:
        """Add/remove label ids on up to 1000 messages in one call. Never deletes."""
        body = {
            "ids": message_ids,
            "addLabelIds": add_label_ids,
            "removeLabelIds": remove_label_ids,
        }
        self._service.users().messages().batchModify(userId=USER_ID, body=body).execute()
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/gmail/test_client_write.py -v && uv run ruff check src/mailbrain/gmail/client.py tests/gmail/test_client_write.py && uv run mypy`
Expected: 2 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/gmail/client.py tests/gmail/test_client_write.py
git commit -m "feat: GmailClient create_label and batch_modify (gmail.modify, no delete)"
```

---

### Task 2: Label resolution + nested auto-create

**Files:**
- Create: `src/mailbrain/labels.py`
- Test: `tests/test_labels.py`

- [ ] **Step 1: Write the failing test**

`tests/test_labels.py`:
```python
from unittest.mock import MagicMock

from sqlalchemy import select

from mailbrain.labels import ensure_label, name_to_id, parent_paths
from mailbrain.storage import db
from mailbrain.storage.models import Label


def test_parent_paths_nested():
    assert parent_paths("A/B/C") == ["A", "A/B", "A/B/C"]


def test_parent_paths_flat():
    assert parent_paths("Reizen") == ["Reizen"]


def test_name_to_id_reads_cache(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        s.add(Label(gmail_id="L1", name="Reizen"))
        s.commit()
    with factory() as s:
        assert name_to_id(s) == {"Reizen": "L1"}


def test_ensure_label_returns_existing(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    client = MagicMock()
    with factory() as s:
        s.add(Label(gmail_id="L1", name="Reizen"))
        s.commit()
        cache = name_to_id(s)
        assert ensure_label(client, s, "Reizen", cache) == "L1"
    client.create_label.assert_not_called()


def test_ensure_label_creates_nested_parents(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    client = MagicMock()
    # each create_label call returns a distinct id in order
    client.create_label.side_effect = ["id_A", "id_AB"]
    with factory() as s:
        cache: dict[str, str] = {}
        result = ensure_label(client, s, "Beleggen/deGiro", cache)
        s.commit()

    assert result == "id_AB"
    # created parent first, then child
    assert [c.args[0] for c in client.create_label.call_args_list] == [
        "Beleggen",
        "Beleggen/deGiro",
    ]
    with factory() as s:
        names = {lbl.name: lbl.gmail_id for lbl in s.scalars(select(Label)).all()}
    assert names == {"Beleggen": "id_A", "Beleggen/deGiro": "id_AB"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_labels.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.labels'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/labels.py`:
```python
"""Resolve label names to Gmail IDs, auto-creating missing nested labels."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from mailbrain.storage.models import Label


class SupportsLabelCreate(Protocol):
    def create_label(self, name: str) -> str: ...


def parent_paths(name: str) -> list[str]:
    """['A', 'A/B', 'A/B/C'] for 'A/B/C' — each nested ancestor plus the leaf."""
    parts = name.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def name_to_id(session: Session) -> dict[str, str]:
    return {lbl.name: lbl.gmail_id for lbl in session.scalars(select(Label)).all()}


def ensure_label(
    client: SupportsLabelCreate,
    session: Session,
    name: str,
    cache: dict[str, str],
) -> str:
    """Return the Gmail id for `name`, creating it (and any missing parents) first.

    `cache` is a name->id map (from name_to_id); it is updated in place as labels
    are created so repeated calls in one run stay consistent.
    """
    for path in parent_paths(name):
        if path not in cache:
            new_id = client.create_label(path)
            session.add(Label(gmail_id=new_id, name=path))
            cache[path] = new_id
    return cache[name]
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_labels.py -v && uv run ruff check src/mailbrain/labels.py tests/test_labels.py && uv run mypy`
Expected: 5 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/labels.py tests/test_labels.py
git commit -m "feat: label name->id resolution with nested auto-create"
```

---

### Task 3: Apply pure logic — desired set, chunk, group

**Files:**
- Create: `src/mailbrain/apply.py`
- Test: `tests/test_apply_logic.py`

- [ ] **Step 1: Write the failing test**

`tests/test_apply_logic.py`:
```python
from mailbrain.apply import chunk, desired_labels, group_operations
from mailbrain.planner import PlannedMutation


def test_desired_labels_adds_and_removes_system():
    current = {"INBOX", "UNREAD", "Work"}
    plan = PlannedMutation(
        gmail_id="m1", add_labels=("Reizen",), archive=True, mark_read=True
    )
    assert desired_labels(current, plan) == {"Work", "Reizen"}  # INBOX+UNREAD removed


def test_desired_labels_label_only():
    current = {"INBOX"}
    plan = PlannedMutation(gmail_id="m1", add_labels=("Reizen",), archive=False, mark_read=False)
    assert desired_labels(current, plan) == {"INBOX", "Reizen"}


def test_chunk_splits():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_chunk_empty():
    assert chunk([], 2) == []


def test_group_operations_groups_identical_ops():
    # two messages with the same add/remove id sets group together
    entries = [
        ("m1", frozenset({"L1"}), frozenset({"INBOX"})),
        ("m2", frozenset({"L1"}), frozenset({"INBOX"})),
        ("m3", frozenset({"L2"}), frozenset()),
    ]
    groups = group_operations(entries)
    assert groups[(frozenset({"L1"}), frozenset({"INBOX"}))] == ["m1", "m2"]
    assert groups[(frozenset({"L2"}), frozenset())] == ["m3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_apply_logic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.apply'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/apply.py`:
```python
"""Apply planned mutations to Gmail with a persisted audit trail.

Pure helpers here; the orchestrating executor is added in the next task.
"""

from __future__ import annotations

from collections import defaultdict

from mailbrain.planner import PlannedMutation

INBOX = "INBOX"
UNREAD = "UNREAD"


def desired_labels(current: set[str], plan: PlannedMutation) -> set[str]:
    """The message's label-name set after the plan is applied."""
    after = set(current) | set(plan.add_labels)
    if plan.archive:
        after.discard(INBOX)
    if plan.mark_read:
        after.discard(UNREAD)
    return after


def chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def group_operations(
    entries: list[tuple[str, frozenset[str], frozenset[str]]],
) -> dict[tuple[frozenset[str], frozenset[str]], list[str]]:
    """Group message ids by identical (add_ids, remove_ids) so each group is one batchModify."""
    groups: dict[tuple[frozenset[str], frozenset[str]], list[str]] = defaultdict(list)
    for message_id, add_ids, remove_ids in entries:
        groups[(add_ids, remove_ids)].append(message_id)
    return dict(groups)
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_apply_logic.py -v && uv run ruff check src/mailbrain/apply.py tests/test_apply_logic.py && uv run mypy`
Expected: 5 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/apply.py tests/test_apply_logic.py
git commit -m "feat: apply pure helpers (desired set diff, chunk, group by operation)"
```

---

### Task 4: Executor — resolve, record, batch-modify

**Files:**
- Modify: `src/mailbrain/apply.py`
- Test: `tests/test_apply_execute.py`

- [ ] **Step 1: Write the failing test**

`tests/test_apply_execute.py`:
```python
import json
from unittest.mock import MagicMock

from sqlalchemy import select

from mailbrain.apply import execute_plan
from mailbrain.planner import PlannedMutation
from mailbrain.storage import db
from mailbrain.storage.models import Label, Mutation, Run


def _factory(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(Label(gmail_id="UNREAD", name="UNREAD"))
        s.add(Label(gmail_id="L_reizen", name="Reizen"))
        s.commit()
    return factory


def test_execute_plan_calls_batch_modify_and_records(tmp_path):
    factory = _factory(tmp_path)
    client = MagicMock()
    plans = [
        PlannedMutation(gmail_id="m1", add_labels=("Reizen",), archive=True, mark_read=False),
    ]
    current = {"m1": {"INBOX", "Reizen"}}  # note: Reizen already present -> only archive acts
    # Actually add is filtered by planner; here Reizen is in add_labels but also current,
    # so the executor must still compute add/remove from the before->after set diff.

    run_id = execute_plan(client, factory, plans, current)

    # one batchModify call: remove INBOX (archive), no adds (Reizen already present)
    args, kwargs = client.batch_modify.call_args
    assert kwargs["message_ids"] == ["m1"] or args[0] == ["m1"]
    with factory() as s:
        run = s.get(Run, run_id)
        assert run is not None and run.dry_run is False
        mut = s.scalar(select(Mutation).where(Mutation.message_gmail_id == "m1"))
        assert mut is not None
        assert mut.applied is True
        assert mut.archived_before is True
        assert mut.archived_after is False
        assert set(json.loads(mut.labels_before)) == {"INBOX", "Reizen"}
        assert set(json.loads(mut.labels_after)) == {"Reizen"}


def test_execute_plan_creates_missing_label(tmp_path):
    factory = _factory(tmp_path)
    client = MagicMock()
    client.create_label.return_value = "L_new"
    plans = [
        PlannedMutation(gmail_id="m2", add_labels=("Administratie",), archive=False, mark_read=False),
    ]
    current = {"m2": {"INBOX"}}

    execute_plan(client, factory, plans, current)

    client.create_label.assert_called_once_with("Administratie")
    # the new label id is in the add set of the batch call
    _, kwargs = client.batch_modify.call_args
    assert "L_new" in kwargs.get("add_label_ids", [])


def test_execute_plan_empty_plans_no_calls(tmp_path):
    factory = _factory(tmp_path)
    client = MagicMock()
    run_id = execute_plan(client, factory, [], {})
    client.batch_modify.assert_not_called()
    with factory() as s:
        assert s.get(Run, run_id) is not None  # a run row still recorded (scanned=0 work)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_apply_execute.py -v`
Expected: FAIL with `ImportError: cannot import name 'execute_plan'`

- [ ] **Step 3: Add the executor to `src/mailbrain/apply.py`**

Add these imports to the top of `apply.py` (extend the existing import block):
```python
import json
from collections.abc import Mapping
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from mailbrain.labels import ensure_label, name_to_id
from mailbrain.storage.models import Mutation, Run
```
Also add a Protocol for the client capabilities the executor needs (near the top, after the constants):
```python
class SupportsApply(Protocol):
    def create_label(self, name: str) -> str: ...
    def batch_modify(
        self, message_ids: list[str], add_label_ids: list[str], remove_label_ids: list[str]
    ) -> None: ...


BATCH_SIZE = 1000
```
Then append the executor:
```python
def _resolve_ids(names: set[str], client: SupportsApply, session: Session, cache: dict[str, str]) -> frozenset[str]:
    return frozenset(ensure_label(client, session, name, cache) for name in names)


def execute_plan(
    client: SupportsApply,
    session_factory: sessionmaker[Session],
    plans: list[PlannedMutation],
    current_labels: Mapping[str, set[str]],
) -> int:
    """Execute plans against Gmail, recording every mutation under a new Run. Returns run id."""
    with session_factory() as session:
        run = Run(dry_run=False, scanned=len(current_labels))
        session.add(run)
        session.flush()  # assign run.id
        cache = name_to_id(session)

        entries: list[tuple[str, frozenset[str], frozenset[str]]] = []
        labeled = archived = 0
        for plan in plans:
            before = set(current_labels.get(plan.gmail_id, set()))
            after = desired_labels(before, plan)
            add_names = after - before
            remove_names = before - after
            if not add_names and not remove_names:
                continue
            add_ids = _resolve_ids(add_names, client, session, cache)
            remove_ids = _resolve_ids(remove_names, client, session, cache)
            entries.append((plan.gmail_id, add_ids, remove_ids))
            session.add(
                Mutation(
                    run_id=run.id,
                    message_gmail_id=plan.gmail_id,
                    labels_before=json.dumps(sorted(before)),
                    labels_after=json.dumps(sorted(after)),
                    archived_before=INBOX in before,
                    archived_after=INBOX in after,
                    read_before=UNREAD in before,
                    read_after=UNREAD in after,
                    applied=True,
                )
            )
            if add_names:
                labeled += 1
            if INBOX in before and INBOX not in after:
                archived += 1

        for (add_ids, remove_ids), message_ids in group_operations(entries).items():
            for batch in chunk(message_ids, BATCH_SIZE):
                client.batch_modify(
                    message_ids=batch,
                    add_label_ids=sorted(add_ids),
                    remove_label_ids=sorted(remove_ids),
                )

        run.labeled = labeled
        run.archived = archived
        session.commit()
        return run.id
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_apply_execute.py -v && uv run ruff check src/mailbrain/apply.py tests/test_apply_execute.py && uv run mypy`
Expected: 3 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/apply.py tests/test_apply_execute.py
git commit -m "feat: apply executor resolves labels, records mutations, batch-modifies"
```

---

### Task 5: Rollback

**Files:**
- Create: `src/mailbrain/rollback.py`
- Test: `tests/test_rollback.py`

- [ ] **Step 1: Write the failing test**

`tests/test_rollback.py`:
```python
import json
from unittest.mock import MagicMock

from mailbrain.rollback import rollback_run
from mailbrain.storage import db
from mailbrain.storage.models import Label, Mutation, Run


def _factory(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(Label(gmail_id="L_reizen", name="Reizen"))
        s.commit()
    return factory


def _seed_run(factory) -> int:
    with factory() as s:
        run = Run(dry_run=False)
        s.add(run)
        s.flush()
        # original apply: added Reizen, removed INBOX (archived)
        s.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="m1",
                labels_before=json.dumps(["INBOX"]),
                labels_after=json.dumps(["Reizen"]),
                archived_before=True,
                archived_after=False,
                read_before=False,
                read_after=False,
                applied=True,
            )
        )
        s.commit()
        return run.id


def test_rollback_reverses_mutations(tmp_path):
    factory = _factory(tmp_path)
    run_id = _seed_run(factory)
    client = MagicMock()

    count = rollback_run(client, factory, run_id)

    assert count == 1
    # reverse of (add Reizen, remove INBOX) is (add INBOX, remove Reizen)
    _, kwargs = client.batch_modify.call_args
    assert kwargs["message_ids"] == ["m1"]
    assert kwargs["add_label_ids"] == ["INBOX"]
    assert kwargs["remove_label_ids"] == ["L_reizen"]


def test_rollback_unknown_run_returns_zero(tmp_path):
    factory = _factory(tmp_path)
    client = MagicMock()
    assert rollback_run(client, factory, 999) == 0
    client.batch_modify.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rollback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.rollback'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/rollback.py`:
```python
"""Reverse a run's recorded mutations (swap each before<->after label set)."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.apply import BATCH_SIZE, SupportsApply, chunk, group_operations
from mailbrain.labels import name_to_id
from mailbrain.storage.models import Mutation


def _ids_for(names: list[str], cache: dict[str, str]) -> frozenset[str]:
    # System labels (INBOX/UNREAD) are their own ids; user labels resolve via cache.
    return frozenset(cache.get(name, name) for name in names)


def rollback_run(
    client: SupportsApply,
    session_factory: sessionmaker[Session],
    run_id: int,
) -> int:
    """Reverse every mutation of `run_id`. Returns the number of messages reversed."""
    with session_factory() as session:
        cache = name_to_id(session)
        mutations = list(
            session.scalars(select(Mutation).where(Mutation.run_id == run_id)).all()
        )
        entries: list[tuple[str, frozenset[str], frozenset[str]]] = []
        for m in mutations:
            before = set(json.loads(m.labels_before))
            after = set(json.loads(m.labels_after))
            # to undo: target state is `before`; current state is `after`
            re_add = _ids_for(sorted(before - after), cache)
            re_remove = _ids_for(sorted(after - before), cache)
            if re_add or re_remove:
                entries.append((m.message_gmail_id, re_add, re_remove))

        for (add_ids, remove_ids), message_ids in group_operations(entries).items():
            for batch in chunk(message_ids, BATCH_SIZE):
                client.batch_modify(
                    message_ids=batch,
                    add_label_ids=sorted(add_ids),
                    remove_label_ids=sorted(remove_ids),
                )
        return len(entries)
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_rollback.py -v && uv run ruff check src/mailbrain/rollback.py tests/test_rollback.py && uv run mypy`
Expected: 2 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/rollback.py tests/test_rollback.py
git commit -m "feat: rollback reverses a run's recorded mutations"
```

---

### Task 6: CLI `apply` (dry-run default, --execute)

**Files:**
- Modify: `src/mailbrain/cli.py`
- Test: `tests/test_cli_apply.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_apply.py`:
```python
import json
from datetime import UTC, datetime

import mailbrain.cli as cli
from typer.testing import CliRunner

from mailbrain.cli import app
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message

runner = CliRunner()

RULES_YAML = (
    "rules:\n"
    "  - id: booking\n"
    "    match:\n"
    "      from_domain: [booking.com]\n"
    "    actions:\n"
    "      add_labels: [Reizen]\n"
    "      archive: true\n"
)


def _seed(home):
    db.init_db(home / "state.db")
    factory = db.session_factory(home / "state.db")
    with factory() as s:
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(
            Message(
                gmail_id="m1",
                sender="a@booking.com",
                subject="Receipt",
                label_ids=json.dumps(["INBOX"]),
                internal_date=datetime(2026, 7, 1, tzinfo=UTC),
            )
        )
        s.commit()


def _rules(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(RULES_YAML)
    return p


def test_apply_dry_run_by_default_does_not_execute(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home)

    called = {"execute": False}
    monkeypatch.setattr(cli, "execute_plan", lambda *a, **k: called.__setitem__("execute", True) or 1)

    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path))])
    assert result.exit_code == 0, result.output
    assert called["execute"] is False  # dry-run must NOT execute
    assert "dry-run" in result.output.lower()
    assert "Reizen" in result.output


def test_apply_execute_calls_executor(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home)

    captured = {}
    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda creds: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service: "CLIENT")

    def fake_execute(client, factory, plans, current):
        captured["client"] = client
        captured["n"] = len(plans)
        return 42

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    (home / "credentials.json").write_text("{}")

    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path)), "--execute"])
    assert result.exit_code == 0, result.output
    assert captured["client"] == "CLIENT"
    assert captured["n"] == 1  # one planned mutation
    assert "42" in result.output  # run id surfaced
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_apply.py -v`
Expected: FAIL — no `apply` command.

- [ ] **Step 3: Add the command to `src/mailbrain/cli.py`**

Extend the import block with:
```python
from mailbrain.apply import execute_plan
```
(`GmailClient`, `load_credentials`, `build_service`, `load_cached`, `plan_mutations`, `classify as classify_messages`, `config`, `session_factory`, `render_plan`, `render_metrics`, `summarize` are already imported from Tasks in 1b.) Append the command:
```python
@app.command()
def apply(
    rules: Path = typer.Option(  # noqa: B008 - Typer option factory
        Path("config/rules.yaml"), "--rules", help="Path to rules YAML."
    ),
    execute: bool = typer.Option(
        False, "--execute", help="Actually apply changes to Gmail (default: dry-run)."
    ),
) -> None:
    """Apply the classification plan to Gmail. Dry-run unless --execute is given."""
    if not rules.exists():
        console.print(f"[red]Rules file not found:[/] {rules}")
        raise typer.Exit(code=2)
    factory = session_factory(paths.db_path())
    messages, current = load_cached(factory)
    classifications = classify_messages(messages, config.load_rules(rules).rules, datetime.now(UTC))
    plans = plan_mutations(classifications, current)

    if not execute:
        render_plan(plans, messages, console=console)
        render_metrics(summarize(plans, scanned=len(messages)), console=console)
        console.print("[yellow]dry-run[/] — no changes applied. Re-run with --execute to apply.")
        return

    if not paths.credentials_path().exists():
        console.print(f"[red]credentials.json not found:[/] {paths.credentials_path()}")
        raise typer.Exit(code=2)
    creds = load_credentials(paths.credentials_path(), paths.token_path())
    client = GmailClient(build_service(creds))
    run_id = execute_plan(client, factory, plans, current)
    console.print(f"[green]Applied[/] {len(plans)} changes (run {run_id}).")
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_cli_apply.py -v && uv run ruff check src/mailbrain/cli.py tests/test_cli_apply.py && uv run mypy`
Expected: 2 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/cli.py tests/test_cli_apply.py
git commit -m "feat: mailbrain apply command (dry-run default, --execute gate)"
```

---

### Task 7: CLI `rollback`

**Files:**
- Modify: `src/mailbrain/cli.py`
- Test: `tests/test_cli_rollback.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_rollback.py`:
```python
import mailbrain.cli as cli
from typer.testing import CliRunner

from mailbrain.cli import app
from mailbrain.storage import db

runner = CliRunner()


def test_rollback_wires_client_and_calls_rollback(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    (home / "credentials.json").write_text("{}")

    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda creds: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service: "CLIENT")

    captured = {}

    def fake_rollback(client, factory, run_id):
        captured["client"] = client
        captured["run_id"] = run_id
        return 3

    monkeypatch.setattr(cli, "rollback_run", fake_rollback)

    result = runner.invoke(app, ["rollback", "7"])
    assert result.exit_code == 0, result.output
    assert captured["run_id"] == 7
    assert captured["client"] == "CLIENT"
    assert "3" in result.output


def test_rollback_missing_credentials_errors(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")

    result = runner.invoke(app, ["rollback", "7"])
    assert result.exit_code == 2
    assert "credentials.json not found" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_rollback.py -v`
Expected: FAIL — no `rollback` command.

- [ ] **Step 3: Add the command to `src/mailbrain/cli.py`**

Extend the import block with:
```python
from mailbrain.rollback import rollback_run
```
Append the command:
```python
@app.command()
def rollback(run_id: int = typer.Argument(..., help="The run id to reverse.")) -> None:
    """Reverse every mutation made by a previous run."""
    if not paths.credentials_path().exists():
        console.print(f"[red]credentials.json not found:[/] {paths.credentials_path()}")
        raise typer.Exit(code=2)
    creds = load_credentials(paths.credentials_path(), paths.token_path())
    client = GmailClient(build_service(creds))
    count = rollback_run(client, session_factory(paths.db_path()), run_id)
    console.print(f"[green]Rolled back[/] {count} messages from run {run_id}.")
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_cli_rollback.py -v && uv run ruff check src/mailbrain/cli.py tests/test_cli_rollback.py && uv run mypy`
Expected: 2 tests PASS; ruff clean; mypy Success.

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: entire suite PASS; ruff clean; mypy Success.

```bash
git add src/mailbrain/cli.py tests/test_cli_rollback.py
git commit -m "feat: mailbrain rollback command"
```

---

## Final verification

- [ ] **Whole suite + lint + types green**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all tests PASS; ruff "All checks passed!"; mypy "Success".

- [ ] **Manual smoke of the dry-run path** (no credentials needed)

Run: `MAILBRAIN_HOME=$(mktemp -d) uv run mailbrain init && MAILBRAIN_HOME=<same dir> uv run mailbrain apply --rules config/rules.yaml`
Expected: prints an empty plan + "dry-run — no changes applied"; exit 0.

---

## Self-Review (completed during planning)

**Spec coverage (v0.2 + v0.1 milestones 8–9):**
- Apply mutations (batch, ≤1000 chunks) → Tasks 3, 4. Auto-create nested labels → Task 2. Name→ID resolution (1b carry-over) → Tasks 2, 4. Persist audit (mutations under a Run) → Task 4. Rollback → Tasks 5, 7. Dry-run default + explicit execute gate (spec "dry-run by default") → Task 6. GmailClient write methods within gmail.modify (no delete) → Task 1. Idempotency preserved (fresh plan each apply; empty plan → no calls) → Task 4 test `test_execute_plan_empty_plans_no_calls`. Metrics on the Run → Task 4. Deferred correctly: digest/weekly (1d/Phase 1c-digest), AI (Phase 2), `historyId` incremental scan.

**Placeholder scan:** none — every code step has complete code; every run step has a command + expected output.

**Type consistency:** `PlannedMutation` (from 1b: gmail_id/add_labels/archive/mark_read) consumed by `desired_labels`/`execute_plan` (Tasks 3, 4). `SupportsApply` Protocol (Task 4: `create_label`, `batch_modify`) matches `GmailClient` methods (Task 1) and is reused by `rollback_run` (Task 5). `ensure_label(client, session, name, cache)` / `name_to_id(session)` (Task 2) used verbatim in the executor (Task 4). `BATCH_SIZE`, `chunk`, `group_operations`, `SupportsApply` imported by rollback from `apply` (Task 5). `Mutation` fields (labels_before/after, archived_before/after, read_before/after, applied, run_id) match the 1a schema. `execute_plan(client, factory, plans, current)` / `rollback_run(client, factory, run_id)` signatures match their CLI call sites (Tasks 6, 7). `Run(dry_run, scanned, labeled, archived)` matches the 1a schema.
