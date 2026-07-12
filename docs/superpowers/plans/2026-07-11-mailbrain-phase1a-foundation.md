# MailBrain Phase 1a — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the MailBrain project skeleton, configuration loading, SQLite storage layer, and Gmail OAuth auth code — a tested foundation the core classify/apply loop (Plan 1b) builds on.

**Architecture:** `uv`-managed Python 3.13 package, `src/mailbrain/` layout. Config is Pydantic models loaded from YAML. Storage is SQLAlchemy 2.0 typed ORM over a single local SQLite file at `~/.mailbrain/state.db`. Gmail auth uses `google-auth-oauthlib` InstalledAppFlow with token persistence. All modules are unit-testable without live Gmail credentials (auth is mocked). An `MAILBRAIN_HOME` env var redirects the app dir so tests use a tmp path.

**Tech Stack:** Python 3.13, uv, Typer, Rich, Pydantic v2, PyYAML, SQLAlchemy 2.0, google-api-python-client, google-auth-oauthlib, pytest, pytest-cov, ruff, mypy --strict.

---

## Scope

Covers v0.1 milestones 1–4: skeleton, configuration, SQLite storage, Gmail authentication. Excludes scan, rules, apply, rollback, digest (Plan 1b/1c) and all AI (Phase 2). Deletion is never implemented (per design v0.2). Gmail scope is `gmail.modify` (read + label + archive, no delete).

## Notes carried to Plan 1b (from Phase 1a review)

These surfaced during Phase 1a review and are intentionally deferred to 1b, where the consuming code lives:

- **`SyncState` is a singleton.** `models.py` documents that exactly one row (`id=1`) holds the Gmail incremental-scan cursor. 1b's cursor-update code MUST upsert with an explicit `id=1` (e.g. `session.merge(SyncState(id=1, ...))`) — a bare `INSERT` without `id` would autoincrement and create a second row, causing history replay. The Python-side `default=1` does not enforce this at the SQL level.
- **Regex rule validation.** `RuleMatch.subject_regex` patterns are not compiled at load time in 1a. When 1b builds the rules engine, add a `@field_validator` that `re.compile`s each pattern so a bad regex fails at config-load, not mid-apply.
- **Rule load error messages.** `load_rules` raises a raw Pydantic `ValidationError` on a malformed rule. 1b should wrap it to name the offending rule `id` for a friendlier message.
- **Refresh hardening (done in 1a):** `load_credentials` already falls back to a fresh OAuth flow if `creds.refresh()` raises, so a revoked/expired refresh token cannot wedge the tool. The live `auth` command that calls this lands in 1b.

## File Structure

- `pyproject.toml` — project metadata, deps, tool config (ruff/mypy/pytest).
- `.python-version` — pins `3.13`.
- `.gitignore` — Python + local artifacts.
- `README.md` — overview + manual Gmail OAuth setup steps.
- `config/settings.yaml` — default runtime settings.
- `config/rules.yaml` — built-in rules (from v0.1 rule list).
- `src/mailbrain/__init__.py` — package marker + version.
- `src/mailbrain/paths.py` — `~/.mailbrain` path resolution (honours `MAILBRAIN_HOME`).
- `src/mailbrain/config.py` — Pydantic Settings/Rule models + YAML loaders.
- `src/mailbrain/storage/__init__.py`
- `src/mailbrain/storage/models.py` — SQLAlchemy ORM models (full v0.2 schema).
- `src/mailbrain/storage/db.py` — engine/session factory + `init_db`.
- `src/mailbrain/gmail/__init__.py`
- `src/mailbrain/gmail/auth.py` — OAuth credential load/refresh + service builder.
- `src/mailbrain/cli.py` — Typer app; `init` command only in 1a.
- `tests/…` — mirrors source tree.

Responsibilities are split so each file holds one concern; `paths` is dependency-free so both config, storage, and CLI import it without cycles.

---

### Task 1: Project skeleton + tooling + git

