"""SQLite engine, schema migration, and session factory."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

BASELINE_REVISION = "0001_baseline"
SCAN_LIFECYCLE_REVISION = "0002_scan_lifecycle"
DURABLE_EXECUTION_REVISION = "0003_durable_execution"
RECOVERY_PROOF_REVISION = "0004_recovery_proof"
DIGEST_PUBLICATION_REVISION = "0005_digest_publication_state"


class SchemaError(RuntimeError):
    pass


class UnknownSchemaError(SchemaError):
    pass


class SchemaUpgradeRequired(SchemaError):
    pass


LEGACY_COLUMNS = {
    "digests": {"id", "iso_week", "notion_page_id", "created_at"},
    "labels": {"id", "gmail_id", "name"},
    "messages": {
        "id",
        "gmail_id",
        "thread_gmail_id",
        "sender",
        "subject",
        "snippet",
        "history_id",
        "internal_date",
        "label_ids",
    },
    "mutations": {
        "id",
        "run_id",
        "message_gmail_id",
        "labels_before",
        "labels_after",
        "archived_before",
        "archived_after",
        "read_before",
        "read_after",
        "applied",
    },
    "review_queue": {
        "id",
        "run_id",
        "message_gmail_id",
        "suggested",
        "source",
        "reason",
        "status",
    },
    "runs": {
        "id",
        "started_at",
        "finished_at",
        "dry_run",
        "scanned",
        "labeled",
        "archived",
        "errors",
    },
    "sync_state": {"id", "last_history_id", "updated_at"},
    "threads": {"id", "gmail_id"},
}
LEGACY_PRIMARY_KEYS = {table: ("id",) for table in LEGACY_COLUMNS}
LEGACY_UNIQUE_COLUMNS = {
    "digests": {("iso_week",)},
    "labels": {("gmail_id",)},
    "messages": {("gmail_id",)},
    "threads": {("gmail_id",)},
}
LEGACY_FOREIGN_KEYS = {
    "mutations": {("run_id", "runs", "id")},
    "review_queue": {("run_id", "runs", "id")},
}


def make_engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists() or _is_empty_database(db_path):
        _upgrade(db_path)
        return
    require_current_schema(db_path)


def upgrade_db(db_path: Path) -> Path | None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists() or _is_empty_database(db_path):
        _upgrade(db_path)
        return None
    revision = current_revision(db_path)
    if revision is not None:
        _require_revision_schema(db_path, revision)
        _upgrade(db_path)
        _require_revision_schema(db_path, head_revision())
        return None
    if not is_canonical_legacy_schema(db_path):
        raise UnknownSchemaError(
            "database schema is neither versioned nor the canonical legacy schema"
        )
    backup_path = _backup_database(db_path)
    config = _alembic_config(db_path)
    command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")
    return backup_path


def current_revision(db_path: Path) -> str | None:
    if not db_path.exists() or _is_empty_database(db_path):
        return None
    engine = make_engine(db_path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def head_revision() -> str:
    revision = ScriptDirectory.from_config(_alembic_config(Path(":memory:"))).get_current_head()
    if revision is None:
        raise SchemaError("packaged migrations have no head revision")
    return revision


def is_current_schema(db_path: Path) -> bool:
    revision = current_revision(db_path)
    if revision != head_revision():
        return False
    try:
        _require_revision_schema(db_path, revision)
    except UnknownSchemaError:
        return False
    return True


def require_current_schema(db_path: Path) -> None:
    revision = current_revision(db_path)
    if revision != head_revision():
        displayed = revision or "unversioned"
        raise SchemaUpgradeRequired(
            f"database schema is {displayed}; run the database upgrade command before continuing"
        )
    _require_revision_schema(db_path, revision)


def _require_revision_schema(db_path: Path, revision: str) -> None:
    expected = _revision_columns(revision)
    connection = sqlite3.connect(db_path)
    try:
        actual_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if actual_tables != set(expected) | {"alembic_version"}:
            raise UnknownSchemaError(f"database structure does not match revision {revision}")
        for table, columns in expected.items():
            actual_columns = {
                row[1] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
            if actual_columns != columns:
                raise UnknownSchemaError(f"table {table} does not match revision {revision}")
    finally:
        connection.close()


def _revision_columns(revision: str) -> dict[str, set[str]]:
    expected = {table: set(columns) for table, columns in LEGACY_COLUMNS.items()}
    if revision == BASELINE_REVISION:
        return expected
    expected["scan_runs"] = {
        "id",
        "query",
        "started_at",
        "finished_at",
        "status",
        "listed_count",
        "fetched_count",
        "failed_count",
        "error_message",
    }
    expected["messages"].add("last_scan_id")
    if revision == SCAN_LIFECYCLE_REVISION:
        return expected
    expected["runs"].update(
        {
            "run_type",
            "status",
            "scan_id",
            "source_run_id",
            "rules_hash",
            "safety_hash",
            "prepared_at",
            "error_message",
            "intended_count",
            "applied_count",
            "failed_count",
            "uncertain_count",
            "conflict_count",
            "abandoned_at",
            "abandonment_reason",
        }
    )
    expected["mutations"].update(
        {
            "status",
            "batch_id",
            "original_mutation_id",
            "matched_rule_ids",
            "gmail_label_ids_before",
            "gmail_label_ids_add",
            "gmail_label_ids_remove",
            "prepared_at",
            "started_at",
            "finished_at",
            "error_message",
            "abandoned_at",
            "abandonment_reason",
        }
    )
    expected["label_creation_intents"] = {
        "id",
        "run_id",
        "label_name",
        "status",
        "gmail_label_id",
        "prepared_at",
        "started_at",
        "finished_at",
        "error_message",
    }
    if revision == DURABLE_EXECUTION_REVISION:
        return expected
    if revision == RECOVERY_PROOF_REVISION:
        expected["mutations"].add("reconciled_at")
        expected["label_creation_intents"].add("reconciled_at")
        return expected
    if revision == DIGEST_PUBLICATION_REVISION:
        expected["mutations"].add("reconciled_at")
        expected["label_creation_intents"].add("reconciled_at")
        expected["digests"].update(
            {"status", "last_error", "last_attempt_at", "published_at"}
        )
        return expected
    raise UnknownSchemaError(f"unsupported database revision {revision}")


def is_canonical_legacy_schema(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if {row[0] for row in rows} != set(LEGACY_COLUMNS):
            return False
        for table, expected in LEGACY_COLUMNS.items():
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            if {row[1] for row in columns} != expected:
                return False
            primary_key = tuple(row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5])
            if primary_key != LEGACY_PRIMARY_KEYS[table]:
                return False
            unique_columns = {
                tuple(
                    row[2]
                    for row in connection.execute(f'PRAGMA index_info("{index[1]}")').fetchall()
                )
                for index in connection.execute(f'PRAGMA index_list("{table}")').fetchall()
                if index[2]
            }
            if unique_columns != LEGACY_UNIQUE_COLUMNS.get(table, set()):
                return False
            foreign_keys = {
                (row[3], row[2], row[4])
                for row in connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
            }
            if foreign_keys != LEGACY_FOREIGN_KEYS.get(table, set()):
                return False
        return True
    finally:
        connection.close()


def _is_empty_database(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return True
    connection = sqlite3.connect(db_path)
    try:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
            ).fetchone()
            is None
        )
    finally:
        connection.close()


def _upgrade(db_path: Path) -> None:
    command.upgrade(_alembic_config(db_path), "head")


def _alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = db_path.with_name(f"{db_path.name}.{timestamp}.bak")
    descriptor = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    except BaseException:
        destination.close()
        source.close()
        backup_path.unlink(missing_ok=True)
        raise
    destination.close()
    source.close()
    os.chmod(backup_path, 0o600)
    return backup_path


def session_factory(db_path: Path) -> sessionmaker[Session]:
    require_current_schema(db_path)
    return sessionmaker(bind=make_engine(db_path), future=True)
