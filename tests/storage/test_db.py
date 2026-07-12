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
    db.init_db(p)  # second call must not raise
    assert p.exists()
