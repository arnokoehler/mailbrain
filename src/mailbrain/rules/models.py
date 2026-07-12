"""Immutable domain objects for the rules engine (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MessageMeta:
    """Metadata for one message, as the engine sees it."""

    gmail_id: str
    sender: str
    subject: str
    internal_date: datetime
    current_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Classification:
    """The desired outcome for one message after rule matching."""

    gmail_id: str
    matched_rule_ids: tuple[str, ...]
    add_labels: tuple[str, ...]
    archive: bool = False
    mark_read: bool = False
