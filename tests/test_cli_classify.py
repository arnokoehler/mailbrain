import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from mailbrain.cli import app
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message, ScanRun

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


def _seed(dbp):
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        scan = ScanRun(
            query="in:inbox",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            status="succeeded",
            listed_count=1,
            fetched_count=1,
        )
        s.add(scan)
        s.flush()
        s.add(Label(gmail_id="INBOX", name="INBOX"))
        s.add(
            Message(
                gmail_id="m1",
                sender="a@booking.com",
                subject="Your receipt",
                snippet="x",
                label_ids=json.dumps(["INBOX"]),
                internal_date=datetime(2026, 7, 1, tzinfo=UTC),
                last_scan_id=scan.id,
            )
        )
        s.commit()


def _write_rules(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML)
    return rules


def test_classify_reports_planned_changes(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home / "state.db")
    rules = _write_rules(tmp_path)

    result = runner.invoke(app, ["classify", "--rules", str(rules)])
    assert result.exit_code == 0, result.output
    assert "Reizen" in result.output
    assert "archived 1" in result.output  # booking rule archives, message is in INBOX


def test_classify_pager_flag_still_renders(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    _seed(home / "state.db")
    rules = _write_rules(tmp_path)
    monkeypatch.setenv("PAGER", "cat")  # non-interactive pager for the test

    result = runner.invoke(app, ["classify", "--rules", str(rules), "--pager"])
    assert result.exit_code == 0, result.output
    assert "Reizen" in result.output


def test_classify_empty_db_is_clean(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")
    factory = db.session_factory(home / "state.db")
    with factory() as session:
        session.add(
            ScanRun(
                query="in:inbox",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                status="succeeded",
            )
        )
        session.commit()
    rules = _write_rules(tmp_path)

    result = runner.invoke(app, ["classify", "--rules", str(rules)])
    assert result.exit_code == 0, result.output
    assert "planned 0" in result.output


def test_classify_missing_rules_file_errors(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir(parents=True)
    db.init_db(home / "state.db")

    result = runner.invoke(app, ["classify", "--rules", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
    assert "not found" in result.output
