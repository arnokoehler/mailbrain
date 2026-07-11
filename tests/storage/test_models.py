import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mailbrain.storage.models import Base, Digest, Label, Message, Mutation, Run


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_metadata_has_all_tables():
    names = set(Base.metadata.tables.keys())
    assert {
        "messages",
        "threads",
        "labels",
        "runs",
        "mutations",
        "digests",
        "review_queue",
        "sync_state",
    } <= names


def test_insert_and_query_label():
    engine = _engine()
    with Session(engine) as s:
        s.add(Label(gmail_id="Label_1", name="Reizen"))
        s.commit()
    with Session(engine) as s:
        found = s.scalar(select(Label).where(Label.name == "Reizen"))
        assert found is not None
        assert found.gmail_id == "Label_1"


def test_mutation_links_to_run():
    engine = _engine()
    with Session(engine) as s:
        run = Run(dry_run=True)
        s.add(run)
        s.flush()
        s.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="msg_1",
                labels_before="[]",
                labels_after='["Label_1"]',
            )
        )
        s.commit()
    with Session(engine) as s:
        m = s.scalar(select(Mutation))
        assert m is not None
        assert m.message_gmail_id == "msg_1"
        assert m.applied is False


def test_message_defaults():
    engine = _engine()
    with Session(engine) as s:
        s.add(Message(gmail_id="m1"))
        s.commit()
    with Session(engine) as s:
        m = s.scalar(select(Message))
        assert m is not None
        assert m.subject is None
        assert m.history_id is None


def test_digest_iso_week_unique():
    engine = _engine()
    with Session(engine) as s:
        s.add(Digest(iso_week="2026-W28"))
        s.commit()
    with Session(engine) as s:
        s.add(Digest(iso_week="2026-W28"))
        with pytest.raises(IntegrityError):
            s.commit()
