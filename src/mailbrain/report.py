"""Dry-run reporting: metrics summary + Rich table rendering."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from mailbrain.planner import PlannedMutation
from mailbrain.rules.models import MessageMeta


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
) -> None:
    console = console or Console()
    subjects = {m.gmail_id: m.subject for m in messages}
    table = Table(title="MailBrain — planned changes (dry-run)")
    table.add_column("Subject", overflow="ellipsis", max_width=48)
    table.add_column("Add labels")
    table.add_column("Archive")
    table.add_column("Read")
    for p in plans:
        table.add_row(
            subjects.get(p.gmail_id, p.gmail_id),
            ", ".join(p.add_labels),
            "yes" if p.archive else "",
            "yes" if p.mark_read else "",
        )
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
