import json

import pytest
from sqlalchemy import select

from mailbrain.apply import UnresolvedIntentError, execute_plan, is_definite_response_failure
from mailbrain.planner import PlannedMutation
from mailbrain.storage import db
from mailbrain.storage.models import Label, LabelCreationIntent, Message, Mutation, Run


class ResponseError(RuntimeError):
    status_code = 400


class ServerResponseError(RuntimeError):
    status_code = 503


class StatefulGmail:
    def __init__(self, labels=None, messages=None):
        self.labels = dict(
            labels or {"INBOX": "INBOX", "UNREAD": "UNREAD", "External": "External"}
        )
        self.messages = {key: set(value) for key, value in (messages or {}).items()}
        self.batch_calls = []
        self.create_calls = []
        self.fail_batch = None
        self.ambiguous_batch = None
        self.ambiguous_create = False
        self.before_first_write = None

    def list_labels(self):
        return dict(self.labels)

    def create_label(self, name):
        self.create_calls.append(name)
        gmail_id = f"L_{len(self.labels)}"
        self.labels[gmail_id] = name
        if self.ambiguous_create:
            self.ambiguous_create = False
            raise TimeoutError("response lost")
        return gmail_id

    def batch_modify(self, message_ids, add_label_ids, remove_label_ids):
        if self.before_first_write is not None and not self.batch_calls:
            self.before_first_write()
        self.batch_calls.append((list(message_ids), list(add_label_ids), list(remove_label_ids)))
        call_number = len(self.batch_calls)
        if call_number == self.fail_batch:
            raise ResponseError("rejected")
        for message_id in message_ids:
            self.messages.setdefault(message_id, set()).update(add_label_ids)
            self.messages[message_id].difference_update(remove_label_ids)
        if call_number == self.ambiguous_batch:
            raise TimeoutError("response lost")

    def get_metadata(self, message_id):
        return {"gmail_id": message_id, "label_ids": sorted(self.messages[message_id])}


def _factory(tmp_path, message_ids=("m1",)):
    db_path = tmp_path / "state.db"
    db.init_db(db_path)
    factory = db.session_factory(db_path)
    with factory() as session:
        session.add_all(
            [
                Label(gmail_id="INBOX", name="INBOX"),
                Label(gmail_id="UNREAD", name="UNREAD"),
                Label(gmail_id="L_reizen", name="Reizen"),
            ]
        )
        session.add_all(
            Message(gmail_id=message_id, label_ids=json.dumps(["INBOX", "External"]))
            for message_id in message_ids
        )
        session.commit()
    return factory


