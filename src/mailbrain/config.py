"""Pydantic configuration models and YAML loaders."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_validator,
)

NonNegativeStrictInt = Annotated[StrictInt, Field(ge=0)]
PositiveStrictInt = Annotated[StrictInt, Field(gt=0)]
FractionStrictFloat = Annotated[StrictFloat, Field(ge=0, le=1)]


class GmailSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_query: str = "in:inbox"
    page_size: PositiveStrictInt = 500
    batch_size: Annotated[StrictInt, Field(gt=0, le=1000)] = 1000


class AISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "mistral"
    model: str = "mistral-small-latest"
    api_key_env: str = "MISTRAL_API_KEY"
    max_digest_messages: PositiveStrictInt = 100
    confidence_floor: float = 0.85


class NotionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    parent_page_id: str | None = None
    token_env: str = "NOTION_TOKEN"

    @field_validator("parent_page_id", "token_env")
    @classmethod
    def _validate_notion_values(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Notion settings must not be empty")
        return value


class SafetySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_mutations: NonNegativeStrictInt | None = None
    max_archives: NonNegativeStrictInt | None = None
    max_archive_fraction: FractionStrictFloat | None = None
    max_scan_age_minutes: PositiveStrictInt | None = None
    protected_label_prefixes: list[str] = Field(default_factory=list)
    protected_subject_contains: list[str] = Field(default_factory=list)
    protected_subject_regex: list[str] = Field(default_factory=list)

    @field_validator("protected_label_prefixes", "protected_subject_contains")
    @classmethod
    def _validate_nonempty_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("protected safety criteria must not be empty")
        return values

    @field_validator("protected_subject_regex")
    @classmethod
    def _validate_subject_regex(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            if not pattern:
                raise ValueError("protected subject regex must not be empty")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid protected_subject_regex {pattern!r}: {exc}") from exc
        return patterns


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gmail: GmailSettings = Field(default_factory=GmailSettings)
    ai: AISettings = Field(default_factory=AISettings)
    notion: NotionSettings = Field(default_factory=NotionSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)


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
