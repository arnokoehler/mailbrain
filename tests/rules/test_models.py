from datetime import UTC, datetime

from mailbrain.rules.models import Classification, MessageMeta


def test_message_meta_is_frozen():
    m = MessageMeta(
        gmail_id="m1",
        sender="Booking <no-reply@booking.com>",
        subject="Receipt",
        internal_date=datetime(2026, 7, 1, tzinfo=UTC),
        current_labels=("INBOX", "UNREAD"),
    )
    assert m.gmail_id == "m1"
    assert m.current_labels == ("INBOX", "UNREAD")


def test_classification_defaults():
    c = Classification(gmail_id="m1", matched_rule_ids=("booking-payment",), add_labels=("Reizen",))
    assert c.archive is False
    assert c.mark_read is False
