import sqlite3

import pytest
from alembic import command
from sqlalchemy import inspect, select

from mailbrain.storage import db
from mailbrain.storage.models import Label


def test_init_db_creates_file_and_tables(tmp_path):
    p = tmp_path / "state.db"
    db.init_db(p)
    assert p.exists()
    engine = db.make_engine(p)
    tables = set(inspect(engine).get_table_names())
    assert {
        "messages",
        "threads",
        "labels",
        "runs",
        "mutations",
        "digests",
        "review_queue",
        "sync_state",
        "scan_runs",
        "label_creation_intents",
        "alembic_version",
    } <= tables


def test_session_factory_roundtrip(tmp_path):
    p = tmp_path / "state.db"
    db.init_db(p)
    Session = db.session_factory(p)
    with Session() as s:
        s.add(Label(gmail_id="L1", name="Work"))
        s.commit()
    with Session() as s:
        assert s.scalar(select(Label).where(Label.name == "Work")) is not None


def test_init_db_is_idempotent(tmp_path):
    p = tmp_path / "state.db"
    db.init_db(p)
    db.init_db(p)
    assert p.exists()


def test_init_db_refuses_unversioned_legacy_database(tmp_path):
    path = tmp_path / "state.db"
    _create_legacy_database(path)
    with pytest.raises(db.SchemaUpgradeRequired, match="upgrade"):
        db.init_db(path)


def test_session_factory_refuses_stale_schema(tmp_path):
    path = tmp_path / "state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    config = db._alembic_config(path)
    command.upgrade(config, db.SCAN_LIFECYCLE_REVISION)
    with pytest.raises(db.SchemaUpgradeRequired, match=db.SCAN_LIFECYCLE_REVISION):
        db.session_factory(path)


def _create_legacy_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE labels (
            id INTEGER NOT NULL PRIMARY KEY,
            gmail_id VARCHAR NOT NULL UNIQUE,
            name VARCHAR NOT NULL
        );
        CREATE TABLE threads (
            id INTEGER NOT NULL PRIMARY KEY,
            gmail_id VARCHAR NOT NULL UNIQUE
        );
        CREATE TABLE messages (
            id INTEGER NOT NULL PRIMARY KEY,
            gmail_id VARCHAR NOT NULL UNIQUE,
            thread_gmail_id VARCHAR,
            sender VARCHAR,
            subject VARCHAR,
            snippet VARCHAR,
            history_id VARCHAR,
            internal_date DATETIME,
            label_ids VARCHAR
        );
        CREATE TABLE runs (
            id INTEGER NOT NULL PRIMARY KEY,
            started_at DATETIME,
            finished_at DATETIME,
            dry_run BOOLEAN NOT NULL,
            scanned INTEGER NOT NULL,
            labeled INTEGER NOT NULL,
            archived INTEGER NOT NULL,
            errors INTEGER NOT NULL
        );
        CREATE TABLE mutations (
            id INTEGER NOT NULL PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES runs(id),
            message_gmail_id VARCHAR NOT NULL,
            labels_before VARCHAR DEFAULT '[]' NOT NULL,
            labels_after VARCHAR DEFAULT '[]' NOT NULL,
            archived_before BOOLEAN,
            archived_after BOOLEAN,
            read_before BOOLEAN,
            read_after BOOLEAN,
            applied BOOLEAN NOT NULL
        );
        CREATE TABLE digests (
            id INTEGER NOT NULL PRIMARY KEY,
            iso_week VARCHAR NOT NULL UNIQUE,
            notion_page_id VARCHAR,
            created_at DATETIME
        );
        CREATE TABLE review_queue (
            id INTEGER NOT NULL PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES runs(id),
            message_gmail_id VARCHAR NOT NULL,
            suggested VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            reason VARCHAR,
            status VARCHAR NOT NULL
        );
        CREATE TABLE sync_state (
            id INTEGER NOT NULL PRIMARY KEY,
            last_history_id VARCHAR,
            updated_at DATETIME
        );
        INSERT INTO messages (
            id, gmail_id, subject, label_ids
        ) VALUES (7, 'legacy-message', 'Old mail', '["INBOX", "UNREAD"]');
        INSERT INTO runs (
            id, started_at, finished_at, dry_run, scanned, labeled, archived, errors
        ) VALUES (4, '2026-07-01', '2026-07-01', 0, 1, 1, 1, 0);
        INSERT INTO mutations (
            id, run_id, message_gmail_id, labels_before, labels_after,
            archived_before, archived_after, read_before, read_after, applied
        ) VALUES (
            9, 4, 'legacy-message', '["INBOX", "UNREAD"]', '["Old"]',
            1, 0, 1, 0, 1
        );
        """
    )
    connection.commit()
    connection.close()
