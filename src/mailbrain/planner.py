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
