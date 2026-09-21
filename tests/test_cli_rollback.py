from typer.testing import CliRunner

import mailbrain.cli as cli
from mailbrain.cli import app
from mailbrain.storage import db
from mailbrain.storage.models import Mutation, Run

runner = CliRunner()


class FakeGmail:
    def __init__(self):
        self.batch_calls = []

    def get_metadata(self, message_id):
        return {"gmail_id": message_id, "label_ids": ["L1"]}

    def list_message_ids(self, query):
        return ["m2"]

    def batch_modify(self, message_ids, add_label_ids, remove_label_ids):
        self.batch_calls.append((message_ids, add_label_ids, remove_label_ids))


def _prep(monkeypatch, tmp_path, max_mutations=10):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    factory = db.session_factory(home / "state.db")
    with factory() as session:
        run = Run(dry_run=False, run_type="apply", status="succeeded", intended_count=1)
        session.add(run)
        session.flush()
        session.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="m1",
                status="applied",
                labels_before='["INBOX"]',
                labels_after='["L1"]',
                gmail_label_ids_before='["INBOX"]',
                gmail_label_ids_add='["L1"]',
                gmail_label_ids_remove='["INBOX"]',
            )
        )
        session.commit()
        run_id = run.id
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        f"""safety:
  max_mutations: {max_mutations}
  max_archives: 10
  max_archive_fraction: 1.0
  max_scan_age_minutes: 60
"""
    )
    (home / "credentials.json").write_text("{}")
    gmail = FakeGmail()
    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda credentials: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service, page_size=500: gmail)
    return settings, run_id, gmail


def test_rollback_dry_run_preflights_without_writes(monkeypatch, tmp_path):
    settings, run_id, gmail = _prep(monkeypatch, tmp_path)
    result = runner.invoke(
        app, ["rollback", str(run_id), "--settings", str(settings), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "eligible=1" in result.output
    assert gmail.batch_calls == []


def test_rollback_safety_failure_blocks_writes_with_yes(monkeypatch, tmp_path):
    settings, run_id, gmail = _prep(monkeypatch, tmp_path, max_mutations=0)
    result = runner.invoke(
        app, ["rollback", str(run_id), "--settings", str(settings), "--yes"]
    )
    assert result.exit_code == 2
    assert "Safety blocked" in result.output
    assert gmail.batch_calls == []
