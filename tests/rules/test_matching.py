from datetime import UTC, datetime, timedelta

from mailbrain.config import Rule, RuleActions, RuleMatch
from mailbrain.rules.engine import rule_matches
from mailbrain.rules.models import MessageMeta

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _msg(sender="x@booking.com", subject="hello", days_old=0, labels=()):
    return MessageMeta(
        gmail_id="m1",
        sender=sender,
        subject=subject,
        internal_date=NOW - timedelta(days=days_old),
        current_labels=tuple(labels),
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


def test_has_label_matches_when_message_carries_the_label():
    rule = _rule(from_domain=["booking.com"], has_label=["CATEGORY_PROMOTIONS"])
    assert rule_matches(rule, _msg(labels=["INBOX", "CATEGORY_PROMOTIONS"]), NOW)


def test_has_label_does_not_match_when_label_absent():
    rule = _rule(from_domain=["booking.com"], has_label=["CATEGORY_PROMOTIONS"])
    assert not rule_matches(rule, _msg(labels=["INBOX"]), NOW)


def test_has_label_is_a_criterion_on_its_own():
    assert rule_matches(
        _rule(has_label=["CATEGORY_PROMOTIONS"]), _msg(labels=["CATEGORY_PROMOTIONS"]), NOW
    )


def test_exclude_on_has_label_vetoes_promotional_mail():
    rule = _rule_with_exclude(
        RuleMatch(from_domain=["coolblue.eu"]),
        RuleMatch(has_label=["CATEGORY_PROMOTIONS"]),
    )
    assert not rule_matches(
        rule,
        _msg(sender="info@noreply.coolblue.eu", labels=["INBOX", "CATEGORY_PROMOTIONS"]),
        NOW,
    )
    assert rule_matches(
        rule,
        _msg(sender="info@noreply.coolblue.eu", subject="Je bestelling", labels=["INBOX"]),
        NOW,
    )


def test_subject_not_contains_blocks_a_match():
    rule = _rule(from_domain=["booking.com"], subject_not_contains=["invoice"])
    assert not rule_matches(rule, _msg(subject="Your invoice"), NOW)


def test_subject_not_contains_allows_other_subjects():
    rule = _rule(from_domain=["booking.com"], subject_not_contains=["invoice"])
    assert rule_matches(rule, _msg(subject="Your booking"), NOW)


def test_subject_not_contains_is_case_insensitive():
    rule = _rule(from_domain=["booking.com"], subject_not_contains=["INVOICE"])
    assert not rule_matches(rule, _msg(subject="your invoice is ready"), NOW)


def test_subject_not_contains_alone_is_not_a_criterion():
    assert not rule_matches(_rule(subject_not_contains=["invoice"]), _msg(subject="hello"), NOW)


def _rule_with_excludes(match: RuleMatch, excludes: list[RuleMatch]) -> Rule:
    return Rule(id="r", match=match, exclude=excludes, actions=RuleActions(add_labels=["L"]))


def test_promotional_veto_spares_a_transactional_subject():
    """A vendor rule must skip ads but still label a real order Gmail misfiled."""
    rule = _rule_with_excludes(
        RuleMatch(from_domain=["coolblue.eu"]),
        [RuleMatch(has_label=["CATEGORY_PROMOTIONS"], subject_not_contains=["bestelling"])],
    )
    advert = _msg(
        sender="a@coolblue.eu", subject="Back to School-deals", labels=["CATEGORY_PROMOTIONS"]
    )
    misfiled_order = _msg(
        sender="a@coolblue.eu", subject="Je bestelling is verzonden", labels=["CATEGORY_PROMOTIONS"]
    )
    assert not rule_matches(rule, advert, NOW)
    assert rule_matches(rule, misfiled_order, NOW)


def test_exclude_list_vetoes_when_any_criteria_set_holds():
    rule = _rule_with_excludes(
        RuleMatch(from_domain=["coolblue.eu"]),
        [
            RuleMatch(has_label=["CATEGORY_PROMOTIONS"]),
            RuleMatch(subject_contains=["deals"]),
        ],
    )
    promo_by_category = _msg(
        sender="a@coolblue.eu", subject="Je bestelling", labels=["CATEGORY_PROMOTIONS"]
    )
    promo_by_subject = _msg(
        sender="a@coolblue.eu", subject="Back to School-deals", labels=["INBOX"]
    )
    real_order = _msg(
        sender="a@coolblue.eu", subject="Je bestelling is verzonden", labels=["INBOX"]
    )

    assert not rule_matches(rule, promo_by_category, NOW)
    assert not rule_matches(rule, promo_by_subject, NOW)
    assert rule_matches(rule, real_order, NOW)


def test_exclude_list_with_only_empty_criteria_never_vetoes():
    rule = _rule_with_excludes(RuleMatch(subject_contains=["factuur"]), [RuleMatch()])
    assert rule_matches(rule, _msg(subject="uw factuur"), NOW)
