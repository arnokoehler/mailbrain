import json
from unittest.mock import MagicMock

from mailbrain.rollback import rollback_run
from mailbrain.storage import db
from mailbrain.storage.models import Label, Mutation, Run


def _factory(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(Label(gmail_id="L_reizen", name="Reizen"))
        s.commit()
    return factory


def _seed_run(factory) -> int:
    with factory() as s:
        run = Run(dry_run=False)
        s.add(run)
        s.flush()
        s.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="m1",
                labels_before=json.dumps(["INBOX"]),
                labels_after=json.dumps(["Reizen"]),
                archived_before=True,
                archived_after=False,
                read_before=False,
                read_after=False,
                applied=True,
            )
        )
        s.commit()
        return run.id


def test_rollback_reverses_mutations(tmp_path):
    factory = _factory(tmp_path)
    run_id = _seed_run(factory)
    client = MagicMock()

    count = rollback_run(client, factory, run_id)

    assert count == 1
    _, kwargs = client.batch_modify.call_args
    assert kwargs["message_ids"] == ["m1"]
    assert kwargs["add_label_ids"] == ["INBOX"]
    assert kwargs["remove_label_ids"] == ["L_reizen"]


def test_rollback_unknown_run_returns_zero(tmp_path):
    factory = _factory(tmp_path)
    client = MagicMock()
    assert rollback_run(client, factory, 999) == 0
    client.batch_modify.assert_not_called()