**Files:**
- Create: `.python-version`, `pyproject.toml`, `.gitignore`, `src/mailbrain/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: git init**

```bash
cd /Users/pnl1ez8p/VibeSpace/mailbrain
git init
```

- [ ] **Step 2: Pin Python version**

Create `.python-version`:

```
3.13
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "mailbrain"
version = "0.1.0"
description = "Local-first CLI that classifies Gmail, applies labels in bulk, and publishes Notion digests."
requires-python = ">=3.13"
dependencies = [
    "typer>=0.12",
    "rich>=13",
    "pydantic>=2.7",
    "pyyaml>=6",
    "sqlalchemy>=2.0",
    "google-api-python-client>=2.130",
    "google-auth-oauthlib>=1.2",
    "google-auth>=2.29",
]

[project.scripts]
mailbrain = "mailbrain.cli:app"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-cov>=5",
    "ruff>=0.5",
    "mypy>=1.10",
    "types-pyyaml",
]

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.13"
strict = true
mypy_path = "src"
packages = ["mailbrain"]

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]
pythonpath = ["src"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mailbrain"]
```

- [ ] **Step 4: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
.mypy_cache/
.ruff_cache/
.pytest_cache/
*.egg-info/
dist/
.coverage
htmlcov/
```

- [ ] **Step 5: Create package + test package markers**

`src/mailbrain/__init__.py`:

```python
"""MailBrain — local-first Gmail classifier."""

__version__ = "0.1.0"
```

`tests/__init__.py`:

```python
```

- [ ] **Step 6: Sync environment (fetches Python 3.13 if missing)**

Run: `uv sync`
Expected: creates `.venv`, installs deps, prints a resolved package list. If 3.13 absent, uv downloads it first.

- [ ] **Step 7: Verify tooling runs clean on empty project**

Run: `uv run ruff check . && uv run pytest`
Expected: ruff prints "All checks passed!"; pytest prints "no tests ran" (exit 0, since `--strict` markers not set) — an empty collection is acceptable here.

- [ ] **Step 8: Commit**

```bash
git add .python-version pyproject.toml .gitignore src/mailbrain/__init__.py tests/__init__.py uv.lock
git commit -m "chore: project skeleton with uv, ruff, mypy, pytest"
```

---

### Task 2: Path resolution module

**Files:**
- Create: `src/mailbrain/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

`tests/test_paths.py`:

```python
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


def test_ensure_app_dir_creates_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILBRAIN_HOME", str(tmp_path / "mb"))
    created = paths.ensure_app_dir()
    assert created.is_dir()
    assert (created / "reports").is_dir()
    assert (created / "cache").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.paths'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/paths.py`:

```python
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
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_paths.py -v && uv run ruff check src/mailbrain/paths.py && uv run mypy`
Expected: pytest PASS (4 tests); ruff clean; mypy "Success: no issues found".

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/paths.py tests/test_paths.py
git commit -m "feat: app dir path resolution with MAILBRAIN_HOME override"
```

---

### Task 3: Configuration models + loaders

**Files:**
- Create: `src/mailbrain/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
from mailbrain import config


def test_settings_defaults_from_empty_yaml(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text("")
    s = config.load_settings(p)
    assert s.gmail.page_size == 500
    assert s.gmail.batch_size == 1000
    assert s.ai.enabled is False
    assert s.ai.provider == "mistral"
    assert s.ai.confidence_floor == 0.85
    assert s.notion.enabled is False


def test_settings_override(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text("gmail:\n  page_size: 100\nai:\n  confidence_floor: 0.9\n")
    s = config.load_settings(p)
    assert s.gmail.page_size == 100
    assert s.ai.confidence_floor == 0.9
    assert s.gmail.batch_size == 1000  # untouched default


def test_load_rules(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: booking-payment\n"
        "    match:\n"
        "      from_domain: [booking.com]\n"
        "      subject_contains: [receipt]\n"
        "    actions:\n"
        "      add_labels: [Reizen]\n"
        "      archive: true\n"
    )
    rf = config.load_rules(p)
    assert len(rf.rules) == 1
    rule = rf.rules[0]
    assert rule.id == "booking-payment"
    assert rule.match.from_domain == ["booking.com"]
    assert rule.match.subject_contains == ["receipt"]
    assert rule.actions.add_labels == ["Reizen"]
    assert rule.actions.archive is True
    assert rule.actions.mark_read is False


def test_rule_match_defaults_empty(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: pw-reset\n"
        "    match:\n"
        "      subject_regex: ['(?i)password reset']\n"
        "      older_than_days: 3\n"
        "    actions:\n"
        "      add_labels: [to-be-removed]\n"
    )
    rule = config.load_rules(p).rules[0]
    assert rule.match.subject_regex == ["(?i)password reset"]
    assert rule.match.older_than_days == 3
    assert rule.match.from_domain == []
    assert rule.actions.archive is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.config'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/config.py`:

```python
"""Pydantic configuration models and YAML loaders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class GmailSettings(BaseModel):
    scan_query: str = "in:inbox"
    page_size: int = 500
    batch_size: int = 1000


class AISettings(BaseModel):
    enabled: bool = False
    provider: str = "mistral"
    confidence_floor: float = 0.85


class NotionSettings(BaseModel):
    enabled: bool = False
    parent_page: str = "Personal/Mail System/Weekly Digest"


class Settings(BaseModel):
    gmail: GmailSettings = Field(default_factory=GmailSettings)
    ai: AISettings = Field(default_factory=AISettings)
    notion: NotionSettings = Field(default_factory=NotionSettings)


class RuleMatch(BaseModel):
    from_domain: list[str] = Field(default_factory=list)
    subject_contains: list[str] = Field(default_factory=list)
    subject_regex: list[str] = Field(default_factory=list)
    older_than_days: int | None = None


class RuleActions(BaseModel):
    add_labels: list[str] = Field(default_factory=list)
    archive: bool = False
    mark_read: bool = False


class Rule(BaseModel):
    id: str
    match: RuleMatch
    actions: RuleActions


class RulesFile(BaseModel):
    rules: list[Rule] = Field(default_factory=list)


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    return data if isinstance(data, dict) else {}


def load_settings(path: Path) -> Settings:
    return Settings.model_validate(_read_yaml(path))


def load_rules(path: Path) -> RulesFile:
    return RulesFile.model_validate(_read_yaml(path))
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_config.py -v && uv run ruff check src/mailbrain/config.py && uv run mypy`
Expected: pytest PASS (4 tests); ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/config.py tests/test_config.py
git commit -m "feat: Pydantic settings + rule models with YAML loaders"
```

---

### Task 4: Seed default config files

**Files:**
- Create: `config/settings.yaml`, `config/rules.yaml`
- Test: `tests/test_default_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_default_config.py`:

```python
from pathlib import Path

from mailbrain import config

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_default_settings_load():
    s = config.load_settings(CONFIG_DIR / "settings.yaml")
    assert s.ai.provider == "mistral"
    assert s.ai.enabled is False  # Phase 1 = no AI


def test_default_rules_load_and_are_nonempty():
    rf = config.load_rules(CONFIG_DIR / "rules.yaml")
    ids = {r.id for r in rf.rules}
    assert "booking-payment" in ids
    assert "password-reset" in ids
    # every rule has at least one action
    for r in rf.rules:
        assert r.actions.add_labels or r.actions.archive or r.actions.mark_read


def test_no_rule_requests_deletion():
    # Design v0.2: the tool never deletes. Rules only label/archive/mark_read.
    rf = config.load_rules(CONFIG_DIR / "rules.yaml")
    for r in rf.rules:
        assert not hasattr(r.actions, "trash")
        assert not hasattr(r.actions, "delete")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_default_config.py -v`
Expected: FAIL with `FileNotFoundError` for `config/settings.yaml`.

- [ ] **Step 3: Write `config/settings.yaml`**

```yaml
gmail:
  scan_query: "in:inbox"
  page_size: 500
  batch_size: 1000

ai:
  enabled: false        # Phase 1 = rules only; Mistral enabled in Phase 2
  provider: mistral
  confidence_floor: 0.85

notion:
  enabled: false        # digest wired up in Phase 1c
  parent_page: "Personal/Mail System/Weekly Digest"
```

- [ ] **Step 4: Write `config/rules.yaml`** (built-in rules from v0.1)

```yaml
rules:
  - id: booking-payment
    match:
      from_domain: [booking.com]
      subject_contains: [payment received, receipt]
    actions:
      add_labels: [Reizen]
      archive: true

  - id: campings
    match:
      subject_contains: [camping, campsite]
    actions:
      add_labels: [Reizen]
      archive: true

  - id: password-reset
    match:
      subject_regex: ["(?i)password reset"]
      older_than_days: 3
    actions:
      add_labels: [to-be-removed]
      archive: true
      mark_read: true

  - id: android-weekly
    match:
      subject_contains: [Android Weekly]
    actions:
      add_labels: [to-be-removed]
      archive: true

  - id: bnnvara-newsletter
    match:
      from_domain: [bnnvara.nl]
    actions:
      add_labels: [to-be-removed]
      archive: true

  - id: follow-this
    match:
      subject_contains: [Follow This]
    actions:
      add_labels: [to-be-removed]
      archive: true

  - id: social-schools
    match:
      from_domain: [socialschools.nl]
    actions:
      add_labels: [to-be-removed]
      archive: true

  - id: library-newsletter
    match:
      subject_contains: [bibliotheek, library]
    actions:
      add_labels: [to-be-removed]
      archive: true

  - id: jcrete
    match:
      subject_contains: [JCrete, Heinz Kabutz]
    actions:
      add_labels: [JCrete]

  - id: bux-reports
    match:
      from_domain: [getbux.com]
    actions:
      add_labels: [Beleggen]
      archive: true

  - id: degiro
    match:
      from_domain: [degiro.nl]
    actions:
      add_labels: [Beleggen/deGiro]
      archive: true

  - id: home-utilities
    match:
      subject_contains: [energie, internet, factuur]
    actions:
      add_labels: [Albrecht Thaerlaan 57]

  - id: orders-invoices
    match:
      subject_contains: [order, invoice, factuur, bestelling]
    actions:
      add_labels: [Administratie/Aankopen]
```

- [ ] **Step 5: Run tests + lint**

Run: `uv run pytest tests/test_default_config.py -v && uv run ruff check tests/test_default_config.py`
Expected: pytest PASS (3 tests); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add config/settings.yaml config/rules.yaml tests/test_default_config.py
git commit -m "feat: seed default settings and built-in rules"
```

---

### Task 5: Storage ORM models

**Files:**
- Create: `src/mailbrain/storage/__init__.py`, `src/mailbrain/storage/models.py`
- Test: `tests/storage/__init__.py`, `tests/storage/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/storage/__init__.py`:

```python
```

`tests/storage/test_models.py`:

```python
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mailbrain.storage.models import Base, Label, Message, Mutation, Run


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine


def test_metadata_has_all_tables():
    names = set(Base.metadata.tables.keys())
    assert {
        "messages",
        "threads",
        "labels",
        "runs",
        "mutations",
        "digests",
        "review_queue",
        "sync_state",
    } <= names


def test_insert_and_query_label():
    engine = _engine()
    with Session(engine) as s:
        s.add(Label(gmail_id="Label_1", name="Reizen"))
        s.commit()
    with Session(engine) as s:
        found = s.scalar(select(Label).where(Label.name == "Reizen"))
        assert found is not None
        assert found.gmail_id == "Label_1"


def test_mutation_links_to_run():
    engine = _engine()
    with Session(engine) as s:
        run = Run(dry_run=True)
        s.add(run)
        s.flush()
        s.add(
            Mutation(
                run_id=run.id,
                message_gmail_id="msg_1",
                labels_before="[]",
                labels_after='["Label_1"]',
            )
        )
        s.commit()
    with Session(engine) as s:
        m = s.scalar(select(Mutation))
        assert m is not None
        assert m.message_gmail_id == "msg_1"
        assert m.applied is False


def test_message_defaults():
    engine = _engine()
    with Session(engine) as s:
        s.add(Message(gmail_id="m1"))
        s.commit()
    with Session(engine) as s:
        m = s.scalar(select(Message))
        assert m is not None
        assert m.subject is None
        assert m.history_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/storage/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.storage'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/storage/__init__.py`:

```python
```

`src/mailbrain/storage/models.py`:

```python
"""SQLAlchemy ORM models — the full v0.2 schema."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(unique=True)


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_id: Mapped[str] = mapped_column(unique=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_id: Mapped[str] = mapped_column(unique=True)
    thread_gmail_id: Mapped[str | None] = mapped_column(default=None)
    sender: Mapped[str | None] = mapped_column(default=None)
    subject: Mapped[str | None] = mapped_column(default=None)
    snippet: Mapped[str | None] = mapped_column(default=None)
    history_id: Mapped[str | None] = mapped_column(default=None)
    internal_date: Mapped[datetime | None] = mapped_column(default=None)
    label_ids: Mapped[str | None] = mapped_column(default=None)  # JSON array of gmail label ids


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    dry_run: Mapped[bool] = mapped_column(default=True)
    scanned: Mapped[int] = mapped_column(default=0)
    labeled: Mapped[int] = mapped_column(default=0)
    archived: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)


class Mutation(Base):
    __tablename__ = "mutations"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    message_gmail_id: Mapped[str] = mapped_column()
    labels_before: Mapped[str] = mapped_column(default="[]")  # JSON array
    labels_after: Mapped[str] = mapped_column(default="[]")  # JSON array
    archived_before: Mapped[bool | None] = mapped_column(default=None)
    archived_after: Mapped[bool | None] = mapped_column(default=None)
    read_before: Mapped[bool | None] = mapped_column(default=None)
    read_after: Mapped[bool | None] = mapped_column(default=None)
    applied: Mapped[bool] = mapped_column(default=False)


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    iso_week: Mapped[str] = mapped_column()  # e.g. "2026-W28"
    notion_page_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime | None] = mapped_column(default=None)


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    message_gmail_id: Mapped[str] = mapped_column()
    suggested: Mapped[str] = mapped_column(default="{}")  # JSON: labels + booleans
    source: Mapped[str] = mapped_column(default="conflict")  # conflict | ai (Phase 2)
    reason: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="pending")  # pending | resolved


class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    last_history_id: Mapped[str | None] = mapped_column(default=None)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/storage/test_models.py -v && uv run ruff check src/mailbrain/storage/models.py && uv run mypy`
Expected: pytest PASS (4 tests); ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/storage/__init__.py src/mailbrain/storage/models.py tests/storage/__init__.py tests/storage/test_models.py
git commit -m "feat: SQLAlchemy ORM models for full v0.2 schema"
```

---

### Task 6: Storage engine + init_db + session factory

**Files:**
- Create: `src/mailbrain/storage/db.py`
- Test: `tests/storage/test_db.py`

- [ ] **Step 1: Write the failing test**

`tests/storage/test_db.py`:

```python
from sqlalchemy import inspect, select

from mailbrain.storage import db
from mailbrain.storage.models import Label


def test_init_db_creates_file_and_tables(tmp_path):
    p = tmp_path / "state.db"
    db.init_db(p)
    assert p.exists()
    engine = db.make_engine(p)
    tables = set(inspect(engine).get_table_names())
    assert {"messages", "labels", "runs", "mutations"} <= tables


def test_session_factory_roundtrip(tmp_path):
    p = tmp_path / "state.db"
    db.init_db(p)
    Session = db.session_factory(p)
    with Session() as s:
        s.add(Label(gmail_id="L1", name="Work"))
        s.commit()
    with Session() as s:
        assert s.scalar(select(Label).where(Label.name == "Work")) is not None


def test_init_db_is_idempotent(tmp_path):
    p = tmp_path / "state.db"
    db.init_db(p)
    db.init_db(p)  # second call must not raise
    assert p.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/storage/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.storage.db'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/storage/db.py`:

```python
"""SQLite engine, schema creation, and session factory."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.storage.models import Base


def make_engine(db_path: Path) -> Engine:
    return create_engine(f"sqlite:///{db_path}", future=True)


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)


def session_factory(db_path: Path) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(db_path), future=True)
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/storage/test_db.py -v && uv run ruff check src/mailbrain/storage/db.py && uv run mypy`
Expected: pytest PASS (3 tests); ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/storage/db.py tests/storage/test_db.py
git commit -m "feat: SQLite engine, init_db, and session factory"
```

---

### Task 7: Gmail authentication

**Files:**
- Create: `src/mailbrain/gmail/__init__.py`, `src/mailbrain/gmail/auth.py`
- Test: `tests/gmail/__init__.py`, `tests/gmail/test_auth.py`

- [ ] **Step 1: Write the failing test**

`tests/gmail/__init__.py`:

```python
```

`tests/gmail/test_auth.py`:

```python
from unittest.mock import MagicMock, patch

from mailbrain.gmail import auth


def test_scope_is_modify_not_delete():
    # Design v0.2: gmail.modify covers read+label+archive; never a delete scope.
    assert auth.SCOPES == ["https://www.googleapis.com/auth/gmail.modify"]


def test_valid_existing_token_skips_flow(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("{}")
    creds = MagicMock()
    creds.valid = True

    with (
        patch.object(
            auth.Credentials, "from_authorized_user_file", return_value=creds
        ) as from_file,
        patch.object(auth.InstalledAppFlow, "from_client_secrets_file") as flow,
    ):
        result = auth.load_credentials(tmp_path / "credentials.json", token)

    from_file.assert_called_once()
    flow.assert_not_called()
    assert result is creds


def test_expired_token_refreshes(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("{}")
    creds = MagicMock()
    creds.valid = False
    creds.expired = True
    creds.refresh_token = "r"
    creds.to_json.return_value = "{}"

    with (
        patch.object(auth.Credentials, "from_authorized_user_file", return_value=creds),
        patch.object(auth, "Request") as request,
    ):
        result = auth.load_credentials(tmp_path / "credentials.json", token)

    creds.refresh.assert_called_once_with(request.return_value)
    assert result is creds


def test_no_token_runs_flow_and_persists(tmp_path):
    token = tmp_path / "token.json"  # does not exist
    new_creds = MagicMock()
    new_creds.to_json.return_value = '{"token": "x"}'
    flow_instance = MagicMock()
    flow_instance.run_local_server.return_value = new_creds

    with patch.object(
        auth.InstalledAppFlow, "from_client_secrets_file", return_value=flow_instance
    ) as from_secrets:
        result = auth.load_credentials(tmp_path / "credentials.json", token)

    from_secrets.assert_called_once()
    flow_instance.run_local_server.assert_called_once()
    assert result is new_creds
    assert token.read_text() == '{"token": "x"}'


def test_build_service_uses_gmail_v1():
    creds = MagicMock()
    with patch.object(auth, "build") as build_mock:
        auth.build_service(creds)
    build_mock.assert_called_once_with("gmail", "v1", credentials=creds)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gmail/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.gmail'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/gmail/__init__.py`:

```python
```

`src/mailbrain/gmail/auth.py`:

```python
"""Gmail OAuth: load/refresh credentials and build the API service.

Scope is gmail.modify (read + label + archive). The tool never deletes,
so no mail.google.com / delete scope is requested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def load_credentials(credentials_path: Path, token_path: Path) -> Credentials:
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    return creds


def build_service(creds: Credentials) -> Any:
    return build("gmail", "v1", credentials=creds)
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/gmail/test_auth.py -v && uv run ruff check src/mailbrain/gmail/auth.py && uv run mypy`
Expected: pytest PASS (5 tests); ruff clean; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add src/mailbrain/gmail/__init__.py src/mailbrain/gmail/auth.py tests/gmail/__init__.py tests/gmail/test_auth.py
git commit -m "feat: Gmail OAuth credential load/refresh with gmail.modify scope"
```

---

### Task 8: CLI `init` command

**Files:**
- Create: `src/mailbrain/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mailbrain.cli'`

- [ ] **Step 3: Write minimal implementation**

`src/mailbrain/cli.py`:

```python
"""MailBrain command-line interface."""

from __future__ import annotations

import typer
from rich.console import Console

from mailbrain import paths
from mailbrain.storage.db import init_db

app = typer.Typer(help="MailBrain — local-first Gmail classifier.")
console = Console()


@app.command()
def init() -> None:
    """Create the local state directory and initialize the SQLite database."""
    app_dir = paths.ensure_app_dir()
    init_db(paths.db_path())
    console.print(f"[green]Initialized MailBrain at[/] {app_dir}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests + lint + type-check**

Run: `uv run pytest tests/test_cli.py -v && uv run ruff check src/mailbrain/cli.py && uv run mypy`
Expected: pytest PASS (2 tests); ruff clean; mypy Success.

- [ ] **Step 5: Verify the installed entry point works**

Run: `MAILBRAIN_HOME=$(mktemp -d) uv run mailbrain init`
Expected: prints `Initialized MailBrain at /var/folders/...`; exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/mailbrain/cli.py tests/test_cli.py
git commit -m "feat: mailbrain init command"
```

---

### Task 9: README with Gmail OAuth setup instructions

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# MailBrain

Local-first CLI that classifies Gmail with deterministic rules, applies labels
and archives in bulk, and (later) publishes weekly Notion digests. Keeps a full
audit trail with rollback. **The tool never deletes mail** — deletion stays a
manual action in Gmail as a safeguard.

See `docs/superpowers/specs/2026-07-11-mailbrain-design.md` for the design.

## Requirements

- Python 3.13+ (managed automatically by `uv`)
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
uv run mailbrain init      # creates ~/.mailbrain/ and state.db
```

## Gmail API credentials (manual, one-time)

MailBrain needs an OAuth **Desktop** client. This step is manual — do it once
before running `mailbrain auth` (auth command lands in Plan 1b):

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or pick one).
3. Enable the **Gmail API** (APIs & Services → Library → Gmail API → Enable).
4. Configure the OAuth consent screen (External; add yourself as a test user).
5. Credentials → Create Credentials → **OAuth client ID** → Application type
   **Desktop app**.
6. Download the JSON and save it as `~/.mailbrain/credentials.json`.

Requested scope is `https://www.googleapis.com/auth/gmail.modify` — read, label,
and archive only. No delete scope is ever requested.

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run mypy            # type-check
```
```

- [ ] **Step 2: Verify markdown has no broken local links**

Run: `test -f docs/superpowers/specs/2026-07-11-mailbrain-design.md && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README with Gmail OAuth setup steps"
```

---

## Final verification

- [ ] **Full suite + lint + types green**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all tests PASS; ruff "All checks passed!"; mypy "Success: no issues found".

---

## Self-Review (completed during planning)

**Spec coverage (v0.2 + v0.1 milestones 1–4):**
- Skeleton/tooling → Task 1. Config models + YAML → Tasks 3–4. Full SQLite schema incl. `review_queue`, `sync_state` → Tasks 5–6. Gmail auth (`gmail.modify`, no delete) → Task 7. `init` command → Task 8. Path layout `~/.mailbrain/{state.db,credentials.json,token.json,reports/,cache/}` → Task 2. No-delete principle asserted in tests (Tasks 4, 7). Deferred correctly: scan/rules/apply/rollback/digest (1b/1c), AI (Phase 2), live OAuth run (needs user's credentials.json).

**Placeholder scan:** none — every code step has complete code; every run step has a command + expected output.

**Type consistency:** `Base`, `Label`, `Message`, `Run`, `Mutation` (with `message_gmail_id`, `labels_before/after`, `applied`) defined in Task 5 and used identically in Tasks 6 and 8 tests. `paths.*` functions (Task 2) reused verbatim in Tasks 6/8. `auth.SCOPES`, `load_credentials(credentials_path, token_path)`, `build_service(creds)` signatures consistent between Task 7 impl and its tests. `config.load_settings/load_rules` (Task 3) reused in Task 4.
