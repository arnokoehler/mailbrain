import json
import os
import sqlite3
import stat

import pytest
from sqlalchemy import inspect, select, text

from mailbrain.storage import db
from mailbrain.storage.models import Message, Mutation, Run
from tests.storage.test_db import _create_legacy_database


def test_fresh_database_reaches_packaged_head(tmp_path):
    path = tmp_path / "state.db"
    db.init_db(path)
    assert db.current_revision(path) == db.DIGEST_PUBLICATION_REVISION
    assert db.is_current_schema(path)
    assert set(inspect(db.make_engine(path)).get_table_names()) == {
        "alembic_version",
        "digests",
        "label_creation_intents",
        "labels",
        "messages",
        "mutations",
        "review_queue",
        "runs",
        "scan_runs",
        "sync_state",
        "threads",
    }


def test_legacy_upgrade_preserves_rows_and_marks_audit_legacy(tmp_path):
    path = tmp_path / "state.db"
    _create_legacy_database(path)
    backup = db.upgrade_db(path)
    assert backup is not None
    assert db.is_current_schema(path)
    with db.session_factory(path)() as session:
        message = session.scalar(select(Message).where(Message.id == 7))
        run = session.scalar(select(Run).where(Run.id == 4))
        mutation = session.scalar(select(Mutation).where(Mutation.id == 9))
        assert message.last_scan_id is None
        assert run.status == "legacy"
        assert run.run_type == "apply"
        assert mutation.status == "legacy"
        assert mutation.applied is True
        assert mutation.archived_before is True
        assert mutation.archived_after is False
        assert mutation.read_before is True
        assert mutation.read_after is False
        assert json.loads(mutation.labels_before) == ["INBOX", "UNREAD"]


def test_legacy_backup_is_restrictive_and_consistent(tmp_path):
    path = tmp_path / "state.db"
    _create_legacy_database(path)
    backup = db.upgrade_db(path)
    assert backup is not None
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o600
    connection = sqlite3.connect(backup)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT gmail_id FROM messages").fetchall() == [
            ("legacy-message",)
        ]
        assert "alembic_version" not in {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()


def test_repeated_upgrade_is_safe_and_does_not_create_another_backup(tmp_path):
    path = tmp_path / "state.db"
    _create_legacy_database(path)
    first_backup = db.upgrade_db(path)
    assert first_backup is not None
    assert db.upgrade_db(path) is None
    assert list(tmp_path.glob("*.bak")) == [first_backup]


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE messages ADD COLUMN unexpected VARCHAR",
        "DROP TABLE sync_state",
        "CREATE TABLE alembic_version (version_num VARCHAR NOT NULL)",
    ],
)
def test_unknown_unversioned_schema_is_refused_without_backup(tmp_path, change):
    path = tmp_path / "state.db"
    _create_legacy_database(path)
    connection = sqlite3.connect(path)
    connection.execute(change)
    connection.commit()
    connection.close()
    with pytest.raises(db.UnknownSchemaError):
        db.upgrade_db(path)
    assert not list(tmp_path.glob("*.bak"))


def test_versioned_database_with_missing_table_is_refused(tmp_path):
    path = tmp_path / "state.db"
    db.init_db(path)
    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE sync_state")
    connection.commit()
    connection.close()
    with pytest.raises(db.UnknownSchemaError, match="does not match revision"):
        db.session_factory(path)


def test_failed_backup_prevents_stamp_and_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    _create_legacy_database(path)

    def fail_backup(_path):
        raise OSError("backup unavailable")

    monkeypatch.setattr(db, "_backup_database", fail_backup)
    with pytest.raises(OSError, match="backup unavailable"):
        db.upgrade_db(path)
    assert db.current_revision(path) is None
    assert db.is_canonical_legacy_schema(path)


def test_scan_status_constraint_rejects_unknown_state(tmp_path):
    path = tmp_path / "state.db"
    db.init_db(path)
    engine = db.make_engine(path)
    with engine.begin() as connection, pytest.raises(Exception, match="ck_scan_runs_status"):
        connection.execute(
            text(
                "INSERT INTO scan_runs (query, started_at, status) "
                "VALUES ('in:inbox', '2026-09-11', 'complete-ish')"
            )
        )
