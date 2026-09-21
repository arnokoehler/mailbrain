"""Audited inverse execution for confirmed durable apply mutations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.apply import _execute_batches, assert_no_unresolved_intents
from mailbrain.config import SafetySettings
from mailbrain.storage.models import Mutation, Run, ScanRun


class SupportsRollback(Protocol):
    def get_metadata(self, message_id: str) -> dict[str, Any]: ...
    def batch_modify(
        self, message_ids: list[str], add_label_ids: list[str], remove_label_ids: list[str]
    ) -> None: ...
    def list_message_ids(self, query: str) -> list[str]: ...


class RollbackConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class RollbackPlan:
    source_run_id: int
    mutation_ids: tuple[int, ...]
    message_ids: tuple[str, ...]
    archive_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RollbackValidation:
    mutation_count: int
    archive_count: int
    inbox_count: int
    archive_fraction: float
    reasons: tuple[str, ...]

    @property
    def writes_allowed(self) -> bool:
        return not self.reasons


def plan_rollback(
    client: SupportsRollback,
    session_factory: sessionmaker[Session],
    run_id: int,
) -> RollbackPlan:
    with session_factory() as session:
        source = session.get(Run, run_id)
        if source is None:
            raise ValueError(f"unknown run id {run_id}")
        blocking_mutation = session.scalar(
            select(Mutation.id)
            .where(
                Mutation.run_id != run_id,
                Mutation.status.in_(("pending", "in_flight", "uncertain", "conflict")),
            )
            .limit(1)
        )
        if blocking_mutation is not None:
            raise RollbackConflictError("another unresolved run blocks rollback planning")
        if source.run_type != "apply" or source.status == "legacy":
            return RollbackPlan(run_id, (), ())
        already_reversed = set(
            session.scalars(
                select(Mutation.original_mutation_id)
                .join(Run, Run.id == Mutation.run_id)
                .where(Run.run_type == "rollback", Mutation.status == "applied")
            ).all()
        )
        candidates = [
            mutation
            for mutation in session.scalars(
                select(Mutation)
                .where(Mutation.run_id == run_id, Mutation.status == "applied")
                .order_by(Mutation.id)
            ).all()
            if mutation.id not in already_reversed
            and mutation.gmail_label_ids_before is not None
            and mutation.gmail_label_ids_add is not None
            and mutation.gmail_label_ids_remove is not None
        ]
        candidate_ids = {mutation.id for mutation in candidates}
        affected_by_message = {
            mutation.message_gmail_id: set(json.loads(mutation.gmail_label_ids_add or "[]"))
            | set(json.loads(mutation.gmail_label_ids_remove or "[]"))
            for mutation in candidates
        }
        later_query = (
            select(Mutation)
            .join(Run, Run.id == Mutation.run_id)
            .where(Mutation.status == "applied", Run.id > run_id)
        )
        if candidate_ids:
            later_query = later_query.where(Mutation.id.not_in(candidate_ids))
        later = list(session.scalars(later_query).all())
        for mutation in later:
            affected = affected_by_message.get(mutation.message_gmail_id)
            if affected is None:
                continue
            later_delta = set(json.loads(mutation.gmail_label_ids_add or "[]")) | set(
                json.loads(mutation.gmail_label_ids_remove or "[]")
            )
            if affected & later_delta:
                raise RollbackConflictError(
                    f"later mutation overlaps affected labels for {mutation.message_gmail_id}"
                )
        snapshots = [
            (
                mutation,
                set(client.get_metadata(mutation.message_gmail_id).get("label_ids", [])),
            )
            for mutation in candidates
        ]
        for mutation, live_ids in snapshots:
            before_ids = set(json.loads(mutation.gmail_label_ids_before or "[]"))
            added_ids = set(json.loads(mutation.gmail_label_ids_add or "[]"))
            removed_ids = set(json.loads(mutation.gmail_label_ids_remove or "[]"))
            expected_ids = (before_ids | added_ids) - removed_ids
            affected_ids = set(json.loads(mutation.gmail_label_ids_add or "[]")) | set(
                json.loads(mutation.gmail_label_ids_remove or "[]")
            )
            if live_ids & affected_ids != expected_ids & affected_ids:
                raise RollbackConflictError(
                    f"live affected-label state changed for {mutation.message_gmail_id}"
                )
        return RollbackPlan(
            source_run_id=run_id,
            mutation_ids=tuple(mutation.id for mutation in candidates),
            message_ids=tuple(mutation.message_gmail_id for mutation in candidates),
            archive_message_ids=tuple(
                mutation.message_gmail_id
                for mutation in candidates
                if "INBOX" in set(json.loads(mutation.gmail_label_ids_add or "[]"))
            ),
        )


def validate_rollback_plan(
    plan: RollbackPlan,
    settings: SafetySettings,
    inbox_message_ids: list[str] | None,
) -> RollbackValidation:
    mutation_count = len(set(plan.message_ids))
    archive_count = len(set(plan.archive_message_ids))
    inbox_count = len(set(inbox_message_ids or []))
    archive_fraction = archive_count / inbox_count if inbox_count else 0.0
    reasons: list[str] = []
    if settings.max_mutations is None:
        reasons.append("missing safety limit max_mutations")
    elif mutation_count > settings.max_mutations:
        reasons.append(
            f"rollback mutations {mutation_count} exceed configured limit {settings.max_mutations}"
        )
    if archive_count:
        if settings.max_archives is None:
            reasons.append("missing safety limit max_archives")
        elif archive_count > settings.max_archives:
            reasons.append(
                f"rollback archives {archive_count} exceed configured limit {settings.max_archives}"
            )
        if settings.max_archive_fraction is None:
            reasons.append("missing safety limit max_archive_fraction")
        elif inbox_count == 0:
            reasons.append("rollback archive fraction requires a nonzero fresh INBOX count")
        elif archive_fraction > settings.max_archive_fraction:
            reasons.append(
                "rollback archive fraction "
                f"{archive_fraction:.3f} exceeds configured limit {settings.max_archive_fraction}"
            )
    return RollbackValidation(
        mutation_count, archive_count, inbox_count, archive_fraction, tuple(reasons)
    )


def rollback_run(
    client: SupportsRollback,
    session_factory: sessionmaker[Session],
    run_id: int,
    *,
    dry_run: bool = False,
    confirmed: bool = True,
    batch_size: int = 1000,
    now: datetime | None = None,
) -> int:
    plan = plan_rollback(client, session_factory, run_id)
    if dry_run or not plan.mutation_ids:
        return len(plan.mutation_ids)
    if not confirmed:
        return 0
    timestamp = now or datetime.now(UTC)
    rollback_run_id = _prepare_rollback(session_factory, plan, timestamp, batch_size)
    _execute_batches(client, session_factory, rollback_run_id, timestamp)
    return len(plan.mutation_ids)


def _prepare_rollback(
    session_factory: sessionmaker[Session],
    plan: RollbackPlan,
    timestamp: datetime,
    batch_size: int,
) -> int:
    if batch_size <= 0 or batch_size > 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    with session_factory() as session:
        assert_no_unresolved_intents(session)
        run = Run(
            dry_run=False,
            run_type="rollback",
            status="prepared",
            source_run_id=plan.source_run_id,
            started_at=timestamp,
            prepared_at=timestamp,
            intended_count=len(plan.mutation_ids),
        )
        session.add(run)
        session.flush()
        latest_scan = session.scalar(
            select(ScanRun)
            .where(ScanRun.status == "succeeded")
            .order_by(ScanRun.id.desc())
            .limit(1)
        )
        if latest_scan is not None:
            latest_scan.status = "invalidated"
        sources = list(
            session.scalars(select(Mutation).where(Mutation.id.in_(plan.mutation_ids))).all()
        )
        grouped: dict[tuple[tuple[str, ...], tuple[str, ...]], list[Mutation]] = {}
        for source in sources:
            inverse_add = tuple(sorted(json.loads(source.gmail_label_ids_remove or "[]")))
            inverse_remove = tuple(sorted(json.loads(source.gmail_label_ids_add or "[]")))
            grouped.setdefault((inverse_add, inverse_remove), []).append(source)
        batch_number = 0
        for key in sorted(grouped):
            sources_for_delta = grouped[key]
            for offset in range(0, len(sources_for_delta), batch_size):
                batch_number += 1
                for source in sources_for_delta[offset : offset + batch_size]:
                    before_ids = set(json.loads(source.gmail_label_ids_before or "[]"))
                    added_ids = set(json.loads(source.gmail_label_ids_add or "[]"))
                    removed_ids = set(json.loads(source.gmail_label_ids_remove or "[]"))
                    applied_ids = (before_ids | added_ids) - removed_ids
                    session.add(
                        Mutation(
                            run_id=run.id,
                            message_gmail_id=source.message_gmail_id,
                            labels_before=source.labels_after,
                            labels_after=source.labels_before,
                            archived_before=source.archived_after,
                            archived_after=source.archived_before,
                            read_before=source.read_after,
                            read_after=source.read_before,
                            applied=False,
                            status="pending",
                            batch_id=f"{run.id}:{batch_number}",
                            original_mutation_id=source.id,
                            matched_rule_ids=source.matched_rule_ids,
                            gmail_label_ids_before=json.dumps(sorted(applied_ids)),
                            gmail_label_ids_add=json.dumps(list(key[0])),
                            gmail_label_ids_remove=json.dumps(list(key[1])),
                            prepared_at=timestamp,
                        )
                    )
        session.commit()
        return run.id
