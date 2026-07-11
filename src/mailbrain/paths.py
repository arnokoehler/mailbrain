"""Filesystem paths for MailBrain's local state directory."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_ENV = "MAILBRAIN_HOME"


def app_dir() -> Path:
    override = os.environ.get(APP_DIR_ENV)
    return Path(override) if override else Path.home() / ".mailbrain"


def db_path() -> Path:
    return app_dir() / "state.db"


def credentials_path() -> Path:
    return app_dir() / "credentials.json"


def token_path() -> Path:
    return app_dir() / "token.json"


def reports_dir() -> Path:
    return app_dir() / "reports"


def cache_dir() -> Path:
    return app_dir() / "cache"


def ensure_app_dir() -> Path:
    root = app_dir()
    root.mkdir(parents=True, exist_ok=True)
    reports_dir().mkdir(exist_ok=True)
    cache_dir().mkdir(exist_ok=True)
    return root
