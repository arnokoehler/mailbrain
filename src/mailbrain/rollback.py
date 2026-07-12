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
