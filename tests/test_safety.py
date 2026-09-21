from datetime import UTC, datetime

from mailbrain.config import SafetySettings
from mailbrain.planner import PlannedMutation
from mailbrain.rules.models import Classification, MessageMeta
from mailbrain.safety import label_matches_prefix, validate_plan


def settings(**values):
    defaults = {
        "max_mutations": 10,
        "max_archives": 10,
        "max_archive_fraction": 1.0,
        "max_scan_age_minutes": 60,
    }
    return SafetySettings(**(defaults | values))


def classification(
    gmail_id="m1", add_labels=(), archive=False, mark_read=False, rule_ids=("rule-1",)
):
    return Classification(gmail_id, rule_ids, add_labels, archive, mark_read)


def mutation(gmail_id="m1", add_labels=(), archive=False, mark_read=False):
    return PlannedMutation(gmail_id, add_labels, archive, mark_read, ("rule-1",))


def message(gmail_id="m1", subject="Routine", labels=("INBOX", "UNREAD")):
    return MessageMeta(gmail_id, "sender@example.com", subject, datetime.now(UTC), labels)


def test_exact_limits_and_zero_noop_are_safe():
    exact = validate_plan(
        [classification(archive=True)],
        [mutation(archive=True)],
        [message()],
        1,
        settings(max_mutations=1, max_archives=1, max_archive_fraction=1.0),
    )
    empty = validate_plan([], [], [], 0, settings(max_mutations=0, max_archives=0))
    assert exact.writes_allowed
    assert exact.counts.archive_fraction == 1.0
    assert empty.writes_allowed
    assert empty.counts.archive_fraction == 0.0


def test_actual_mutation_deltas_drive_volume_limits():
    result = validate_plan(
        [classification("m1"), classification("m2")],
        [mutation("m1", ("Label",)), mutation("m2", ("Label",))],
        [message("m1"), message("m2")],
        2,
        settings(max_mutations=1),
    )
    reason = result.reasons[0]
    assert not result.writes_allowed
    assert reason.code == "max_mutations_exceeded"
    assert reason.count == 2
    assert reason.message_ids == ("m1", "m2")
    assert reason.rule_ids == ("rule-1",)


def test_missing_limits_allow_preview_but_block_writes():
    result = validate_plan([], [], [], 0, SafetySettings())
    assert result.preview_allowed
    assert not result.writes_allowed
    assert result.reasons == ()
    assert result.missing_limits == (
        "max_mutations",
        "max_archives",
        "max_archive_fraction",
        "max_scan_age_minutes",
    )


def test_prefix_matching_respects_slash_boundary():
    assert label_matches_prefix("A", "A")
    assert label_matches_prefix("A/B", "A")
    assert not label_matches_prefix("AB", "A")


def test_full_classification_blocks_protected_actions_even_when_label_delta_is_noop():
    result = validate_plan(
        [
            classification(
                add_labels=("Protected/Payroll", "to-be-removed"),
                archive=True,
                mark_read=True,
            )
        ],
        [mutation(add_labels=("to-be-removed",), archive=True, mark_read=True)],
        [message(labels=("INBOX", "UNREAD", "Protected/Payroll"))],
        1,
        settings(protected_label_prefixes=["Protected"]),
    )
    assert {reason.code for reason in result.reasons} == {
        "protected_archive",
        "protected_mark_read",
        "protected_to_be_removed",
    }
    assert result.message_ids == ("m1",)
    assert result.rule_ids == ("rule-1",)


def test_protected_actions_without_actual_delta_are_safe():
    result = validate_plan(
        [classification(add_labels=("Protected/Payroll",), archive=True, mark_read=True)],
        [],
        [message(labels=("Protected/Payroll",))],
        0,
        settings(protected_label_prefixes=["Protected"]),
    )
    assert result.writes_allowed


def test_subject_protection_is_not_negated_by_promotional_wording():
    result = validate_plan(
        [classification(archive=True)],
        [mutation(archive=True)],
        [message(subject="Korting op uw factuur")],
        1,
        settings(protected_subject_contains=["factuur"]),
    )
    assert [reason.code for reason in result.reasons] == ["protected_archive"]


def test_proposed_protected_label_allows_label_only_classification():
    result = validate_plan(
        [classification(add_labels=("Protected/Travel",))],
        [mutation(add_labels=("Protected/Travel",))],
        [message()],
        1,
        settings(protected_label_prefixes=["Protected"]),
    )
    assert result.writes_allowed
