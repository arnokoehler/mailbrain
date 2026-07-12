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
