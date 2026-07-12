"""Apply planned mutations to Gmail with a persisted audit trail.

Pure set-diff helpers plus the `execute_plan` orchestrator. Every change is a
before->after label-name set; archive removes INBOX, mark-read removes UNREAD.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from mailbrain.labels import ensure_label, name_to_id
from mailbrain.planner import PlannedMutation
from mailbrain.storage.models import Mutation, Run

INBOX = "INBOX"
UNREAD = "UNREAD"


class SupportsApply(Protocol):
    def create_label(self, name: str) -> str: ...
    def batch_modify(
        self, message_ids: list[str], add_label_ids: list[str], remove_label_ids: list[str]
    ) -> None: ...


BATCH_SIZE = 1000


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


def _resolve_ids(
    names: set[str], client: SupportsApply, session: Session, cache: dict[str, str]
) -> frozenset[str]:
    return frozenset(ensure_label(client, session, name, cache) for name in names)


def _resolve_existing_ids(names: set[str], cache: dict[str, str]) -> frozenset[str]:
    """Resolve names to ids WITHOUT creating anything.

    Labels being removed already exist; system labels (INBOX/UNREAD) are their
    own ids and may not be in the cache, so fall back to the name itself.
    """
    return frozenset(cache.get(name, name) for name in names)


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
            remove_ids = _resolve_existing_ids(remove_names, cache)
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

        # Gmail writes happen before the commit below. If the commit fails after a
        # partial write, apply's idempotent desired-state diff lets a re-run finish.
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
