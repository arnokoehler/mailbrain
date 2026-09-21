import json
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

import mailbrain.cli as cli
from mailbrain.cli import app
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message, ScanRun

runner = CliRunner()

RULES_YAML = """rules:
  - id: booking
    match:
      from_domain: [booking.com]
    actions:
      add_labels: [Reizen]
      archive: true
"""
SETTINGS_YAML = """gmail:
  scan_query: in:inbox
  page_size: 250
  batch_size: 500
safety:
  max_mutations: 10
  max_archives: 10
  max_archive_fraction: 1.0
  max_scan_age_minutes: 60
"""


class FakeGmail:
    def __init__(self):
        self.batch_calls = []
        self.create_calls = []

    def get_metadata(self, message_id):
        return {
            "gmail_id": message_id,
            "thread_id": None,
            "snippet": None,
            "sender": "a@booking.com",
            "subject": "Receipt",
            "label_ids": ["INBOX"],
            "internal_date_ms": 1782864000000,
        }

    def list_labels(self):
        return {"INBOX": "INBOX", "L1": "Reizen"}

    def create_label(self, name):
        self.create_calls.append(name)
        return "L1"

    def batch_modify(self, message_ids, add_label_ids, remove_label_ids):
        self.batch_calls.append((message_ids, add_label_ids, remove_label_ids))


def _prep(monkeypatch, tmp_path, *, status="succeeded", age_minutes=0, limits=True):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    factory = db.session_factory(home / "state.db")
    started_at = datetime.now(UTC) - timedelta(minutes=age_minutes)
    with factory() as session:
        scan = ScanRun(
            query="in:inbox",
            started_at=started_at,
            finished_at=started_at,
            status=status,
            listed_count=1,
            fetched_count=1,
        )
        session.add(scan)
        session.flush()
        session.add_all(
            [
                Label(gmail_id="INBOX", name="INBOX"),
                Label(gmail_id="L1", name="Reizen"),
                Message(
                    gmail_id="m1",
                    sender="a@booking.com",
                    subject="Receipt",
                    label_ids=json.dumps(["INBOX"]),
                    internal_date=datetime(2026, 7, 1, tzinfo=UTC),
                    last_scan_id=scan.id,
                ),
            ]
        )
        session.commit()
        scan_id = scan.id
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML)
    settings = tmp_path / "settings.yaml"
    settings.write_text(SETTINGS_YAML if limits else "safety: {}\n")
    (home / "credentials.json").write_text("{}")
    return home, rules, settings, scan_id


def _wire_client(monkeypatch, gmail):
    monkeypatch.setattr(cli, "load_credentials", lambda c, t: object())
    monkeypatch.setattr(cli, "build_service", lambda credentials: object())
    monkeypatch.setattr(cli, "GmailClient", lambda service, page_size=500: gmail)


def test_apply_dry_run_uses_latest_successful_scan_without_credentials(monkeypatch, tmp_path):
    home, rules, settings, scan_id = _prep(monkeypatch, tmp_path)
    (home / "credentials.json").unlink()
    result = runner.invoke(
        app, ["apply", "--rules", str(rules), "--settings", str(settings), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert f"scan {scan_id}" in result.output
    assert "dry-run" in result.output.lower()


def test_apply_yes_executes_explicit_safe_fresh_scan(monkeypatch, tmp_path):
    _, rules, settings, scan_id = _prep(monkeypatch, tmp_path)
    gmail = FakeGmail()
    _wire_client(monkeypatch, gmail)
    result = runner.invoke(
        app,
        [
            "apply",
            "--rules",
            str(rules),
            "--settings",
            str(settings),
            "--scan-id",
            str(scan_id),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(gmail.batch_calls) == 1
    assert "confirmed=1" in result.output


def test_apply_missing_limits_blocks_before_oauth_even_with_yes(monkeypatch, tmp_path):
    _, rules, settings, scan_id = _prep(monkeypatch, tmp_path, limits=False)
    constructed = False

    def fail_client(*args, **kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError

    monkeypatch.setattr(cli, "GmailClient", fail_client)
    result = runner.invoke(
        app,
        [
            "apply",
            "--rules",
            str(rules),
            "--settings",
            str(settings),
            "--scan-id",
            str(scan_id),
            "--yes",
        ],
    )
    assert result.exit_code == 2
    assert "Safety blocked" in result.output
    assert constructed is False


def test_apply_rejects_stale_or_failed_scan_before_oauth(monkeypatch, tmp_path):
    for status, age in (("succeeded", 61), ("failed", 0)):
        case = tmp_path / status
        case.mkdir()
        _, rules, settings, scan_id = _prep(monkeypatch, case, status=status, age_minutes=age)
        monkeypatch.setattr(
            cli,
            "load_credentials",
            lambda *args: (_ for _ in ()).throw(AssertionError),
        )
        result = runner.invoke(
            app,
            [
                "apply",
                "--rules",
                str(rules),
                "--settings",
                str(settings),
                "--scan-id",
                str(scan_id),
                "--yes",
            ],
        )
        assert result.exit_code == 2


def test_apply_live_drift_blocks_all_writes(monkeypatch, tmp_path):
    _, rules, settings, scan_id = _prep(monkeypatch, tmp_path)
    gmail = FakeGmail()
    original = gmail.get_metadata

    def drifted(message_id):
        value = original(message_id)
        value["label_ids"] = ["INBOX", "STARRED"]
        return value

    gmail.get_metadata = drifted
    _wire_client(monkeypatch, gmail)
    result = runner.invoke(
        app,
        [
            "apply",
            "--rules",
            str(rules),
            "--settings",
            str(settings),
            "--scan-id",
            str(scan_id),
            "--yes",
        ],
    )
    assert result.exit_code == 2
    assert "fresh scan" in result.output
    assert gmail.create_calls == []
    assert gmail.batch_calls == []
