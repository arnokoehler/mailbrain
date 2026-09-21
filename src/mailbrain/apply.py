"""Checkpointed Gmail mutation execution with durable audit state."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.labels import name_to_id, parent_paths
from mailbrain.planner import PlannedMutation
from mailbrain.storage.models import Label, LabelCreationIntent, Message, Mutation, Run, ScanRun

INBOX = "INBOX"
UNREAD = "UNREAD"
BATCH_SIZE = 1000
UNRESOLVED_MUTATION_STATUSES = ("pending", "in_flight", "uncertain", "conflict")
UNRESOLVED_LABEL_STATUSES = ("pending", "in_flight", "uncertain")


class SupportsApply(Protocol):
    def create_label(self, name: str) -> str: ...
    def batch_modify(
        self, message_ids: list[str], add_label_ids: list[str], remove_label_ids: list[str]
    ) -> None: ...


class UnresolvedIntentError(RuntimeError):
    pass


class LabelResolutionError(RuntimeError):
    pass


def desired_labels(current: set[str], plan: PlannedMutation) -> set[str]:
    after = set(current) | set(plan.add_labels)
    if plan.archive:
        after.discard(INBOX)
    if plan.mark_read:
        after.discard(UNREAD)
    return after


def chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def group_operations(
    entries: list[tuple[str, frozenset[str], frozenset[str]]],
) -> dict[tuple[frozenset[str], frozenset[str]], list[str]]:
    groups: dict[tuple[frozenset[str], frozenset[str]], list[str]] = defaultdict(list)
    for message_id, add_ids, remove_ids in entries:
        groups[(add_ids, remove_ids)].append(message_id)
    return dict(groups)


def is_definite_response_failure(error: BaseException) -> bool:
    response = getattr(error, "resp", None)
    status = getattr(response, "status", None)
    if not isinstance(status, int):
        status = getattr(error, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in (408, 409, 429)


def assert_no_unresolved_intents(session: Session) -> None:
    mutation = session.scalar(
        select(Mutation.id).where(Mutation.status.in_(UNRESOLVED_MUTATION_STATUSES)).limit(1)
    )
    label_intent = session.scalar(
        select(LabelCreationIntent.id)
        .where(LabelCreationIntent.status.in_(UNRESOLVED_LABEL_STATUSES))
        .limit(1)
    )
    if mutation is not None or label_intent is not None:
        raise UnresolvedIntentError(
            "unresolved mutation or label intent blocks a new independent writer"
        )


def execute_plan(
    client: SupportsApply,
    session_factory: sessionmaker[Session],
    plans: list[PlannedMutation],
    current_labels: Mapping[str, set[str]],
    *,
    scan_id: int | None = None,
    rules_hash: str | None = None,
    safety_hash: str | None = None,
    batch_size: int = BATCH_SIZE,
    now: datetime | None = None,
) -> int:
    timestamp = now or datetime.now(UTC)
    run_id = _prepare_apply(
        session_factory,
        plans,
        current_labels,
        scan_id,
        rules_hash,
        safety_hash,
        timestamp,
    )
    try:
        cache = _resolve_label_intents(client, session_factory, run_id, timestamp)
    except BaseException as error:
        _finish_run_after_error(session_factory, run_id, error, timestamp)
        raise
    _resolve_mutation_ids(session_factory, run_id, cache, batch_size)
    _execute_batches(client, session_factory, run_id, timestamp)
    return run_id


def _prepare_apply(
    session_factory: sessionmaker[Session],
    plans: list[PlannedMutation],
    current_labels: Mapping[str, set[str]],
    scan_id: int | None,
    rules_hash: str | None,
    safety_hash: str | None,
    timestamp: datetime,
) -> int:
    with session_factory() as session:
        assert_no_unresolved_intents(session)
        prepared: list[tuple[PlannedMutation, set[str], set[str]]] = []
        required_labels: set[str] = set()
        for plan in plans:
            before = set(current_labels.get(plan.gmail_id, set()))
            after = desired_labels(before, plan)
            if before == after:
                continue
            prepared.append((plan, before, after))
            for name in after - before:
                required_labels.update(parent_paths(name))
        cache = name_to_id(session)
        run = Run(
            dry_run=False,
            run_type="apply",
            status="prepared",
            scan_id=scan_id,
            rules_hash=rules_hash,
            safety_hash=safety_hash,
            started_at=timestamp,
            prepared_at=timestamp,
            scanned=len(current_labels),
            intended_count=len(prepared),
        )
        session.add(run)
        session.flush()
        for plan, before, after in prepared:
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
                    applied=False,
                    status="pending",
                    matched_rule_ids=json.dumps(sorted(plan.matched_rule_ids)),
                    prepared_at=timestamp,
                )
            )
        for label_name in sorted(required_labels - cache.keys()):
            session.add(
                LabelCreationIntent(
                    run_id=run.id, label_name=label_name, status="pending", prepared_at=timestamp
                )
            )
        if scan_id is not None:
            scan = session.get(ScanRun, scan_id)
            if scan is None:
                raise ValueError(f"unknown scan id {scan_id}")
            scan.status = "invalidated"
        session.commit()
        return run.id


def _live_label_cache(client: SupportsApply, session: Session) -> dict[str, str]:
    cache = name_to_id(session)
    list_labels = getattr(client, "list_labels", None)
    if not callable(list_labels):
        return cache
    listed = list_labels()
    if not isinstance(listed, dict):
        return cache
    existing = {label.gmail_id: label for label in session.scalars(select(Label)).all()}
    for gmail_id, name in listed.items():
        if not isinstance(gmail_id, str) or not isinstance(name, str):
            continue
        row = existing.get(gmail_id)
        if row is None:
            session.add(Label(gmail_id=gmail_id, name=name))
        else:
            row.name = name
        cache[name] = gmail_id
    session.commit()
    return cache


def _resolve_label_intents(
    client: SupportsApply,
    session_factory: sessionmaker[Session],
    run_id: int,
    timestamp: datetime,
) -> dict[str, str]:
    with session_factory() as session:
        cache = _live_label_cache(client, session)
        intent_ids = list(
            session.scalars(
                select(LabelCreationIntent.id)
                .where(LabelCreationIntent.run_id == run_id)
                .order_by(LabelCreationIntent.id)
            ).all()
        )
    for intent_id in intent_ids:
        with session_factory() as session:
            intent = session.get(LabelCreationIntent, intent_id)
            if intent is None:
                continue
            cache = _live_label_cache(client, session)
            existing_id = cache.get(intent.label_name)
            if existing_id is not None:
                _resolve_intent(session, intent, existing_id, timestamp)
                session.commit()
                continue
            intent.status = "in_flight"
            intent.started_at = timestamp
            session.commit()
            label_name = intent.label_name
        try:
            created_id = client.create_label(label_name)
        except BaseException as error:
            with session_factory() as session:
                intent = session.get(LabelCreationIntent, intent_id)
                if intent is None:
                    raise
                cache = _live_label_cache(client, session)
                reconciled_id = cache.get(label_name)
                if reconciled_id is not None:
                    _resolve_intent(session, intent, reconciled_id, timestamp)
                    session.commit()
                    continue
                intent.status = "failed" if is_definite_response_failure(error) else "uncertain"
                intent.error_message = str(error)
                intent.finished_at = timestamp
                session.commit()
            raise LabelResolutionError(f"could not safely resolve label {label_name!r}") from error
        with session_factory() as session:
            intent = session.get(LabelCreationIntent, intent_id)
            if intent is None:
                raise RuntimeError(f"label intent {intent_id} disappeared")
            _resolve_intent(session, intent, created_id, timestamp)
            session.commit()
    with session_factory() as session:
        unresolved = session.scalar(
            select(LabelCreationIntent.id)
            .where(
                LabelCreationIntent.run_id == run_id,
                LabelCreationIntent.status != "resolved",
            )
            .limit(1)
        )
        if unresolved is not None:
            raise LabelResolutionError("unresolved label intent blocks message writes")
        return name_to_id(session)


def _resolve_intent(
    session: Session, intent: LabelCreationIntent, gmail_id: str, timestamp: datetime
) -> None:
    existing = session.scalar(select(Label).where(Label.gmail_id == gmail_id))
    if existing is None:
        session.add(Label(gmail_id=gmail_id, name=intent.label_name))
    else:
        existing.name = intent.label_name
    intent.gmail_label_id = gmail_id
    intent.status = "resolved"
    intent.finished_at = timestamp
    intent.error_message = None


def _resolve_mutation_ids(
    session_factory: sessionmaker[Session],
    run_id: int,
    cache: dict[str, str],
    batch_size: int,
) -> None:
    if batch_size <= 0 or batch_size > BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {BATCH_SIZE}")
    with session_factory() as session:
        mutations = list(
            session.scalars(
                select(Mutation).where(Mutation.run_id == run_id).order_by(Mutation.id)
            ).all()
        )
        grouped: dict[tuple[frozenset[str], frozenset[str]], list[Mutation]] = defaultdict(list)
        for mutation in mutations:
            before_names = set(json.loads(mutation.labels_before))
            after_names = set(json.loads(mutation.labels_after))
            before_ids = _ids_for_names(before_names, cache)
            add_ids = _ids_for_names(after_names - before_names, cache)
            remove_ids = _ids_for_names(before_names - after_names, cache)
            mutation.gmail_label_ids_before = json.dumps(sorted(before_ids))
            mutation.gmail_label_ids_add = json.dumps(sorted(add_ids))
            mutation.gmail_label_ids_remove = json.dumps(sorted(remove_ids))
            grouped[(add_ids, remove_ids)].append(mutation)
        batch_number = 0
        for key in sorted(grouped, key=lambda value: (sorted(value[0]), sorted(value[1]))):
            for batch in chunk([str(mutation.id) for mutation in grouped[key]], batch_size):
                batch_number += 1
                batch_id = f"{run_id}:{batch_number}"
                for mutation_id in batch:
                    batched_mutation = session.get(Mutation, int(mutation_id))
                    if batched_mutation is not None:
                        batched_mutation.batch_id = batch_id
        session.commit()


def _ids_for_names(names: set[str], cache: dict[str, str]) -> frozenset[str]:
    missing = sorted(name for name in names if name not in cache and name not in (INBOX, UNREAD))
    if missing:
        raise LabelResolutionError(f"missing Gmail label ids for: {', '.join(missing)}")
    return frozenset(cache.get(name, name) for name in names)


def _execute_batches(
    client: Any,
    session_factory: sessionmaker[Session],
    run_id: int,
    timestamp: datetime,
) -> None:
    with session_factory() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} disappeared")
        run.status = "running"
        batch_ids = list(
            session.scalars(
                select(Mutation.batch_id)
                .where(Mutation.run_id == run_id, Mutation.batch_id.is_not(None))
                .distinct()
                .order_by(Mutation.batch_id)
            ).all()
        )
        if not batch_ids:
            run.status = "succeeded"
            run.finished_at = timestamp
        session.commit()
    for batch_id in batch_ids:
        with session_factory() as session:
            mutations = list(
                session.scalars(
                    select(Mutation).where(Mutation.run_id == run_id, Mutation.batch_id == batch_id)
                ).all()
            )
            for mutation in mutations:
                mutation.status = "in_flight"
                mutation.started_at = timestamp
            session.commit()
            message_ids = [mutation.message_gmail_id for mutation in mutations]
            add_ids = sorted(json.loads(mutations[0].gmail_label_ids_add or "[]"))
            remove_ids = sorted(json.loads(mutations[0].gmail_label_ids_remove or "[]"))
        try:
            client.batch_modify(
                message_ids=message_ids,
                add_label_ids=add_ids,
                remove_label_ids=remove_ids,
            )
        except BaseException as error:
            status = "failed" if is_definite_response_failure(error) else "uncertain"
            with session_factory() as session:
                for mutation in session.scalars(
                    select(Mutation).where(Mutation.run_id == run_id, Mutation.batch_id == batch_id)
                ).all():
                    mutation.status = status
                    mutation.error_message = str(error)
                    mutation.finished_at = timestamp
                _refresh_run_counts(session, run_id, timestamp, str(error))
                session.commit()
            raise
        with session_factory() as session:
            persisted = list(
                session.scalars(
                    select(Mutation).where(Mutation.run_id == run_id, Mutation.batch_id == batch_id)
                ).all()
            )
            for mutation in persisted:
                mutation.status = "applied"
                mutation.applied = True
                mutation.finished_at = timestamp
                _sync_cached_message(session, client, mutation)
            _refresh_run_counts(session, run_id, timestamp)
            session.commit()


def _sync_cached_message(session: Session, client: Any, mutation: Mutation) -> None:
    row = session.scalar(select(Message).where(Message.gmail_id == mutation.message_gmail_id))
    if row is None:
        return
    live_ids: list[str] | None = None
    get_metadata = getattr(client, "get_metadata", None)
    if callable(get_metadata):
        metadata = get_metadata(mutation.message_gmail_id)
        if isinstance(metadata, Mapping) and isinstance(metadata.get("label_ids"), list):
            live_ids = [value for value in metadata["label_ids"] if isinstance(value, str)]
    if live_ids is None:
        existing = set(json.loads(row.label_ids or "[]"))
        existing.update(json.loads(mutation.gmail_label_ids_add or "[]"))
        existing.difference_update(json.loads(mutation.gmail_label_ids_remove or "[]"))
        live_ids = sorted(existing)
    row.label_ids = json.dumps(sorted(live_ids))


def _refresh_run_counts(
    session: Session,
    run_id: int,
    timestamp: datetime,
    error_message: str | None = None,
) -> None:
    run = session.get(Run, run_id)
    if run is None:
        return
    statuses = list(session.scalars(select(Mutation.status).where(Mutation.run_id == run_id)).all())
    run.applied_count = statuses.count("applied")
    run.failed_count = statuses.count("failed")
    run.uncertain_count = statuses.count("uncertain")
    run.conflict_count = statuses.count("conflict")
    run.labeled = run.applied_count
    run.archived = sum(
        1
        for mutation in session.scalars(select(Mutation).where(Mutation.run_id == run_id)).all()
        if mutation.status == "applied" and mutation.archived_before and not mutation.archived_after
    )
    run.errors = run.failed_count + run.uncertain_count + run.conflict_count
    run.error_message = error_message
    terminal = {"applied", "failed", "uncertain", "conflict", "abandoned"}
    if statuses and all(status in terminal for status in statuses):
        if all(status == "applied" for status in statuses):
            run.status = "succeeded"
        elif run.uncertain_count or run.conflict_count:
            run.status = "needs_review"
        elif run.applied_count:
            run.status = "partial"
        else:
            run.status = "failed"
        run.finished_at = timestamp
    elif not statuses:
        run.status = "succeeded"
        run.finished_at = timestamp
    else:
        run.status = "partial"


def _finish_run_after_error(
    session_factory: sessionmaker[Session], run_id: int, error: BaseException, timestamp: datetime
) -> None:
    with session_factory() as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        unresolved_label = session.scalar(
            select(LabelCreationIntent.id)
            .where(
                LabelCreationIntent.run_id == run_id,
                LabelCreationIntent.status == "uncertain",
            )
            .limit(1)
        )
        run.status = "needs_review" if unresolved_label is not None else "failed"
        run.error_message = str(error)
        run.finished_at = timestamp
        session.commit()
