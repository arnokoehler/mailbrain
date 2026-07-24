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


def _prep(monkeypatch, tmp_path, creds=True):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home)
    if creds:
        (home / "credentials.json").write_text("{}")
    return home


def test_apply_dry_run_flag_previews_without_executing(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path, creds=False)
    called = {"execute": False}
    monkeypatch.setattr(cli, "execute_plan", lambda *a, **k: called.__setitem__("execute", True))

    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path)), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert called["execute"] is False
    assert "dry-run" in result.output.lower()
    assert "Reizen" in result.output


def test_apply_aborts_when_user_declines_prompt(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    called = {"execute": False}
    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda creds: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service: "CLIENT")
    monkeypatch.setattr(cli, "execute_plan", lambda *a, **k: called.__setitem__("execute", True))

    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path))], input="n\n")
    assert result.exit_code != 0  # aborted
    assert called["execute"] is False


def test_apply_executes_when_user_confirms(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda creds: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service: "CLIENT")
    captured = {}

    def fake_execute(client, factory, plans, current):
        captured["client"] = client
        captured["n"] = len(plans)
        return 42

    monkeypatch.setattr(cli, "execute_plan", fake_execute)

    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path))], input="y\n")
    assert result.exit_code == 0, result.output
    assert captured["client"] == "CLIENT"
    assert captured["n"] == 1
    assert "42" in result.output


def test_apply_yes_skips_prompt_and_executes(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda creds: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service: "CLIENT")
    captured = {}

    def fake_execute(client, factory, plans, current):
        captured["n"] = len(plans)
        return 7

    monkeypatch.setattr(cli, "execute_plan", fake_execute)

    # no stdin provided; --yes must mean no prompt is read
    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path)), "--yes"])
    assert result.exit_code == 0, result.output
    assert captured["n"] == 1
    assert "7" in result.output


def test_apply_missing_credentials_errors(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path, creds=False)
    result = runner.invoke(app, ["apply", "--rules", str(_rules(tmp_path)), "--yes"])
    assert result.exit_code == 2
    assert "credentials.json not found" in result.output
