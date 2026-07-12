from typer.testing import CliRunner

import mailbrain.cli as cli
from mailbrain.cli import app
from mailbrain.storage import db

runner = CliRunner()


def test_rollback_wires_client_and_calls_rollback(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    (home / "credentials.json").write_text("{}")

    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda creds: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service: "CLIENT")

    captured = {}

    def fake_rollback(client, factory, run_id):
        captured["client"] = client
        captured["run_id"] = run_id
        return 3

    monkeypatch.setattr(cli, "rollback_run", fake_rollback)

    result = runner.invoke(app, ["rollback", "7"])
    assert result.exit_code == 0, result.output
    assert captured["run_id"] == 7
    assert captured["client"] == "CLIENT"
    assert "3" in result.output


def test_rollback_missing_credentials_errors(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")

    result = runner.invoke(app, ["rollback", "7"])
    assert result.exit_code == 2
    assert "credentials.json not found" in result.output
