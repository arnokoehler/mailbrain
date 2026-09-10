"""Pydantic configuration models and YAML loaders."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class GmailSettings(BaseModel):
    scan_query: str = "in:inbox"
    page_size: int = 500
    batch_size: int = 1000


class AISettings(BaseModel):
    enabled: bool = False
    provider: str = "mistral"
    confidence_floor: float = 0.85


class NotionSettings(BaseModel):
    enabled: bool = False
    parent_page: str = "Personal/Mail System/Weekly Digest"


class Settings(BaseModel):
    gmail: GmailSettings = Field(default_factory=GmailSettings)
    ai: AISettings = Field(default_factory=AISettings)
    notion: NotionSettings = Field(default_factory=NotionSettings)


class RuleMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_domain: list[str] = Field(default_factory=list)
    subject_contains: list[str] = Field(default_factory=list)
    subject_not_contains: list[str] = Field(default_factory=list)
    subject_regex: list[str] = Field(default_factory=list)
    has_label: list[str] = Field(default_factory=list)
    older_than_days: int | None = None

    @field_validator("subject_regex")
    @classmethod
    def _validate_regex(cls, patterns: list[str]) -> list[str]:
        for pat in patterns:
            try:
                re.compile(pat)
            except re.error as exc:
                raise ValueError(f"invalid subject_regex {pat!r}: {exc}") from exc
        return patterns


class RuleActions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    add_labels: list[str] = Field(default_factory=list)
    archive: bool = False
    mark_read: bool = False


class Rule(BaseModel):
    id: str
    match: RuleMatch
    exclude: RuleMatch | list[RuleMatch] | None = None
    actions: RuleActions

    @property
    def exclude_sets(self) -> list[RuleMatch]:
        if self.exclude is None:
            return []
        if isinstance(self.exclude, RuleMatch):
            return [self.exclude]
        return self.exclude


class RulesFile(BaseModel):
    rules: list[Rule] = Field(default_factory=list)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text())
    return data if isinstance(data, dict) else {}


def load_settings(path: Path) -> Settings:
    return Settings.model_validate(_read_yaml(path))


def load_rules(path: Path) -> RulesFile:
    return RulesFile.model_validate(_read_yaml(path))
