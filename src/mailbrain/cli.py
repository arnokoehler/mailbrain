"""MailBrain command-line interface."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler

from mailbrain import config, paths
from mailbrain.apply import execute_plan
from mailbrain.gmail.auth import build_service, load_credentials
from mailbrain.gmail.client import GmailClient
from mailbrain.planner import plan_mutations
from mailbrain.report import render_metrics, render_plan, summarize
from mailbrain.rollback import rollback_run
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


def _build_plan(rules: Path, factory):  # type: ignore[no-untyped-def]
    """Load the cache, classify against `rules`, and diff to planned mutations.

    Shared by `classify` (preview) and `apply` so both derive the plan the
    same way. Returns (messages, current_labels, plans).
    """
    messages, current = load_cached(factory)
    classifications = classify_messages(messages, config.load_rules(rules).rules, datetime.now(UTC))
    return messages, current, plan_mutations(classifications, current)


@app.command()
def classify(
    rules: Path = typer.Option(  # noqa: B008 - Typer option factory
        Path("config/rules.yaml"), "--rules", help="Path to rules YAML."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show which rule(s) matched each message."
    ),
) -> None:
    """Classify cached mail and print a dry-run report (no changes applied)."""
    if not rules.exists():
        console.print(f"[red]Rules file not found:[/] {rules}")
        raise typer.Exit(code=2)
    factory = session_factory(paths.db_path())
    messages, _current, plans = _build_plan(rules, factory)
    render_plan(plans, messages, console=console, verbose=verbose)
    render_metrics(summarize(plans, scanned=len(messages)), console=console)


@app.command()
def scan(
    query: str = typer.Option("in:inbox", "--query", help="Gmail search query."),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress per-batch scan progress output."
    ),
    resume: bool = typer.Option(
        False, "--resume", help="Skip ids already cached (resume an interrupted scan)."
    ),
) -> None:
    """Fetch Gmail metadata for the query and cache it locally."""
    # Root stays at WARNING so noisy third-party INFO (googleapiclient, etc.)
    # is suppressed; only mailbrain's own progress logs are shown.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_time=False, show_path=False, markup=True)],
        force=True,
    )
    logging.getLogger("mailbrain").setLevel(logging.WARNING if quiet else logging.INFO)
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
    count = scan_mailbox(client, query, session_factory(paths.db_path()), skip_cached=resume)
    console.print(f"[green]Scanned and cached[/] {count} messages")


@app.command()
def apply(
    rules: Path = typer.Option(  # noqa: B008 - Typer option factory
        Path("config/rules.yaml"), "--rules", help="Path to rules YAML."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview the plan without writing anything to Gmail."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt (for scripts)."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show which rule(s) matched each message."
    ),
) -> None:
    """Apply the classification plan to Gmail. Writes changes unless --dry-run."""
    if not rules.exists():
        console.print(f"[red]Rules file not found:[/] {rules}")
        raise typer.Exit(code=2)
    factory = session_factory(paths.db_path())
    messages, current, plans = _build_plan(rules, factory)
    render_plan(plans, messages, console=console, verbose=verbose)
    render_metrics(summarize(plans, scanned=len(messages)), console=console)

    if dry_run:
        console.print("[yellow]dry-run[/] — no changes applied.")
        return
    if not plans:
        console.print("Nothing to apply.")
        return

    if not paths.credentials_path().exists():
        console.print(
            f"[red]credentials.json not found:[/] {paths.credentials_path()}\n"
            "Download an OAuth desktop client from Google Cloud Console and save it there "
            "(see README)."
        )
        raise typer.Exit(code=2)
    if not yes:
        typer.confirm(f"Apply {len(plans)} change(s) to Gmail?", abort=True)
    creds = load_credentials(paths.credentials_path(), paths.token_path())
    client = GmailClient(build_service(creds))
    run_id = execute_plan(client, factory, plans, current)
    console.print(f"[green]Applied[/] {len(plans)} changes (run {run_id}).")


@app.command()
def rollback(run_id: int = typer.Argument(..., help="The run id to reverse.")) -> None:
    """Reverse every mutation made by a previous run."""
    if not paths.credentials_path().exists():
        console.print(
            f"[red]credentials.json not found:[/] {paths.credentials_path()}\n"
            "Download an OAuth desktop client from Google Cloud Console and save it there "
            "(see README)."
        )
        raise typer.Exit(code=2)
    creds = load_credentials(paths.credentials_path(), paths.token_path())
    client = GmailClient(build_service(creds))
    count = rollback_run(client, session_factory(paths.db_path()), run_id)
    console.print(f"[green]Rolled back[/] {count} messages from run {run_id}.")


if __name__ == "__main__":
    app()
