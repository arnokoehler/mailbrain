"""Resolve label names to Gmail IDs, auto-creating missing nested labels."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from mailbrain.storage.models import Label


class SupportsLabelCreate(Protocol):
    def create_label(self, name: str) -> str: ...


def parent_paths(name: str) -> list[str]:
    """['A', 'A/B', 'A/B/C'] for 'A/B/C' — each nested ancestor plus the leaf."""
    parts = name.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def name_to_id(session: Session) -> dict[str, str]:
    return {lbl.name: lbl.gmail_id for lbl in session.scalars(select(Label)).all()}


def ensure_label(
    client: SupportsLabelCreate,
    session: Session,
    name: str,
    cache: dict[str, str],
) -> str:
    """Return the Gmail id for `name`, creating it (and any missing parents) first.

    `cache` is a name->id map (from name_to_id); it is updated in place as labels
    are created so repeated calls in one run stay consistent.
    """
    for path in parent_paths(name):
        if path not in cache:
            new_id = client.create_label(path)
            session.add(Label(gmail_id=new_id, name=path))
            cache[path] = new_id
    return cache[name]
