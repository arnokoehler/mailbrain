"""MailBrain command-line interface."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import typer
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.logging import RichHandler
from sqlalchemy.orm import Session, sessionmaker

from mailbrain import config, paths
from mailbrain.apply import assert_no_unresolved_intents, execute_plan
from mailbrain.digest import (
    build_digest,
    current_iso_week,
    load_week_messages,
    mistral_summarizer,
    parse_iso_week,
    render_markdown,
)
from mailbrain.gmail.auth import build_service, load_credentials
from mailbrain.gmail.client import GmailClient
from mailbrain.locking import LockUnavailableError, process_lock
from mailbrain.notion import DigestPublisher, NotionClient, UrllibHttpTransport
from mailbrain.planner import PlannedMutation, plan_mutations
from mailbrain.report import (
    render_metrics,
    render_plan,
    render_run,
    render_safety,
    render_scan,
    summarize,
)
from mailbrain.rollback import plan_rollback, rollback_run, validate_rollback_plan
from mailbrain.rules.engine import classify as classify_messages
from mailbrain.rules.models import Classification, MessageMeta
from mailbrain.runs import abandon_run, inspect_run, list_runs, reconcile_run
from mailbrain.safety import PlanValidation, validate_plan
from mailbrain.scan import (
    ScanDriftError,
    ScanNotEligibleError,
    latest_scan_run,
    load_cached,
    require_eligible_scan,
    scan_mailbox,
    validate_live_candidates,
)
from mailbrain.storage.db import init_db, session_factory, upgrade_db
from mailbrain.storage.models import ScanRun

app = typer.Typer(help="MailBrain - local-first Gmail classifier.")
db_app = typer.Typer(help="Database maintenance commands.")
runs_app = typer.Typer(help="Inspect and resolve durable execution runs.")
app.add_typer(db_app, name="db")
app.add_typer(runs_app, name="runs")
console = Console()
DEFAULT_SETTINGS = Path("config/settings.yaml")
DEFAULT_RULES = Path("config/rules.yaml")


@app.callback()
def _callback() -> None:
    """MailBrain - local-first Gmail classifier."""


def _fail(message: str) -> NoReturn:
    console.print(f"[red]{message}[/]")
    raise typer.Exit(code=2)


def _settings(path: Path) -> config.Settings:
    try:
        return config.load_settings(path)
    except (OSError, ValidationError, ValueError) as error:
        _fail(f"Invalid settings at {path}: {error}")


def _factory() -> sessionmaker[Session]:
    try:
        return session_factory(paths.db_path())
    except Exception as error:
        _fail(str(error))


def _client(settings: config.Settings) -> GmailClient:
    if not paths.credentials_path().exists():
        _fail(
            f"credentials.json not found: {paths.credentials_path()}\n"
            "Download an OAuth desktop client from Google Cloud Console and save it there "
            "(see README)."
        )
    credentials = load_credentials(paths.credentials_path(), paths.token_path())
    return GmailClient(build_service(credentials), page_size=settings.gmail.page_size)


def _run_locked[Result](action: Callable[[], Result]) -> Result:
    try:
        with process_lock():
            return action()
    except LockUnavailableError as error:
        _fail(str(error))


def _latest_successful_scan(factory: sessionmaker[Session]) -> ScanRun:
    scan = latest_scan_run(factory)
    if scan is None:
        _fail("No scan is available; run mailbrain scan first.")
    if scan.status != "succeeded":
        _fail(f"Latest scan {scan.id} has status {scan.status}; run a fresh scan.")
    return scan


def _scan_age_minutes(scan: ScanRun, now: datetime | None = None) -> float:
    started_at = (
        scan.started_at.replace(tzinfo=UTC)
        if scan.started_at.tzinfo is None
        else scan.started_at
    )
    return ((now or datetime.now(UTC)) - started_at.astimezone(UTC)).total_seconds() / 60


def _build_plan(
    rules: Path, factory: sessionmaker[Session], scan_id: int
) -> tuple[
    list[MessageMeta],
    dict[str, set[str]],
    list[Classification],
    list[PlannedMutation],
    config.RulesFile,
]:
    messages, current = load_cached(factory, scan_id)
    loaded_rules = config.load_rules(rules)
    classifications = classify_messages(messages, loaded_rules.rules, datetime.now(UTC))
    plans = plan_mutations(classifications, current)
    return messages, current, classifications, plans, loaded_rules


def _validate(
    messages: list[MessageMeta],
    current: dict[str, set[str]],
    classifications: list[Classification],
    plans: list[PlannedMutation],
    settings: config.Settings,
) -> PlanValidation:
    inbox_count = sum("INBOX" in labels for labels in current.values())
    return validate_plan(classifications, plans, messages, inbox_count, settings.safety)


def _config_hash(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_report(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_text(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _render_plan(
    plans: list[PlannedMutation],
    messages: list[MessageMeta],
    scan: ScanRun,
    validation: PlanValidation,
    *,
    verbose: bool,
    pager: bool,
) -> None:
    context = console.pager(styles=True) if pager else nullcontext()
    with context:
        render_scan(scan, _scan_age_minutes(scan), console=console)
        render_plan(plans, messages, console=console, verbose=verbose)
        render_metrics(summarize(plans, scanned=len(messages)), console=console)
        render_safety(validation, console=console)


@app.command()
def init() -> None:
    """Create the local state directory and initialize it through migrations."""

    def action() -> None:
        app_dir = paths.ensure_app_dir()
        init_db(paths.db_path())
        console.print(f"[green]Initialized MailBrain at[/] {app_dir}")

    _run_locked(action)


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Upgrade the local database schema after creating a safe legacy backup."""

    def action() -> None:
        backup = upgrade_db(paths.db_path())
        if backup is None:
            console.print("[green]Database schema is current.[/]")
        else:
            console.print(f"[green]Database upgraded; backup:[/] {backup}")

    _run_locked(action)


