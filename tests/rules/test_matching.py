from datetime import UTC, datetime, timedelta

from mailbrain.config import Rule, RuleActions, RuleMatch
from mailbrain.rules.engine import rule_matches
from mailbrain.rules.models import MessageMeta

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _msg(sender="x@booking.com", subject="hello", days_old=0):
    return MessageMeta(
        gmail_id="m1",
        sender=sender,
        subject=subject,
        internal_date=NOW - timedelta(days=days_old),
    )


def _rule(**match_kwargs):
    return Rule(
        id="r",
        match=RuleMatch(**match_kwargs),
        actions=RuleActions(add_labels=["L"]),
    )


def test_from_domain_exact_match():
    assert rule_matches(_rule(from_domain=["booking.com"]), _msg(sender="a@booking.com"), NOW)


def test_from_domain_with_display_name():
    assert rule_matches(
        _rule(from_domain=["booking.com"]),
        _msg(sender="Booking.com <no-reply@booking.com>"),
        NOW,
    )


def test_from_domain_subdomain_matches():
    assert rule_matches(
        _rule(from_domain=["booking.com"]), _msg(sender="x@mail.booking.com"), NOW
    )


def test_from_domain_no_match():
    assert not rule_matches(_rule(from_domain=["booking.com"]), _msg(sender="x@other.com"), NOW)


def test_subject_contains_case_insensitive():
    assert rule_matches(_rule(subject_contains=["receipt"]), _msg(subject="Your RECEIPT"), NOW)


def test_subject_regex():
    assert rule_matches(
        _rule(subject_regex=["(?i)password reset"]), _msg(subject="Password Reset Requested"), NOW
    )


def test_older_than_days_true():
    assert rule_matches(_rule(older_than_days=3), _msg(days_old=5), NOW)


def test_older_than_days_false():
    assert not rule_matches(_rule(older_than_days=3), _msg(days_old=1), NOW)


def test_empty_match_never_matches():
    assert not rule_matches(_rule(), _msg(), NOW)


def test_all_present_criteria_must_hold():
    rule = _rule(from_domain=["booking.com"], subject_contains=["invoice"])
    assert not rule_matches(rule, _msg(sender="a@booking.com", subject="receipt"), NOW)


def test_older_than_days_boundary_is_inclusive():
    # age exactly == threshold must match (>= semantics)
    assert rule_matches(_rule(older_than_days=3), _msg(days_old=3), NOW)


def test_sender_without_at_sign_does_not_match_domain_rule():
    assert not rule_matches(_rule(from_domain=["booking.com"]), _msg(sender="garbage"), NOW)


def test_rule_domain_case_insensitive():
    assert rule_matches(_rule(from_domain=["Booking.COM"]), _msg(sender="a@booking.com"), NOW)


def _rule_with_exclude(match: RuleMatch, exclude: RuleMatch) -> Rule:
    return Rule(id="r", match=match, exclude=exclude, actions=RuleActions(add_labels=["L"]))


def test_exclude_vetoes_an_otherwise_matching_rule():
    rule = _rule_with_exclude(
        RuleMatch(subject_contains=["factuur"]),
        RuleMatch(subject_contains=["Kenteken"]),
    )
    assert not rule_matches(rule, _msg(subject="factuur (Kenteken K-830-HL)"), NOW)


def test_exclude_does_not_veto_when_its_criteria_absent():
    rule = _rule_with_exclude(
        RuleMatch(subject_contains=["factuur"]),
        RuleMatch(subject_contains=["Kenteken"]),
    )
    assert rule_matches(rule, _msg(subject="factuur zonder auto"), NOW)


def test_empty_exclude_never_vetoes():
    rule = _rule_with_exclude(RuleMatch(subject_contains=["factuur"]), RuleMatch())
    assert rule_matches(rule, _msg(subject="uw factuur"), NOW)
