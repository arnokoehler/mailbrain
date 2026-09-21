"""Fetch Gmail metadata into explicit durable scan scopes."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.rules.models import MessageMeta
from mailbrain.storage.models import Label, Message, ScanRun

logger = logging.getLogger(__name__)


class ScanIncompleteError(RuntimeError):
    pass


class ScanNotEligibleError(RuntimeError):
    pass


class ScanDriftError(RuntimeError):
    pass


class SupportsGmailReads(Protocol):
    def list_message_ids(self, query: str) -> list[str]: ...
    def get_metadata(self, message_id: str) -> dict[str, Any]: ...
    def list_labels(self) -> dict[str, str]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _database_datetime(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _upsert_labels(session: Session, id_to_name: dict[str, str]) -> None:
    existing = {label.gmail_id: label for label in session.scalars(select(Label)).all()}
    for gmail_id, name in id_to_name.items():
        row = existing.get(gmail_id)
        if row is None:
            session.add(Label(gmail_id=gmail_id, name=name))
        else:
            row.name = name


def _upsert_message(
    session: Session, listed_message_id: str, meta: dict[str, Any], scan_id: int
) -> None:
    fetched_message_id = meta["gmail_id"]
    if fetched_message_id != listed_message_id:
        raise ValueError(
            f"Gmail returned message {fetched_message_id!r} for requested id {listed_message_id!r}"
        )
    row = session.scalar(select(Message).where(Message.gmail_id == fetched_message_id))
    if row is None:
        row = Message(gmail_id=fetched_message_id)
        session.add(row)
    row.thread_gmail_id = meta.get("thread_id")
    row.sender = meta.get("sender")
    row.subject = meta.get("subject")
    row.snippet = meta.get("snippet")
    row.label_ids = json.dumps(meta.get("label_ids", []))
    row.internal_date = datetime.fromtimestamp(meta["internal_date_ms"] / 1000, tz=UTC)
    row.last_scan_id = scan_id


def _create_scan(
    session_factory: sessionmaker[Session], query: str, started_at: datetime
) -> int:
    with session_factory() as session:
        scan = ScanRun(query=query, started_at=_database_datetime(started_at), status="running")
        session.add(scan)
        session.commit()
        return scan.id


def _fail_scan(
    session_factory: sessionmaker[Session],
    scan_id: int,
    error: BaseException,
    finished_at: datetime,
    *,
    failed_count: int | None = None,
) -> None:
    with session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            raise RuntimeError(f"scan {scan_id} disappeared while recording failure") from error
        scan.status = "failed"
        scan.finished_at = _database_datetime(finished_at)
        scan.error_message = str(error)
        if failed_count is not None:
            scan.failed_count = failed_count
        elif scan.failed_count == 0:
            scan.failed_count = 1
        session.commit()


def scan_mailbox(
    client: SupportsGmailReads,
    query: str,
    session_factory: sessionmaker[Session],
    batch_size: int = 200,
    skip_cached: bool = False,
    now: Callable[[], datetime] = _utc_now,
) -> int:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    scan_id = _create_scan(session_factory, query, now())
    if skip_cached:
        logger.info("Resume requested; performing a full fresh scan")
    logger.info("Listing message ids for query %r ...", query)
    try:
        ids = list(dict.fromkeys(client.list_message_ids(query)))
    except BaseException as exc:
        _fail_scan(session_factory, scan_id, exc, now())
        raise

    total = len(ids)
    with session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            raise RuntimeError(f"scan {scan_id} disappeared before metadata fetching")
        scan.listed_count = total
        session.commit()

    try:
        labels = client.list_labels()
    except BaseException as exc:
        _fail_scan(session_factory, scan_id, exc, now())
        raise

    logger.info("Found %d messages and %d labels; fetching metadata ...", total, len(labels))
    with session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        if scan is None:
            raise RuntimeError(f"scan {scan_id} disappeared before metadata fetching")
        _upsert_labels(session, labels)
        session.commit()

        fetched = 0
        failures: list[tuple[str, Exception]] = []
        try:
            for index, message_id in enumerate(ids, 1):
                try:
                    with session.begin_nested():
                        meta = client.get_metadata(message_id)
                        _upsert_message(session, message_id, meta, scan_id)
                except Exception as exc:
                    scan = session.get(ScanRun, scan_id)
                    if scan is None:
                        raise RuntimeError(
                            f"scan {scan_id} disappeared while recording progress"
                        ) from exc
                    failures.append((message_id, exc))
                    scan.failed_count = len(failures)
                    logger.warning("failed to fetch message %s: %s", message_id, exc)
                    continue
                fetched += 1
                scan.fetched_count = fetched
                if fetched % batch_size == 0:
                    session.commit()
                    logger.info("cached %d messages (%d/%d scanned)", fetched, index, total)
            if failures:
                error = ScanIncompleteError(
                    f"scan {scan_id} incomplete: {len(failures)} message fetch(es) failed"
                )
                scan.status = "failed"
                scan.finished_at = _database_datetime(now())
                scan.error_message = "; ".join(
                    f"{message_id}: {failure}" for message_id, failure in failures
                )
                session.commit()
                raise error
            refreshed_labels = client.list_labels()
            _upsert_labels(session, refreshed_labels)
            known_label_ids = set(refreshed_labels)
            unknown_label_ids = sorted(
                {
                    label_id
                    for row in session.scalars(
                        select(Message).where(Message.last_scan_id == scan_id)
                    ).all()
                    for label_id in json.loads(row.label_ids or "[]")
                    if label_id not in known_label_ids
                }
            )
            if unknown_label_ids:
                error = ScanIncompleteError(
                    f"scan {scan_id} contains unknown label ids: {', '.join(unknown_label_ids)}"
                )
                scan.status = "failed"
                scan.finished_at = _database_datetime(now())
                scan.error_message = str(error)
                scan.failed_count = max(scan.failed_count, 1)
                session.commit()
                raise error
            scan.status = "succeeded"
            scan.finished_at = _database_datetime(now())
            session.commit()
        except BaseException as exc:
            if isinstance(exc, ScanIncompleteError):
                raise
            session.rollback()
            _fail_scan(
                session_factory,
                scan_id,
                exc,
                now(),
                failed_count=len(failures) or 1,
            )
            raise
    logger.info("Done: cached %d messages in successful scan %d", fetched, scan_id)
    return fetched


def latest_scan_run(session_factory: sessionmaker[Session]) -> ScanRun | None:
    with session_factory() as session:
        return session.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))


def require_eligible_scan(
    session_factory: sessionmaker[Session],
    scan_id: int,
    max_age_minutes: int,
    *,
    now: Callable[[], datetime] = _utc_now,
) -> ScanRun:
    if max_age_minutes <= 0:
        raise ValueError("max_age_minutes must be positive")
    with session_factory() as session:
        latest = session.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))
        if latest is None:
            raise ScanNotEligibleError("no scan is available")
        if latest.id != scan_id:
            raise ScanNotEligibleError(
                f"scan {scan_id} is not the latest scan; "
                f"latest scan is {latest.id} ({latest.status})"
            )
        if latest.status != "succeeded":
            raise ScanNotEligibleError(
                f"latest scan {scan_id} has status {latest.status}, not succeeded"
            )
        age = _aware_utc(now()) - _aware_utc(latest.started_at)
        if age > timedelta(minutes=max_age_minutes):
            age_minutes = age.total_seconds() / 60
            raise ScanNotEligibleError(
                f"latest scan {scan_id} is too old for writes ({age_minutes:.1f} minutes)"
            )
        session.expunge(latest)
        return latest


def load_cached(
    session_factory: sessionmaker[Session], scan_id: int | None = None
) -> tuple[list[MessageMeta], dict[str, set[str]]]:
    with session_factory() as session:
        if scan_id is None:
            latest = session.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))
            if latest is None or latest.status != "succeeded":
                raise ScanNotEligibleError("the latest scan is not a complete successful scan")
            scan_id = latest.id
        id_to_name = {label.gmail_id: label.name for label in session.scalars(select(Label)).all()}
        messages: list[MessageMeta] = []
        current: dict[str, set[str]] = {}
        rows = session.scalars(select(Message).where(Message.last_scan_id == scan_id)).all()
        for row in rows:
            label_ids: list[str] = json.loads(row.label_ids) if row.label_ids else []
            names = {id_to_name.get(label_id, label_id) for label_id in label_ids}
            current[row.gmail_id] = names
            raw_date = row.internal_date
            if raw_date is None:
                internal_date = datetime.fromtimestamp(0, tz=UTC)
            elif raw_date.tzinfo is None:
                internal_date = raw_date.replace(tzinfo=UTC)
            else:
                internal_date = raw_date
            messages.append(
                MessageMeta(
                    gmail_id=row.gmail_id,
                    sender=row.sender or "",
                    subject=row.subject or "",
                    internal_date=internal_date,
                    current_labels=tuple(sorted(names)),
                )
            )
    return messages, current


def validate_live_candidates(
    client: SupportsGmailReads,
    session_factory: sessionmaker[Session],
    scan_id: int,
    message_ids: list[str],
) -> None:
    with session_factory() as session:
        rows = {
            row.gmail_id: row
            for row in session.scalars(
                select(Message).where(
                    Message.last_scan_id == scan_id,
                    Message.gmail_id.in_(message_ids),
                )
            ).all()
        }
        expected = {
            gmail_id: {
                "gmail_id": row.gmail_id,
                "thread_id": row.thread_gmail_id,
                "snippet": row.snippet,
                "sender": row.sender or "",
                "subject": row.subject or "",
                "label_ids": sorted(json.loads(row.label_ids or "[]")),
                "internal_date_ms": int(_aware_utc(row.internal_date).timestamp() * 1000)
                if row.internal_date is not None
                else 0,
            }
            for gmail_id, row in rows.items()
        }
    missing = sorted(set(message_ids) - expected.keys())
    changed: list[str] = []
    for message_id in message_ids:
        if message_id in missing:
            continue
        try:
            live = client.get_metadata(message_id)
        except BaseException as exc:
            raise ScanDriftError(
                f"live revalidation failed for {message_id}; run a fresh scan"
            ) from exc
        normalized = {
            "gmail_id": live.get("gmail_id"),
            "thread_id": live.get("thread_id"),
            "snippet": live.get("snippet"),
            "sender": live.get("sender", ""),
            "subject": live.get("subject", ""),
            "label_ids": sorted(live.get("label_ids", [])),
            "internal_date_ms": int(live.get("internal_date_ms", 0)),
        }
        if normalized != expected[message_id]:
            changed.append(message_id)
    blocked = sorted(set(missing + changed))
    if blocked:
        raise ScanDriftError(
            f"live Gmail state changed for {', '.join(blocked)}; run a fresh scan"
        )
