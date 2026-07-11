from sqlalchemy import inspect
from typer.testing import CliRunner

from mailbrain.cli import app
from mailbrain.storage import db

runner = CliRunner()


def test_init_creates_app_dir_and_db(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert home.is_dir()
    assert (home / "reports").is_dir()
    assert (home / "state.db").exists()

    engine = db.make_engine(home / "state.db")
    assert "labels" in set(inspect(engine).get_table_names())


def test_init_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBRAIN_HOME", str(tmp_path / "mb"))
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["init"]).exit_code == 0
