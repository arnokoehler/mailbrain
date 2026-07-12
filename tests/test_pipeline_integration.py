from datetime import UTC, datetime
from unittest.mock import MagicMock

from mailbrain.config import Rule, RuleActions, RuleMatch
from mailbrain.planner import plan_mutations
from mailbrain.rules.engine import classify
from mailbrain.scan import load_cached, scan_mailbox
from mailbrain.storage import db


def _client_with_old_message():
    client = MagicMock()
    client.list_message_ids.return_value = ["old1"]
    client.list_labels.return_value = {"INBOX": "INBOX"}
    client.get_metadata.side_effect = [
        {
            "gmail_id": "old1",
            "thread_id": "t",
            "snippet": "s",
            "sender": "noreply@example.com",
            "subject": "Password reset requested",
            "label_ids": ["INBOX"],
            "internal_date_ms": 1262304000000,  # 2010-01-01, definitely > 3 days old
        }
    ]
    return client


def test_older_than_days_rule_survives_db_roundtrip(tmp_path):
    # Regression: SQLite strips tzinfo; load_cached must return tz-aware datetimes
    # so the engine's (now - internal_date) subtraction does not raise TypeError.
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    scan_mailbox(_client_with_old_message(), "in:inbox", factory)

    messages, current = load_cached(factory)
    # internal_date must be timezone-aware after the round-trip
    assert messages[0].internal_date.tzinfo is not None

    rule = Rule(
        id="pw",
        match=RuleMatch(subject_regex=["(?i)password reset"], older_than_days=3),
        actions=RuleActions(add_labels=["to-be-removed"], archive=True),
    )
    # This line raised TypeError before the fix.
    classifications = classify(messages, [rule], datetime.now(UTC))
    assert len(classifications) == 1
    assert classifications[0].add_labels == ("to-be-removed",)

    plans = plan_mutations(classifications, current)
    assert plans[0].archive is True  # message is in INBOX
