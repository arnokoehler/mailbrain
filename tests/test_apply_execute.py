import json
from unittest.mock import MagicMock

from sqlalchemy import select

from mailbrain.apply import execute_plan
from mailbrain.planner import PlannedMutation
from mailbrain.storage import db
from mailbrain.storage.models import Label, Mutation, Run


def _factory(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(Label(gmail_id="UNREAD", name="UNREAD"))
        s.add(Label(gmail_id="L_reizen", name="Reizen"))
        s.commit()
    return factory


def test_execute_plan_calls_batch_modify_and_records(tmp_path):
    factory = _factory(tmp_path)
    client = MagicMock()
    plans = [
        PlannedMutation(gmail_id="m1", add_labels=("Reizen",), archive=True, mark_read=False),
    ]
    current = {"m1": {"INBOX", "Reizen"}}

    run_id = execute_plan(client, factory, plans, current)

    args, kwargs = client.batch_modify.call_args
    assert kwargs["message_ids"] == ["m1"] or args[0] == ["m1"]
    with factory() as s:
        run = s.get(Run, run_id)
        assert run is not None and run.dry_run is False
        mut = s.scalar(select(Mutation).where(Mutation.message_gmail_id == "m1"))
        assert mut is not None
        assert mut.applied is True
        assert mut.archived_before is True
        assert mut.archived_after is False
        assert set(json.loads(mut.labels_before)) == {"INBOX", "Reizen"}
        assert set(json.loads(mut.labels_after)) == {"Reizen"}


def test_execute_plan_creates_missing_label(tmp_path):
    factory = _factory(tmp_path)
    client = MagicMock()
    client.create_label.return_value = "L_new"
    plans = [
        PlannedMutation(
            gmail_id="m2", add_labels=("Administratie",), archive=False, mark_read=False
        ),
    ]
    current = {"m2": {"INBOX"}}

    execute_plan(client, factory, plans, current)

    client.create_label.assert_called_once_with("Administratie")
    _, kwargs = client.batch_modify.call_args
    assert "L_new" in kwargs.get("add_label_ids", [])


def test_execute_plan_empty_plans_no_calls(tmp_path):
    factory = _factory(tmp_path)
    client = MagicMock()
    run_id = execute_plan(client, factory, [], {})
    client.batch_modify.assert_not_called()
    with factory() as s:
        assert s.get(Run, run_id) is not None
