"""MailBrain command-line interface."""

from __future__ import annotations

import typer
from rich.console import Console

from mailbrain import paths
from mailbrain.storage.db import init_db

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


if __name__ == "__main__":
    app()
