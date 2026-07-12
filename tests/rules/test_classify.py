from datetime import UTC, datetime

from mailbrain.config import Rule, RuleActions, RuleMatch
from mailbrain.rules.engine import classify
from mailbrain.rules.models import MessageMeta

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _rule(rid, labels, archive=False, mark_read=False, **match):
    return Rule(
        id=rid,
        match=RuleMatch(**match),
        actions=RuleActions(add_labels=labels, archive=archive, mark_read=mark_read),
    )


def _msg(gmail_id, sender="x@booking.com", subject="hi"):
    return MessageMeta(gmail_id=gmail_id, sender=sender, subject=subject, internal_date=NOW)


def test_unmatched_message_is_skipped():
    rules = [_rule("r", ["L"], from_domain=["nope.com"])]
    assert classify([_msg("m1")], rules, NOW) == []


def test_single_rule_classification():
    rules = [_rule("booking", ["Reizen"], archive=True, from_domain=["booking.com"])]
    result = classify([_msg("m1")], rules, NOW)
    assert len(result) == 1
    c = result[0]
    assert c.gmail_id == "m1"
    assert c.matched_rule_ids == ("booking",)
    assert c.add_labels == ("Reizen",)
    assert c.archive is True
    assert c.mark_read is False


def test_multiple_rules_union_labels_and_or_booleans():
    rules = [
        _rule("a", ["Reizen"], archive=False, from_domain=["booking.com"]),
        _rule("b", ["Administratie"], archive=True, subject_contains=["hi"]),
    ]
    result = classify([_msg("m1")], rules, NOW)
    assert len(result) == 1
    c = result[0]
    assert c.matched_rule_ids == ("a", "b")
    assert c.add_labels == ("Administratie", "Reizen")
    assert c.archive is True


def test_duplicate_labels_deduped():
    rules = [
        _rule("a", ["Reizen"], from_domain=["booking.com"]),
        _rule("b", ["Reizen"], subject_contains=["hi"]),
    ]
    c = classify([_msg("m1")], rules, NOW)[0]
    assert c.add_labels == ("Reizen",)
