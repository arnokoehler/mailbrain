import json
from datetime import UTC, datetime

from typer.testing import CliRunner

import mailbrain.cli as cli
from mailbrain.cli import app
from mailbrain.notion.publisher import PublicationResult
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message

runner = CliRunner()


def test_digest_writes_deterministic_markdown(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir()
    db.init_db(home / "state.db")
    factory = db.session_factory(home / "state.db")
    with factory() as session:
        session.add(Label(gmail_id="work", name="Werk/AI"))
        session.add(
            Message(
                gmail_id="m1",
                sender="Example <news@example.com>",
                subject="AI engineering",
                snippet="A useful development",
                internal_date=datetime(2026, 9, 15, tzinfo=UTC),
                label_ids=json.dumps(["work"]),
            )
        )
        session.commit()
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "rules:\n"
        "  - id: nieuwsbrief-example\n"
        "    match: { from_domain: [example.com] }\n"
        "    actions: { archive: true }\n"
    )
    output = tmp_path / "digest.md"

    result = runner.invoke(
        app,
        ["digest", "--week", "2026-W38", "--rules", str(rules), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert "deterministic overview" in result.output
    assert "## Werk" in output.read_text()
    assert "AI engineering" in output.read_text()


def test_digest_requires_api_key_when_ai_enabled(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    home.mkdir()
    db.init_db(home / "state.db")
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules: []\n")
    settings = tmp_path / "settings.yaml"
    settings.write_text("ai:\n  enabled: true\n")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    result = runner.invoke(
        app,
        ["digest", "--week", "2026-W38", "--rules", str(rules), "--settings", str(settings)],
    )

    assert result.exit_code == 0
    assert "Mistral unavailable; using deterministic digest" in result.output
    assert "MISTRAL_API_KEY is required" in result.output


def test_digest_publishes_to_notion_when_enabled(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    home.mkdir()
    db.init_db(home / "state.db")
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules: []\n")
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "notion:\n"
        "  enabled: true\n"
        "  parent_page_id: parent-id\n"
    )
    calls = []

    class FakePublisher:
        def __init__(self, factory, client, parent_page_id):
            calls.append((factory, client, parent_page_id))

        def publish(self, iso_week, markdown, *, retry_uncertain=False):
            calls.append((iso_week, markdown, retry_uncertain))
            return PublicationResult("created", "page-id", "https://notion/page-id")

    monkeypatch.setattr(cli, "DigestPublisher", FakePublisher)
    monkeypatch.setattr(cli, "UrllibHttpTransport", lambda: object())
    monkeypatch.setattr(cli, "NotionClient", lambda token, transport: (token, transport))

    result = runner.invoke(
        app,
        ["digest", "--week", "2026-W38", "--rules", str(rules), "--settings", str(settings)],
    )

    assert result.exit_code == 0, result.output
    assert "Published Notion digest 2026-W38" in result.output
    assert calls[0][2] == "parent-id"
    assert calls[1][0] == "2026-W38"


def test_digest_requires_notion_token_after_writing_local_report(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    home.mkdir()
    db.init_db(home / "state.db")
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules: []\n")
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "notion:\n"
        "  enabled: true\n"
        "  parent_page_id: parent-id\n"
    )
    output = tmp_path / "digest.md"

    result = runner.invoke(
        app,
        [
            "digest",
            "--week",
            "2026-W38",
            "--rules",
            str(rules),
            "--settings",
            str(settings),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    assert "Notion publishing skipped" in result.output
    assert "NOTION_TOKEN is required" in result.output
    assert output.exists()


def test_digest_continues_when_notion_publication_fails(monkeypatch, tmp_path):
    home = tmp_path / "mb"
    monkeypatch.setenv("MAILBRAIN_HOME", str(home))
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    home.mkdir()
    db.init_db(home / "state.db")
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules: []\n")
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        "notion:\n"
        "  enabled: true\n"
        "  parent_page_id: parent-id\n"
    )

    class FailingPublisher:
        def __init__(self, factory, client, parent_page_id):
            pass

        def publish(self, iso_week, markdown, *, retry_uncertain=False):
            return PublicationResult("failed", error="Notion unavailable")

    monkeypatch.setattr(cli, "DigestPublisher", FailingPublisher)
    monkeypatch.setattr(cli, "UrllibHttpTransport", lambda: object())
    monkeypatch.setattr(cli, "NotionClient", lambda token, transport: (token, transport))

    result = runner.invoke(
        app,
        ["digest", "--week", "2026-W38", "--rules", str(rules), "--settings", str(settings)],
    )

    assert result.exit_code == 0, result.output
    assert "Notion publication failed" in result.output
    assert "local digest remains available" in result.output
