"""Pure validation of planned mailbox mutations."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from mailbrain.config import SafetySettings
from mailbrain.planner import PlannedMutation
from mailbrain.rules.models import Classification, MessageMeta

REQUIRED_LIMITS = (
    "max_mutations",
    "max_archives",
    "max_archive_fraction",
    "max_scan_age_minutes",
)


@dataclass(frozen=True)
class SafetyCounts:
    mutations: int
    archives: int
    inbox_messages: int
    archive_fraction: float


@dataclass(frozen=True)
class SafetyReason:
    code: str
    detail: str
    count: int = 0
    message_ids: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanValidation:
    counts: SafetyCounts
    reasons: tuple[SafetyReason, ...]
    missing_limits: tuple[str, ...]

    @property
    def preview_allowed(self) -> bool:
        return True

    @property
    def writes_allowed(self) -> bool:
        return not self.reasons and not self.missing_limits

    @property
    def message_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item for reason in self.reasons for item in reason.message_ids}))

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item for reason in self.reasons for item in reason.rule_ids}))


def label_matches_prefix(label: str, prefix: str) -> bool:
    normalized_label = label.casefold()
    normalized_prefix = prefix.rstrip("/").casefold()
    return normalized_label == normalized_prefix or normalized_label.startswith(
        normalized_prefix + "/"
    )


def validate_plan(
    classifications: Iterable[Classification],
    mutations: Iterable[PlannedMutation],
    messages: Iterable[MessageMeta] | Mapping[str, MessageMeta],
    inbox_message_count: int,
    settings: SafetySettings,
) -> PlanValidation:
    classification_by_id = {item.gmail_id: item for item in classifications}
    mutation_by_id = {item.gmail_id: item for item in mutations if not item.is_noop()}
    if isinstance(messages, Mapping):
        message_by_id = dict(messages)
    else:
        message_by_id = {item.gmail_id: item for item in messages}

    mutation_ids = set(mutation_by_id)
    archive_ids = {gmail_id for gmail_id, item in mutation_by_id.items() if item.archive}
    archive_fraction = len(archive_ids) / inbox_message_count if inbox_message_count > 0 else 0.0
    counts = SafetyCounts(
        mutations=len(mutation_ids),
        archives=len(archive_ids),
        inbox_messages=inbox_message_count,
        archive_fraction=archive_fraction,
    )

    reasons: list[SafetyReason] = []
    if inbox_message_count < 0:
        reasons.append(
            SafetyReason("invalid_inbox_count", "INBOX message count cannot be negative")
        )
    if settings.max_mutations is not None and counts.mutations > settings.max_mutations:
        reasons.append(
            _volume_reason(
                "max_mutations_exceeded",
                counts.mutations,
                settings.max_mutations,
                mutation_ids,
                classification_by_id,
            )
        )
    if settings.max_archives is not None and counts.archives > settings.max_archives:
        reasons.append(
            _volume_reason(
                "max_archives_exceeded",
                counts.archives,
                settings.max_archives,
                archive_ids,
                classification_by_id,
            )
        )
    if archive_ids and inbox_message_count == 0:
        reasons.append(
            _reason_for_messages(
                "archive_fraction_unavailable",
                "archive fraction requires a nonzero selected INBOX count",
                archive_ids,
                classification_by_id,
            )
        )
    elif (
        settings.max_archive_fraction is not None
        and counts.archive_fraction > settings.max_archive_fraction
    ):
        reasons.append(
            _volume_reason(
                "max_archive_fraction_exceeded",
                counts.archive_fraction,
                settings.max_archive_fraction,
                archive_ids,
                classification_by_id,
            )
        )

    protected_ids = {
        gmail_id
        for gmail_id, classification in classification_by_id.items()
        if _is_protected(message_by_id.get(gmail_id), classification, settings)
    }
    protected_actions = {
        "protected_archive": {
            gmail_id
            for gmail_id in protected_ids
            if gmail_id in mutation_by_id and mutation_by_id[gmail_id].archive
        },
        "protected_mark_read": {
            gmail_id
            for gmail_id in protected_ids
            if gmail_id in mutation_by_id and mutation_by_id[gmail_id].mark_read
        },
        "protected_to_be_removed": {
            gmail_id
            for gmail_id in protected_ids
            if any(
                label_matches_prefix(label, "to-be-removed")
                for label in mutation_by_id.get(
                    gmail_id,
                    PlannedMutation(gmail_id, (), False, False),
                ).add_labels
            )
        },
    }
    details = {
        "protected_archive": "protected messages cannot be archived",
        "protected_mark_read": "protected messages cannot be marked read",
        "protected_to_be_removed": "protected messages cannot receive to-be-removed",
    }
    for code, message_ids in protected_actions.items():
        if message_ids:
            reasons.append(
                _reason_for_messages(
                    code,
                    details[code],
                    message_ids,
                    classification_by_id,
                )
            )

    missing_limits = tuple(
        name for name in REQUIRED_LIMITS if getattr(settings, name) is None
    )
    return PlanValidation(counts, tuple(reasons), missing_limits)


def _is_protected(
    message: MessageMeta | None,
    classification: Classification,
    settings: SafetySettings,
) -> bool:
    current_labels = message.current_labels if message else ()
    labels = (*current_labels, *classification.add_labels)
    if any(
        label_matches_prefix(label, prefix)
        for label in labels
        for prefix in settings.protected_label_prefixes
    ):
        return True
    subject = message.subject if message else ""
    if any(value.casefold() in subject.casefold() for value in settings.protected_subject_contains):
        return True
    return any(re.search(pattern, subject) for pattern in settings.protected_subject_regex)


def _volume_reason(
    code: str,
    actual: int | float,
    limit: int | float,
    message_ids: set[str],
    classifications: Mapping[str, Classification],
) -> SafetyReason:
    return _reason_for_messages(
        code,
        f"actual {actual} exceeds configured limit {limit}",
        message_ids,
        classifications,
    )


def _reason_for_messages(
    code: str,
    detail: str,
    message_ids: set[str],
    classifications: Mapping[str, Classification],
) -> SafetyReason:
    sorted_message_ids = tuple(sorted(message_ids))
    rule_ids = tuple(
        sorted(
            {
                rule_id
                for gmail_id in message_ids
                for rule_id in classifications.get(
                    gmail_id,
                    Classification(gmail_id, (), ()),
                ).matched_rule_ids
            }
        )
    )
    return SafetyReason(code, detail, len(message_ids), sorted_message_ids, rule_ids)
