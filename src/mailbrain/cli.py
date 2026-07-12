"""MailBrain command-line interface."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from mailbrain import config, paths
from mailbrain.gmail.auth import build_service, load_credentials
from mailbrain.gmail.client import GmailClient
from mailbrain.planner import plan_mutations
from mailbrain.report import render_metrics, render_plan, summarize
from mailbrain.rules.engine import classify as classify_messages
from mailbrain.scan import load_cached, scan_mailbox
from mailbrain.storage.db import init_db, session_factory

app = typer.Typer(help="MailBrain — local-first Gmail classifier.")
console = Console()


@app.callback()
def _callback() -> None:
    """MailBrain — local-first Gmail classifier."""


@app.command()
def init() -> None:
    """Create the local state directory and initialize the SQLite database."""
    app_dir = paths.ensure_app_dir()
    init_db(paths.db_path())
    console.print(f"[green]Initialized MailBrain at[/] {app_dir}")


@app.command()
def classify(
    rules: Path = typer.Option(  # noqa: B008 - Typer option factory
        Path("config/rules.yaml"), "--rules", help="Path to rules YAML."
    ),
) -> None:
    """Classify cached mail and print a dry-run report (no changes applied)."""
    if not rules.exists():
        console.print(f"[red]Rules file not found:[/] {rules}")
        raise typer.Exit(code=2)
    rules_file = config.load_rules(rules)
    factory = session_factory(paths.db_path())
    messages, current = load_cached(factory)
    classifications = classify_messages(messages, rules_file.rules, datetime.now(UTC))
    plans = plan_mutations(classifications, current)
    metrics = summarize(plans, scanned=len(messages))
    render_plan(plans, messages, console=console)
    render_metrics(metrics, console=console)


@app.command()
def scan(
    query: str = typer.Option("in:inbox", "--query", help="Gmail search query."),
) -> None:
    """Fetch Gmail metadata for the query and cache it locally."""
    if not paths.credentials_path().exists():
        console.print(
            f"[red]credentials.json not found:[/] {paths.credentials_path()}\n"
            "Download an OAuth desktop client from Google Cloud Console and save it there "
            "(see README)."
        )
        raise typer.Exit(code=2)
    creds = load_credentials(paths.credentials_path(), paths.token_path())
    service = build_service(creds)
    client = GmailClient(service)
    count = scan_mailbox(client, query, session_factory(paths.db_path()))
    console.print(f"[green]Scanned and cached[/] {count} messages")


if __name__ == "__main__":
    app()