@app.command()
def classify(
    rules: Path = typer.Option(  # noqa: B008
        DEFAULT_RULES, "--rules", help="Path to rules YAML."
    ),
    settings_path: Path = typer.Option(  # noqa: B008
        DEFAULT_SETTINGS, "--settings", help="Path to settings YAML."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show matched rules."),
    pager: bool = typer.Option(False, "--pager", help="Page output through $PAGER."),
) -> None:
    """Classify the latest complete scan and print an offline report."""
    if not rules.exists():
        _fail(f"Rules file not found: {rules}")
    settings = _settings(settings_path)

    def action() -> None:
        factory = _factory()
        scan = _latest_successful_scan(factory)
        messages, current, classifications, plans, _ = _build_plan(rules, factory, scan.id)
        validation = _validate(messages, current, classifications, plans, settings)
        _render_plan(plans, messages, scan, validation, verbose=verbose, pager=pager)

    _run_locked(action)


@app.command()
def scan(
    settings_path: Path = typer.Option(  # noqa: B008
        DEFAULT_SETTINGS, "--settings", help="Path to settings YAML."
    ),
    query: str | None = typer.Option(None, "--query", help="Override the configured query."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress scan progress."),
    resume: bool = typer.Option(False, "--resume", help="Perform a full fresh scan."),
) -> None:
    """Fetch Gmail metadata into a new explicit scan scope."""
    settings = _settings(settings_path)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_time=False, show_path=False, markup=True)],
        force=True,
    )
    logging.getLogger("mailbrain").setLevel(logging.WARNING if quiet else logging.INFO)

    def action() -> None:
        client = _client(settings)
        count = scan_mailbox(
            client,
            query or settings.gmail.scan_query,
            _factory(),
            batch_size=settings.gmail.batch_size,
            skip_cached=resume,
        )
        console.print(f"[green]Scanned and cached[/] {count} messages")

    _run_locked(action)


