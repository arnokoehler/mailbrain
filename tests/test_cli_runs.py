import json
from datetime import UTC, datetime

from typer.testing import CliRunner

import mailbrain.cli as cli
from mailbrain.cli import app
from mailbrain.storage import db
from mailbrain.storage.models import Mutation, Run

runner = CliRunner()


def _seed(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir()
    db.init_db(home / "state.db")
    factory = db.session_factory(home / "state.db")
    with factory() as session:
        run = Run(dry_run=False, status="needs_review", intended_count=1)
        session.add(run)
        session.flush()
        session.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="m1",
                status="uncertain",
                reconciled_at=datetime.now(UTC),
                gmail_label_ids_before=json.dumps(["INBOX"]),
                gmail_label_ids_add=json.dumps(["L1"]),
                gmail_label_ids_remove=json.dumps([]),
            )
        )
        session.commit()
        return run.id


def test_runs_list_inspect_and_abandon(monkeypatch, tmp_path):
    run_id = _seed(monkeypatch, tmp_path)
    listed = runner.invoke(app, ["runs"])
    listed_explicitly = runner.invoke(app, ["runs", "list"])
    inspected = runner.invoke(app, ["runs", "inspect", str(run_id)])
    abandoned = runner.invoke(
        app,
        ["runs", "abandon", str(run_id), "--reason", "handled manually", "--yes"],
    )
    assert listed.exit_code == 0 and "needs_review" in listed.output
    assert listed_explicitly.exit_code == 0 and "needs_review" in listed_explicitly.output
    assert inspected.exit_code == 0 and "mutation" in inspected.output
    assert abandoned.exit_code == 0 and "abandoned" in abandoned.output


def test_runs_reconcile_reads_gmail_without_writes(monkeypatch, tmp_path):
    run_id = _seed(monkeypatch, tmp_path)
    home = tmp_path / "mb"
    (home / "credentials.json").write_text("{}")

    class ReadOnlyGmail:
        def get_metadata(self, message_id):
            return {"gmail_id": message_id, "label_ids": ["INBOX", "L1"]}

        def batch_modify(self, *args, **kwargs):
            raise AssertionError("reconcile must not mutate Gmail")

        def list_labels(self):
            return {"L1": "Reizen"}

    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda credentials: object())
    monkeypatch.setattr(
        cli, "GmailClient", lambda service, page_size=500: ReadOnlyGmail()
    )
    result = runner.invoke(app, ["runs", "reconcile", str(run_id)])
    assert result.exit_code == 0, result.output
    assert "uncertain" in result.output
