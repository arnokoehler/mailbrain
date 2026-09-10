from unittest.mock import MagicMock

from typer.testing import CliRunner

import mailbrain.cli as cli
from mailbrain.cli import app
from mailbrain.storage import db

runner = CliRunner()


def test_scan_wires_auth_client_and_scanner(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    (home / "credentials.json").write_text("{}")

    monkeypatch.setattr(cli, "load_credentials", lambda c, t: MagicMock())
    monkeypatch.setattr(cli, "build_service", lambda creds: MagicMock())

    captured: dict[str, object] = {}

    def fake_scan(client, query, factory, skip_cached=False):
        captured["query"] = query
        captured["client"] = client
        captured["skip_cached"] = skip_cached
        return 7

    monkeypatch.setattr(cli, "scan_mailbox", fake_scan)

    result = runner.invoke(app, ["scan", "--query", "in:inbox"])
    assert result.exit_code == 0, result.output
    assert captured["query"] == "in:inbox"
    assert "7" in result.output
    assert captured["client"] is not None


def test_scan_missing_credentials_errors(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    # no credentials.json created

    result = runner.invoke(app, ["scan", "--query", "in:inbox"])
    assert result.exit_code == 2
    assert "credentials.json not found" in result.output
