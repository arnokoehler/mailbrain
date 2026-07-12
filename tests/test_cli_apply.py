import json
from datetime import UTC, datetime

from typer.testing import CliRunner

import mailbrain.cli as cli
from mailbrain.cli import app
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message

runner = CliRunner()

RULES_YAML = (
    "rules:\n"
    "  - id: booking\n"
    "    match:\n"
    "      from_domain: [booking.com]\n"
    "    actions:\n"
    "      add_labels: [Reizen]\n"
    "      archive: true\n"
)


def _seed(home):
    db.init_db(home / "state.db")
    factory = db.session_factory(home / "state.db")
    with factory() as s:
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(
            Message(
                gmail_id="m1",
                sender="a@booking.com",
                subject="Receipt",
                label_ids=json.dumps(["INBOX"]),
                internal_date=datetime(2026, 7, 1, tzinfo=UTC),
            )
        )
        s.commit()


def _rules(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(RULES_YAML)
    return p


def test_apply_dry_run_by_default_does_not_execute(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home)

    called = {"execute": False}

    def _no_execute(*a, **k):
        called.__setitem__("execute", True)
        return 1

    monkeypatch.setattr(cli, "execute_plan", _no_execute)

    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path))])
    assert result.exit_code == 0, result.output
    assert called["execute"] is False
    assert "dry-run" in result.output.lower()
    assert "Reizen" in result.output


def test_apply_execute_calls_executor(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home)

    captured = {}
    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda creds: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service: "CLIENT")

    def fake_execute(client, factory, plans, current):
        captured["client"] = client
        captured["n"] = len(plans)
        return 42

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    (home / "credentials.json").write_text("{}")

    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path)), "--execute"])
    assert result.exit_code == 0, result.output
    assert captured["client"] == "CLIENT"
    assert captured["n"] == 1
    assert "42" in result.output