@app.command()
def digest(
    week: str | None = typer.Option(None, "--week", help="ISO week, for example 2026-W38."),
    rules: Path = typer.Option(  # noqa: B008
        DEFAULT_RULES, "--rules", help="Path to rules YAML."
    ),
    settings_path: Path = typer.Option(  # noqa: B008
        DEFAULT_SETTINGS, "--settings", help="Path to settings YAML."
    ),
    output: Path | None = typer.Option(  # noqa: B008
        None, "--output", help="Markdown output path."
    ),
    retry_uncertain: bool = typer.Option(
        False,
        "--retry-uncertain",
        help="Create after reconciliation cannot find an earlier uncertain page.",
    ),
) -> None:
    """Generate a weekly newsletter digest from locally cached Gmail metadata."""
    if not rules.exists():
        _fail(f"Rules file not found: {rules}")
    settings = _settings(settings_path)

    def action() -> None:
        selected_week = week or current_iso_week()
        try:
            normalized_week, start, end = parse_iso_week(selected_week)
        except ValueError as error:
            _fail(str(error))
        summarizer = None
        ai_generated = False
        if settings.ai.enabled:
            try:
                summarizer = mistral_summarizer(settings.ai)
            except ValueError as error:
                console.print(
                    f"[yellow]Mistral unavailable; using deterministic digest:[/] {error}"
                )
        messages = load_week_messages(_factory(), start, end)
        loaded_rules = config.load_rules(rules).rules
        try:
            weekly_digest = build_digest(normalized_week, messages, loaded_rules, summarizer)
            ai_generated = summarizer is not None
        except (
            OSError,
            TimeoutError,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            console.print(f"[yellow]Mistral failed; using deterministic digest:[/] {error}")
            weekly_digest = build_digest(normalized_week, messages, loaded_rules)
        markdown = render_markdown(weekly_digest)
        destination = output or paths.reports_dir() / f"{normalized_week}.md"
        _write_report(destination, markdown)
        console.print(markdown, markup=False)
        mode = "Mistral summary" if ai_generated else "deterministic overview"
        console.print(f"[green]Wrote {mode}:[/] {destination}")
        if not settings.notion.enabled:
            return
        if settings.notion.parent_page_id is None:
            console.print(
                "[yellow]Notion publishing skipped:[/] notion.parent_page_id is required"
            )
            return
        token = os.environ.get(settings.notion.token_env)
        if not token:
            console.print(
                f"[yellow]Notion publishing skipped:[/] {settings.notion.token_env} is required"
            )
            return
        publisher = DigestPublisher(
            _factory(),
            NotionClient(token, UrllibHttpTransport()),
            settings.notion.parent_page_id,
        )
        publication = publisher.publish(
            normalized_week, markdown, retry_uncertain=retry_uncertain
        )
        if publication.status == "created":
            target = publication.url or publication.page_id
            console.print(f"[green]Published Notion digest {normalized_week}:[/] {target}")
        elif publication.status == "recovered":
            console.print(
                f"[green]Recovered Notion digest {normalized_week} as page[/] "
                f"{publication.page_id}"
            )
        elif publication.status == "already_published":
            console.print(
                f"Notion digest {normalized_week} is already published as page "
                f"{publication.page_id}."
            )
        else:
            console.print(
                f"[yellow]Notion publication {publication.status}; local digest remains "
                f"available:[/] {publication.error}"
            )

    _run_locked(action)


@app.command()
def apply(
    scan_id: int | None = typer.Option(None, "--scan-id", help="Required scan ID for writes."),
    rules: Path = typer.Option(  # noqa: B008
        DEFAULT_RULES, "--rules", help="Path to rules YAML."
    ),
    settings_path: Path = typer.Option(  # noqa: B008
        DEFAULT_SETTINGS, "--settings", help="Path to settings YAML."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without Gmail writes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip only the confirmation prompt."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show matched rules."),
    pager: bool = typer.Option(False, "--pager", help="Page output through $PAGER."),
) -> None:
    """Apply a safe plan from the explicitly selected latest successful scan."""
    if not rules.exists():
        _fail(f"Rules file not found: {rules}")
    settings = _settings(settings_path)

    def action() -> None:
        factory = _factory()
        selected_scan = _latest_successful_scan(factory) if scan_id is None else None
        if not dry_run and scan_id is None:
            _fail("--scan-id is required for Gmail writes")
        if dry_run:
            scan = selected_scan or _latest_successful_scan(factory)
        else:
            if settings.safety.max_scan_age_minutes is None:
                _fail("Safety blocked writes: missing limit max_scan_age_minutes")
            try:
                assert scan_id is not None
                scan = require_eligible_scan(
                    factory, scan_id, settings.safety.max_scan_age_minutes
                )
            except ScanNotEligibleError as error:
                _fail(str(error))
        messages, current, classifications, plans, loaded_rules = _build_plan(
            rules, factory, scan.id
        )
        validation = _validate(messages, current, classifications, plans, settings)
        _render_plan(plans, messages, scan, validation, verbose=verbose, pager=pager)
        if dry_run:
            console.print("[yellow]dry-run[/] - no changes applied.")
            return
        if not validation.writes_allowed:
            _fail("Safety blocked Gmail writes.")
        with factory() as session:
            try:
                assert_no_unresolved_intents(session)
            except RuntimeError as error:
                _fail(str(error))
        if not plans:
            console.print("Nothing to apply.")
            return
        if not yes and not typer.confirm(f"Apply {len(plans)} change(s) to Gmail?"):
            raise typer.Abort()
        client = _client(settings)
        try:
            validate_live_candidates(client, factory, scan.id, [plan.gmail_id for plan in plans])
            max_scan_age_minutes = settings.safety.max_scan_age_minutes
            assert max_scan_age_minutes is not None
            require_eligible_scan(factory, scan.id, max_scan_age_minutes)
        except ScanDriftError as error:
            _fail(str(error))
        except ScanNotEligibleError as error:
            _fail(str(error))
        run_id = execute_plan(
            client,
            factory,
            plans,
            current,
            scan_id=scan.id,
            rules_hash=_config_hash(loaded_rules),
            safety_hash=_config_hash(settings.safety),
            batch_size=settings.gmail.batch_size,
        )
        state = inspect_run(factory, run_id)
        render_run(state, console)

    _run_locked(action)


@app.command()
def rollback(
    run_id: int = typer.Argument(..., help="The apply run ID to reverse."),
    settings_path: Path = typer.Option(  # noqa: B008
        DEFAULT_SETTINGS, "--settings", help="Path to settings YAML."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without Gmail writes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip only the confirmation prompt."),
) -> None:
    """Conservatively reverse eligible confirmed mutations from an apply run."""
    settings = _settings(settings_path)

    def action() -> None:
        factory = _factory()
        with factory() as session:
            try:
                assert_no_unresolved_intents(session)
            except RuntimeError as error:
                _fail(str(error))
        client = _client(settings)
        try:
            plan = plan_rollback(client, factory, run_id)
        except (ValueError, RuntimeError) as error:
            _fail(str(error))
        inbox_ids = client.list_message_ids("in:inbox") if plan.archive_message_ids else None
        validation = validate_rollback_plan(plan, settings.safety, inbox_ids)
        console.print(
            f"rollback {run_id}: eligible={validation.mutation_count} "
            f"archives={validation.archive_count} inbox={validation.inbox_count} "
            f"archive_fraction={validation.archive_fraction:.3f}"
        )
        for reason in validation.reasons:
            console.print(f"[red]safety blocker[/] {reason}")
        if dry_run:
            console.print("[yellow]dry-run[/] - no changes applied.")
            return
        if not validation.writes_allowed:
            _fail("Safety blocked rollback writes.")
        if not plan.mutation_ids:
            console.print("No eligible confirmed mutations to roll back.")
            return
        if not yes and not typer.confirm(f"Roll back {len(plan.mutation_ids)} mutation(s)?"):
            raise typer.Abort()
        count = rollback_run(
            client, factory, run_id, batch_size=settings.gmail.batch_size
        )
        console.print(f"[green]Confirmed rollback mutations:[/] {count}")

    _run_locked(action)


@runs_app.callback(invoke_without_command=True)
def runs_callback(ctx: typer.Context) -> None:
    """List durable execution runs."""
    if ctx.invoked_subcommand is not None:
        return

    _render_runs()


def _render_runs() -> None:
    def action() -> None:
        for state in list_runs(_factory()):
            render_run(state, console)

    _run_locked(action)


@runs_app.command("list")
def runs_list() -> None:
    """List durable execution runs."""
    _render_runs()


@runs_app.command("inspect")
def runs_inspect(run_id: int) -> None:
    """Inspect one run and its mutation statuses."""

    def action() -> None:
        try:
            render_run(inspect_run(_factory(), run_id), console)
        except ValueError as error:
            _fail(str(error))

    _run_locked(action)


@runs_app.command("reconcile")
def runs_reconcile(
    run_id: int,
    settings_path: Path = typer.Option(  # noqa: B008
        DEFAULT_SETTINGS, "--settings", help="Path to settings YAML."
    ),
) -> None:
    """Compare uncertain intent with Gmail and update only local state."""
    settings = _settings(settings_path)

    def action() -> None:
        try:
            render_run(reconcile_run(_client(settings), _factory(), run_id), console)
        except ValueError as error:
            _fail(str(error))

    _run_locked(action)


@runs_app.command("abandon")
def runs_abandon(
    run_id: int,
    reason: str = typer.Option(..., "--reason", help="Operator resolution reason."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm local abandonment."),
) -> None:
    """Close reconciled unresolved intent locally without Gmail writes."""

    def action() -> None:
        confirmed = yes or typer.confirm(f"Abandon unresolved intent for run {run_id}?")
        if not confirmed:
            raise typer.Abort()
        try:
            render_run(abandon_run(_factory(), run_id, reason, confirmed=True), console)
        except ValueError as error:
            _fail(str(error))

    _run_locked(action)


if __name__ == "__main__":
    app()
