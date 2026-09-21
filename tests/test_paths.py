from pathlib import Path

from mailbrain import paths


def test_app_dir_defaults_to_home(monkeypatch):
    monkeypatch.delenv("MAILBRAIN_HOME", raising=False)
    assert paths.app_dir() == Path.home() / ".mailbrain"


def test_app_dir_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBRAIN_HOME", str(tmp_path))
    assert paths.app_dir() == tmp_path


def test_derived_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBRAIN_HOME", str(tmp_path))
    assert paths.db_path() == tmp_path / "state.db"
    assert paths.credentials_path() == tmp_path / "credentials.json"
    assert paths.token_path() == tmp_path / "token.json"
    assert paths.reports_dir() == tmp_path / "reports"
    assert paths.cache_dir() == tmp_path / "cache"
    assert paths.lock_path() == tmp_path / "mailbrain.lock"


def test_ensure_app_dir_creates_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBRAIN_HOME", str(tmp_path / "mb"))
    created = paths.ensure_app_dir()
    assert created.is_dir()
    assert (created / "reports").is_dir()
    assert (created / "cache").is_dir()