def test_complete_intent_is_visible_before_first_message_write(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(
        labels={
            "INBOX": "INBOX",
            "UNREAD": "UNREAD",
            "External": "External",
            "L_reizen": "Reizen",
        },
        messages={"m1": {"INBOX", "External"}},
    )

    def inspect_intent():
        with factory() as session:
            run = session.scalar(select(Run))
            mutation = session.scalar(select(Mutation))
            assert run is not None and run.status == "running"
            assert mutation is not None and mutation.status == "in_flight"
            assert mutation.gmail_label_ids_before is not None
            assert mutation.gmail_label_ids_add == '["L_reizen"]'
            assert mutation.gmail_label_ids_remove == '["INBOX"]'

    gmail.before_first_write = inspect_intent
    run_id = execute_plan(
        gmail,
        factory,
        [PlannedMutation("m1", ("Reizen",), True, False, ("travel",))],
        {"m1": {"INBOX", "External"}},
    )

    with factory() as session:
        run = session.get(Run, run_id)
        mutation = session.scalar(select(Mutation))
        message = session.scalar(select(Message))
        assert run is not None and run.status == "succeeded"
        assert mutation is not None and mutation.status == "applied"
        assert json.loads(mutation.matched_rule_ids) == ["travel"]
        assert message is not None
        assert set(json.loads(message.label_ids)) == {"External", "L_reizen"}


def test_definite_second_batch_failure_stops_later_batches(tmp_path):
    message_ids = tuple(f"m{index:04}" for index in range(2001))
    factory = _factory(tmp_path, message_ids)
    gmail = StatefulGmail(messages={message_id: {"INBOX"} for message_id in message_ids})
    gmail.fail_batch = 2
    plans = [PlannedMutation(message_id, (), True, False) for message_id in message_ids]

    with pytest.raises(ResponseError):
        execute_plan(gmail, factory, plans, {message_id: {"INBOX"} for message_id in message_ids})

    assert [len(call[0]) for call in gmail.batch_calls] == [1000, 1000]
    with factory() as session:
        statuses = list(session.scalars(select(Mutation.status)).all())
        assert statuses.count("applied") == 1000
        assert statuses.count("failed") == 1000
        assert statuses.count("pending") == 1


def test_ambiguous_write_is_not_retried_and_remains_uncertain(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(messages={"m1": {"INBOX"}})
    gmail.ambiguous_batch = 1

    with pytest.raises(TimeoutError):
        execute_plan(
            gmail,
            factory,
            [PlannedMutation("m1", (), True, False)],
            {"m1": {"INBOX"}},
        )

    assert len(gmail.batch_calls) == 1
    with factory() as session:
        assert session.scalar(select(Mutation)).status == "uncertain"
        assert session.scalar(select(Run)).status == "needs_review"


def test_status_commit_failure_leaves_durable_in_flight_intent(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(messages={"m1": {"INBOX"}})

    def fail_after_write(message_id):
        raise RuntimeError("metadata unavailable after write")

    gmail.get_metadata = fail_after_write
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        execute_plan(
            gmail,
            factory,
            [PlannedMutation("m1", (), True, False)],
            {"m1": {"INBOX"}},
        )

    with factory() as session:
        mutation = session.scalar(select(Mutation))
        assert mutation is not None and mutation.status == "in_flight"


def test_uncertain_label_creation_relists_and_reuses_created_label(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(messages={"m1": {"INBOX"}})
    gmail.ambiguous_create = True

    execute_plan(
        gmail,
        factory,
        [PlannedMutation("m1", ("Administration",), False, False)],
        {"m1": {"INBOX"}},
    )

    assert gmail.create_calls == ["Administration"]
    with factory() as session:
        intent = session.scalar(select(LabelCreationIntent))
        assert intent is not None and intent.status == "resolved"
        assert intent.gmail_label_id in gmail.messages["m1"]


def test_2001_mutations_use_stable_1000_1000_1_batches(tmp_path):
    message_ids = tuple(f"m{index:04}" for index in range(2001))
    factory = _factory(tmp_path, message_ids)
    gmail = StatefulGmail(messages={message_id: {"INBOX"} for message_id in message_ids})

    execute_plan(
        gmail,
        factory,
        [PlannedMutation(message_id, (), True, False) for message_id in message_ids],
        {message_id: {"INBOX"} for message_id in message_ids},
    )

    assert [len(call[0]) for call in gmail.batch_calls] == [1000, 1000, 1]


def test_unresolved_intent_blocks_independent_writer(tmp_path):
    factory = _factory(tmp_path)
    gmail = StatefulGmail(messages={"m1": {"INBOX"}})
    with factory() as session:
        run = Run(dry_run=False, status="needs_review")
        session.add(run)
        session.flush()
        session.add(Mutation(run_id=run.id, message_gmail_id="old", status="uncertain"))
        session.commit()

    with pytest.raises(UnresolvedIntentError):
        execute_plan(
            gmail,
            factory,
            [PlannedMutation("m1", (), True, False)],
            {"m1": {"INBOX"}},
        )

    assert gmail.batch_calls == []


def test_only_unambiguous_client_rejections_are_definite():
    assert is_definite_response_failure(ResponseError("rejected"))
    assert not is_definite_response_failure(ServerResponseError("uncertain"))
