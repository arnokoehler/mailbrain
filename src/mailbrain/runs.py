"""Run inspection, reconciliation, and confirmed local abandonment."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.apply import _refresh_run_counts, _sync_cached_message
from mailbrain.storage.models import Label, LabelCreationIntent, Mutation, Run


class SupportsMetadata(Protocol):
    def get_metadata(self, message_id: str) -> dict[str, Any]: ...
    def list_labels(self) -> dict[str, str]: ...


@dataclass(frozen=True)
class LabelIntentState:
    id: int
    label_name: str
    status: str
    gmail_label_id: str | None
    error_message: str | None


@dataclass(frozen=True)
class MutationState:
    id: int
    message_gmail_id: str
    status: str
    batch_id: str | None
    error_message: str | None


@dataclass(frozen=True)
class RunState:
    id: int
    run_type: str
    status: str
    source_run_id: int | None
    intended_count: int
    applied_count: int
    failed_count: int
    uncertain_count: int
    conflict_count: int
    mutations: tuple[MutationState, ...] = ()
    label_intents: tuple[LabelIntentState, ...] = ()


def list_runs(session_factory: sessionmaker[Session]) -> list[RunState]:
    with session_factory() as session:
        return [_run_state(run) for run in session.scalars(select(Run).order_by(Run.id)).all()]


def inspect_run(session_factory: sessionmaker[Session], run_id: int) -> RunState:
    with session_factory() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f"unknown run id {run_id}")
        mutations = tuple(
            MutationState(
                id=mutation.id,
                message_gmail_id=mutation.message_gmail_id,
                status=mutation.status,
                batch_id=mutation.batch_id,
                error_message=mutation.error_message,
            )
            for mutation in session.scalars(
                select(Mutation).where(Mutation.run_id == run_id).order_by(Mutation.id)
            ).all()
        )
        label_intents = tuple(
            LabelIntentState(
                id=intent.id,
                label_name=intent.label_name,
                status=intent.status,
                gmail_label_id=intent.gmail_label_id,
                error_message=intent.error_message,
            )
            for intent in session.scalars(
                select(LabelCreationIntent)
                .where(LabelCreationIntent.run_id == run_id)
                .order_by(LabelCreationIntent.id)
            ).all()
        )
        return replace(_run_state(run), mutations=mutations, label_intents=label_intents)


def reconcile_run(
    client: SupportsMetadata,
    session_factory: sessionmaker[Session],
    run_id: int,
    *,
    now: datetime | None = None,
) -> RunState:
    timestamp = now or datetime.now(UTC)
    live_labels = client.list_labels()
    with session_factory() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f"unknown run id {run_id}")
        mutations = list(
            session.scalars(
                select(Mutation).where(
                    Mutation.run_id == run_id, Mutation.status.in_(("in_flight", "uncertain"))
                )
            ).all()
        )
        for mutation in mutations:
            mutation.status = "uncertain"
            mutation.reconciled_at = timestamp
            metadata = client.get_metadata(mutation.message_gmail_id)
            live_ids = set(metadata.get("label_ids", []))
            before_ids = set(_json_ids(mutation.gmail_label_ids_before))
            desired_ids = (before_ids | set(_json_ids(mutation.gmail_label_ids_add))) - set(
                _json_ids(mutation.gmail_label_ids_remove)
            )
            affected_ids = set(_json_ids(mutation.gmail_label_ids_add)) | set(
                _json_ids(mutation.gmail_label_ids_remove)
            )
            if live_ids & affected_ids not in (
                before_ids & affected_ids,
                desired_ids & affected_ids,
            ):
                mutation.status = "conflict"
            _sync_cached_message(session, client, mutation)
        labels_by_name = {name: gmail_id for gmail_id, name in live_labels.items()}
        for intent in session.scalars(
            select(LabelCreationIntent).where(
                LabelCreationIntent.run_id == run_id,
                LabelCreationIntent.status.in_(("in_flight", "uncertain")),
            )
        ).all():
            intent.status = "uncertain"
            intent.reconciled_at = timestamp
            gmail_label_id = labels_by_name.get(intent.label_name)
            if gmail_label_id is not None:
                label = session.scalar(select(Label).where(Label.gmail_id == gmail_label_id))
                if label is None:
                    session.add(Label(gmail_id=gmail_label_id, name=intent.label_name))
                else:
                    label.name = intent.label_name
                intent.gmail_label_id = gmail_label_id
                intent.status = "resolved"
                intent.finished_at = timestamp
                intent.error_message = None
        _refresh_run_counts(session, run_id, timestamp)
        unresolved_label_intent = session.scalar(
            select(LabelCreationIntent.id)
            .where(
                LabelCreationIntent.run_id == run_id,
                LabelCreationIntent.status.in_(("pending", "in_flight", "uncertain")),
            )
            .limit(1)
        )
        if unresolved_label_intent is not None:
            run.status = "needs_review"
        session.commit()
    return inspect_run(session_factory, run_id)


def abandon_run(
    session_factory: sessionmaker[Session],
    run_id: int,
    reason: str,
    *,
    confirmed: bool,
    now: datetime | None = None,
) -> RunState:
    if not confirmed:
        raise ValueError("abandonment requires explicit confirmation")
    if not reason.strip():
        raise ValueError("abandonment requires a reason")
    timestamp = now or datetime.now(UTC)
    with session_factory() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError(f"unknown run id {run_id}")
        in_flight = session.scalar(
            select(Mutation.id)
            .where(Mutation.run_id == run_id, Mutation.status == "in_flight")
            .limit(1)
        )
        if in_flight is not None:
            raise ValueError("reconcile in-flight mutations before abandonment")
        unreconciled = session.scalar(
            select(Mutation.id)
            .where(
                Mutation.run_id == run_id,
                Mutation.status.in_(("uncertain", "conflict")),
                Mutation.reconciled_at.is_(None),
            )
            .limit(1)
        )
        if unreconciled is not None:
            raise ValueError("reconcile uncertain mutations before abandonment")
        unresolved_label = session.scalar(
            select(LabelCreationIntent.id)
            .where(
                LabelCreationIntent.run_id == run_id,
                LabelCreationIntent.status.in_(("in_flight", "uncertain")),
                LabelCreationIntent.reconciled_at.is_(None),
            )
            .limit(1)
        )
        if unresolved_label is not None:
            raise ValueError("reconcile uncertain label intents before abandonment")
        for mutation in session.scalars(
            select(Mutation).where(
                Mutation.run_id == run_id, Mutation.status.in_(("pending", "uncertain", "conflict"))
            )
        ).all():
            mutation.status = "abandoned"
            mutation.abandoned_at = timestamp
            mutation.abandonment_reason = reason
        for intent in session.scalars(
            select(LabelCreationIntent).where(
                LabelCreationIntent.run_id == run_id,
                LabelCreationIntent.status.in_(("pending", "in_flight", "uncertain")),
            )
        ).all():
            intent.status = "abandoned"
            intent.finished_at = timestamp
            intent.error_message = reason
        _refresh_run_counts(session, run_id, timestamp)
        run.abandoned_at = timestamp
        run.abandonment_reason = reason
        run.status = "partial" if run.applied_count else "abandoned"
        run.finished_at = timestamp
        session.commit()
    return inspect_run(session_factory, run_id)


def _run_state(run: Run) -> RunState:
    return RunState(
        id=run.id,
        run_type=run.run_type,
        status=run.status,
        source_run_id=run.source_run_id,
        intended_count=run.intended_count,
        applied_count=run.applied_count,
        failed_count=run.failed_count,
        uncertain_count=run.uncertain_count,
        conflict_count=run.conflict_count,
    )


def _json_ids(value: str | None) -> list[str]:
    import json

    return list(json.loads(value or "[]"))
