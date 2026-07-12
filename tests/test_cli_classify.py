import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from mailbrain.cli import app
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message

runner = CliRunner()


def _seed(dbp):
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(
            Message(
                gmail_id="m1",
                sender="a@booking.com",
                subject="Your receipt",
                snippet="x",
                label_ids=json.dumps(["INBOX"]),
                internal_date=datetime(2026, 7, 1, tzinfo=UTC),
            )
        )
        s.commit()


def test_classify_reports_planned_changes(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home / "state.db")

    result = runner.invoke(app, ["classify", "--rules", "config/rules.yaml"])
    assert result.exit_code == 0, result.output
    assert "Reizen" in result.output


def test_classify_empty_db_is_clean(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    result = runner.invoke(app, ["classify", "--rules", "config/rules.yaml"])
    assert result.exit_code == 0, result.output
    assert "planned 0" in result.output
