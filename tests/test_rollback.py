import json

import pytest
from sqlalchemy import select

from mailbrain.apply import execute_plan
from mailbrain.config import SafetySettings
from mailbrain.planner import PlannedMutation
from mailbrain.rollback import (
    RollbackConflictError,
    RollbackPlan,
    plan_rollback,
    rollback_run,
    validate_rollback_plan,
)
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message, Mutation, Run
from tests.test_apply_execute import StatefulGmail


def _factory(tmp_path):
    db_path = tmp_path / "state.db"
    db.init_db(db_path)
    factory = db.session_factory(db_path)
    with factory() as session:
        session.add_all(
            [
                Label(gmail_id="INBOX", name="INBOX"),
                Label(gmail_id="L_reizen", name="Reizen"),
                Message(gmail_id="m1", label_ids=json.dumps(["INBOX", "External"])),
            ]
        )
        session.commit()
    return factory


def _applied(factory, gmail):
    return execute_plan(
        gmail,
        factory,
        [PlannedMutation("m1", ("Reizen",), True, False)],
        {"m1": {"INBOX", "External"}},
    )


def test_apply_rollback_preserves_external_labels_and_second_is_noop(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(
        labels={"INBOX": "INBOX", "External": "External", "L_reizen": "Reizen"},
        messages={"m1": {"INBOX", "External"}},
    )
    source_run_id = _applied(factory, gmail)
    gmail.messages["m1"].add("External2")

    assert rollback_run(gmail, factory, source_run_id) == 1
    assert gmail.messages["m1"] == {"INBOX", "External", "External2"}
    assert rollback_run(gmail, factory, source_run_id) == 0
    assert len(gmail.batch_calls) == 2
    with factory() as session:
        message = session.scalar(select(Message))
        rollback_mutation = session.scalar(
            select(Mutation).where(Mutation.original_mutation_id.is_not(None))
        )
        assert message is not None
        assert set(json.loads(message.label_ids)) == {"INBOX", "External", "External2"}
        assert rollback_mutation is not None and rollback_mutation.status == "applied"


def test_unknown_run_is_error(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(messages={"m1": {"INBOX"}})
    with pytest.raises(ValueError, match="unknown run id"):
        rollback_run(gmail, factory, 999)


def test_legacy_and_uncertain_mutations_are_not_eligible(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(messages={"m1": {"INBOX"}})
    with factory() as session:
        legacy = Run(dry_run=False)
        session.add(legacy)
        session.flush()
        session.add(Mutation(run_id=legacy.id, message_gmail_id="m1", applied=True))
        session.commit()
        legacy_id = legacy.id
    assert plan_rollback(gmail, factory, legacy_id).mutation_ids == ()


def test_manual_affected_label_change_blocks_whole_plan(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(
        labels={"INBOX": "INBOX", "External": "External", "L_reizen": "Reizen"},
        messages={"m1": {"INBOX", "External"}},
    )
    source_run_id = _applied(factory, gmail)
    gmail.messages["m1"].discard("L_reizen")

    with pytest.raises(RollbackConflictError):
        rollback_run(gmail, factory, source_run_id)

    assert len(gmail.batch_calls) == 1


def test_later_overlapping_applied_mutation_blocks_whole_plan(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(
        labels={"INBOX": "INBOX", "External": "External", "L_reizen": "Reizen"},
        messages={"m1": {"INBOX", "External"}},
    )
    source_run_id = _applied(factory, gmail)
    with factory() as session:
        later_run = Run(dry_run=False, run_type="apply", status="succeeded")
        session.add(later_run)
        session.flush()
        session.add(
            Mutation(
                run_id=later_run.id,
                message_gmail_id="m1",
                status="applied",
                gmail_label_ids_before=json.dumps(["External", "L_reizen"]),
                gmail_label_ids_add=json.dumps([]),
                gmail_label_ids_remove=json.dumps(["L_reizen"]),
            )
        )
        session.commit()

    with pytest.raises(RollbackConflictError, match="overlaps"):
        rollback_run(gmail, factory, source_run_id)

    assert len(gmail.batch_calls) == 1


def test_rollback_archive_budget_counts_inverse_inbox_removal():
    plan = RollbackPlan(1, (1,), ("m1",), ("m1",))
    validation = validate_rollback_plan(
        plan,
        SafetySettings(
            max_mutations=1,
            max_archives=0,
            max_archive_fraction=1.0,
            max_scan_age_minutes=60,
        ),
        ["m1"],
    )
    assert not validation.writes_allowed
    assert "rollback archives 1 exceed configured limit 0" in validation.reasons


def test_rollback_plan_marks_inverse_inbox_removal_as_archive(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(messages={"m1": {"INBOX"}})
    with factory() as session:
        run = Run(dry_run=False, run_type="apply", status="succeeded")
        session.add(run)
        session.flush()
        session.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="m1",
                status="applied",
                gmail_label_ids_before="[]",
                gmail_label_ids_add='["INBOX"]',
                gmail_label_ids_remove="[]",
            )
        )
        session.commit()
        run_id = run.id

    plan = plan_rollback(gmail, factory, run_id)

    assert plan.archive_message_ids == ("m1",)
