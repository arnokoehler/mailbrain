"""Dry-run reporting: metrics summary + Rich table rendering."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from mailbrain.planner import PlannedMutation
from mailbrain.rules.models import MessageMeta
from mailbrain.runs import RunState
from mailbrain.safety import PlanValidation
from mailbrain.storage.models import ScanRun


@dataclass(frozen=True)
class Metrics:
    scanned: int
    planned: int
    labeled: int
    archived: int
    marked_read: int


def summarize(plans: list[PlannedMutation], scanned: int) -> Metrics:
    return Metrics(
        scanned=scanned,
        planned=len(plans),
        labeled=sum(1 for p in plans if p.add_labels),
        archived=sum(1 for p in plans if p.archive),
        marked_read=sum(1 for p in plans if p.mark_read),
    )


def render_plan(
    plans: list[PlannedMutation],
    messages: list[MessageMeta],
    console: Console | None = None,
    verbose: bool = False,
) -> None:
    console = console or Console()
    subjects = {m.gmail_id: m.subject for m in messages}
    table = Table(title="MailBrain — planned changes (dry-run)")
    table.add_column("Subject", overflow="ellipsis", max_width=48)
    if verbose:
        table.add_column("Matched rules")
    table.add_column("Add labels")
    table.add_column("Archive")
    table.add_column("Read")
    for p in plans:
        row = [subjects.get(p.gmail_id, p.gmail_id)]
        if verbose:
            row.append(", ".join(p.matched_rule_ids) or "—")
        row += [
            ", ".join(p.add_labels),
            "yes" if p.archive else "",
            "yes" if p.mark_read else "",
        ]
        table.add_row(*row)
    console.print(table)


def render_metrics(metrics: Metrics, console: Console | None = None) -> None:
    console = console or Console()
    console.print(
        f"[bold]scanned[/] {metrics.scanned}  "
        f"[bold]planned[/] {metrics.planned}  "
        f"[bold]labeled[/] {metrics.labeled}  "
        f"[bold]archived[/] {metrics.archived}  "
        f"[bold]read[/] {metrics.marked_read}"
    )


def render_scan(scan: ScanRun, age_minutes: float, console: Console | None = None) -> None:
    console = console or Console()
    console.print(
        f"[bold]scan[/] {scan.id}  [bold]query[/] {scan.query!r}  "
        f"[bold]age[/] {age_minutes:.1f}m  [bold]status[/] {scan.status}"
    )


def render_safety(validation: PlanValidation, console: Console | None = None) -> None:
    console = console or Console()
    for limit in validation.missing_limits:
        console.print(f"[yellow]safety blocker[/] missing limit: {limit}")
    for reason in validation.reasons:
        details = reason.detail
        if reason.message_ids:
            details += f"; messages={','.join(reason.message_ids)}"
        if reason.rule_ids:
            details += f"; rules={','.join(reason.rule_ids)}"
        console.print(f"[red]safety blocker[/] {reason.code}: {details}")


def render_run(state: RunState, console: Console | None = None) -> None:
    console = console or Console()
    console.print(
        f"run {state.id} type={state.run_type} status={state.status} "
        f"intended={state.intended_count} confirmed={state.applied_count} "
        f"failed={state.failed_count} uncertain={state.uncertain_count} "
        f"conflict={state.conflict_count}"
    )
    for mutation in state.mutations:
        console.print(
            f"  mutation {mutation.id} message={mutation.message_gmail_id} "
            f"status={mutation.status} batch={mutation.batch_id or '-'}"
        )
    for intent in state.label_intents:
        console.print(
            f"  label-intent {intent.id} label={intent.label_name} "
            f"status={intent.status} gmail-id={intent.gmail_label_id or '-'}"
        )
