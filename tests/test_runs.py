import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mailbrain.runs import abandon_run, inspect_run, list_runs, reconcile_run
from mailbrain.storage import db
from mailbrain.storage.models import Label, LabelCreationIntent, Mutation, Run
from tests.test_apply_execute import StatefulGmail


def _factory(tmp_path):
    db_path = tmp_path / "state.db"
    db.init_db(db_path)
    return db.session_factory(db_path)


def test_list_and_inspect_are_read_only(tmp_path):
    factory = _factory(tmp_path)
    with factory() as session:
        run = Run(dry_run=False, status="needs_review", intended_count=1)
        session.add(run)
        session.flush()
        session.add(Mutation(run_id=run.id, message_gmail_id="m1", status="uncertain"))
        session.commit()
        run_id = run.id

    assert [state.id for state in list_runs(factory)] == [run_id]
    assert inspect_run(factory, run_id).mutations[0].status == "uncertain"


def test_reconcile_marks_in_flight_uncertain_without_writing(tmp_path):
    factory = _factory(tmp_path)
    with factory() as session:
        run = Run(dry_run=False, status="running", intended_count=1)
        session.add(run)
        session.flush()
        session.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="m1",
                status="in_flight",
                gmail_label_ids_before=json.dumps(["INBOX"]),
                gmail_label_ids_add=json.dumps(["L1"]),
                gmail_label_ids_remove=json.dumps([]),
            )
        )
        session.commit()
        run_id = run.id
    gmail = StatefulGmail(messages={"m1": {"INBOX", "L1"}})

    state = reconcile_run(gmail, factory, run_id)

    assert state.mutations[0].status == "uncertain"
    assert gmail.batch_calls == []


def test_reconcile_resolves_visible_uncertain_label_intent(tmp_path):
    factory = _factory(tmp_path)
    with factory() as session:
        run = Run(dry_run=False, status="needs_review")
        session.add(run)
        session.flush()
        session.add(
            LabelCreationIntent(
                run_id=run.id,
                label_name="Reizen",
                status="uncertain",
                prepared_at=datetime.now(UTC),
            )
        )
        session.commit()
        run_id = run.id
    gmail = StatefulGmail(labels={"L1": "Reizen"})
    state = reconcile_run(gmail, factory, run_id)
    assert state.label_intents[0].status == "resolved"
    with factory() as session:
        assert session.scalar(select(Label)).gmail_id == "L1"


def test_reconcile_keeps_missing_label_intent_in_needs_review(tmp_path):
    factory = _factory(tmp_path)
    with factory() as session:
        run = Run(dry_run=False, status="needs_review")
        session.add(run)
        session.flush()
        session.add(
            LabelCreationIntent(
                run_id=run.id,
                label_name="Missing",
                status="uncertain",
                prepared_at=datetime.now(UTC),
            )
        )
        session.commit()
        run_id = run.id
    state = reconcile_run(StatefulGmail(labels={}), factory, run_id)
    assert state.status == "needs_review"
    assert state.label_intents[0].status == "uncertain"


def test_abandon_requires_reconciliation_confirmation_and_reason(tmp_path):
    factory = _factory(tmp_path)
    with factory() as session:
        run = Run(dry_run=False, status="needs_review", intended_count=1)
        session.add(run)
        session.flush()
        session.add(Mutation(run_id=run.id, message_gmail_id="m1", status="in_flight"))
        session.commit()
        run_id = run.id

    with pytest.raises(ValueError, match="confirmation"):
        abandon_run(factory, run_id, "handled", confirmed=False)
    with pytest.raises(ValueError, match="reconcile"):
        abandon_run(factory, run_id, "handled", confirmed=True)
    with factory() as session:
        session.scalar(select(Mutation)).status = "uncertain"
        session.commit()
    with pytest.raises(ValueError, match="reconcile uncertain"):
        abandon_run(factory, run_id, "handled manually", confirmed=True)
    reconcile_run(StatefulGmail(messages={"m1": set()}), factory, run_id)
    state = abandon_run(factory, run_id, "handled manually", confirmed=True)
    assert state.status == "abandoned"
    assert state.mutations[0].status == "abandoned"


def test_reconcile_ignores_unrelated_external_label_changes(tmp_path):
    factory = _factory(tmp_path)
    with factory() as session:
        run = Run(dry_run=False, status="needs_review", intended_count=1)
        session.add(run)
        session.flush()
        session.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="m1",
                status="uncertain",
                gmail_label_ids_before='["INBOX"]',
                gmail_label_ids_add='["L1"]',
                gmail_label_ids_remove='["INBOX"]',
            )
        )
        session.commit()
        run_id = run.id
    gmail = StatefulGmail(messages={"m1": {"L1", "EXTERNAL"}})

    state = reconcile_run(gmail, factory, run_id)

    assert state.mutations[0].status == "uncertain"
    with factory() as session:
        assert session.scalar(select(Mutation)).reconciled_at is not None


def test_in_flight_label_intent_requires_reconciliation_before_abandon(tmp_path):
    factory = _factory(tmp_path)
    with factory() as session:
        run = Run(dry_run=False, status="needs_review")
        session.add(run)
        session.flush()
        session.add(
            LabelCreationIntent(
                run_id=run.id,
                label_name="Missing",
                status="in_flight",
                prepared_at=datetime.now(UTC),
            )
        )
        session.commit()
        run_id = run.id

    with pytest.raises(ValueError, match="label intents"):
        abandon_run(factory, run_id, "handled", confirmed=True)
    reconcile_run(StatefulGmail(labels={}), factory, run_id)
    state = abandon_run(factory, run_id, "handled", confirmed=True)
    assert state.label_intents[0].status == "abandoned"
