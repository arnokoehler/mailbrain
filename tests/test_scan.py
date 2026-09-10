from unittest.mock import MagicMock

from sqlalchemy import select

from mailbrain.scan import load_cached, scan_mailbox
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message


def _fake_client():
    client = MagicMock()
    client.list_message_ids.return_value = ["m1", "m2"]
    client.list_labels.return_value = {"INBOX": "INBOX", "Label_1": "Reizen"}
    client.get_metadata.side_effect = [
        {
            "gmail_id": "m1",
            "thread_id": "t1",
            "snippet": "hi",
            "sender": "a@booking.com",
            "subject": "Receipt",
            "label_ids": ["INBOX", "Label_1"],
            "internal_date_ms": 1751328000000,
        },
        {
            "gmail_id": "m2",
            "thread_id": "t2",
            "snippet": "yo",
            "sender": "b@degiro.nl",
            "subject": "Statement",
            "label_ids": ["INBOX"],
            "internal_date_ms": 1751328000000,
        },
    ]
    return client


def test_scan_caches_messages_and_labels(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)

    count = scan_mailbox(_fake_client(), "in:inbox", factory)
    assert count == 2

    with factory() as s:
        assert s.scalar(select(Message).where(Message.gmail_id == "m1")) is not None
        assert s.scalar(select(Label).where(Label.name == "Reizen")) is not None


def test_scan_is_idempotent_upsert(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    scan_mailbox(_fake_client(), "in:inbox", factory)
    scan_mailbox(_fake_client(), "in:inbox", factory)
    with factory() as s:
        rows = s.scalars(select(Message)).all()
        assert len(rows) == 2


def test_load_cached_returns_meta_and_current_labels(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    scan_mailbox(_fake_client(), "in:inbox", factory)

    messages, current = load_cached(factory)
    by_id = {m.gmail_id: m for m in messages}
    assert by_id["m1"].sender == "a@booking.com"
    assert current["m1"] == {"INBOX", "Reizen"}
    assert current["m2"] == {"INBOX"}


def test_rescan_updates_changed_fields(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    scan_mailbox(_fake_client(), "in:inbox", factory)

    # second scan where m1's subject changed
    updated = _fake_client()
    updated.get_metadata.side_effect = [
        {
            "gmail_id": "m1",
            "thread_id": "t1",
            "snippet": "hi",
            "sender": "a@booking.com",
            "subject": "Receipt UPDATED",
            "label_ids": ["INBOX", "Label_1"],
            "internal_date_ms": 1751328000000,
        },
        {
            "gmail_id": "m2",
            "thread_id": "t2",
            "snippet": "yo",
            "sender": "b@degiro.nl",
            "subject": "Statement",
            "label_ids": ["INBOX"],
            "internal_date_ms": 1751328000000,
        },
    ]
    scan_mailbox(updated, "in:inbox", factory)

    with factory() as s:
        row = s.scalar(select(Message).where(Message.gmail_id == "m1"))
        assert row.subject == "Receipt UPDATED"
        assert len(s.scalars(select(Message)).all()) == 2


def _meta(gmail_id, subject="s", sender="a@b.com", labels=("INBOX",)):
    return {
        "gmail_id": gmail_id,
        "thread_id": "t",
        "snippet": "x",
        "sender": sender,
        "subject": subject,
        "label_ids": list(labels),
        "internal_date_ms": 1751328000000,
    }


def test_scan_skips_a_message_that_fails_to_fetch(tmp_path):
    # One message raising (e.g. Gmail 400 failedPrecondition) must not abort
    # the whole scan; the good messages are still cached.
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)

    client = MagicMock()
    client.list_message_ids.return_value = ["m1", "bad", "m3"]
    client.list_labels.return_value = {"INBOX": "INBOX"}
    client.get_metadata.side_effect = [_meta("m1"), RuntimeError("boom"), _meta("m3")]

    count = scan_mailbox(client, "in:inbox", factory)

    assert count == 2  # m1 + m3 cached, bad skipped
    with factory() as s:
        assert s.scalar(select(Message).where(Message.gmail_id == "m1")) is not None
        assert s.scalar(select(Message).where(Message.gmail_id == "m3")) is not None
        assert s.scalar(select(Message).where(Message.gmail_id == "bad")) is None


def test_scan_resume_skips_already_cached_ids(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    scan_mailbox(_fake_client(), "in:inbox", factory)  # caches m1, m2

    client = MagicMock()
    client.list_message_ids.return_value = ["m1", "m2", "m3"]
    client.list_labels.return_value = {"INBOX": "INBOX"}
    client.get_metadata.side_effect = [_meta("m3")]  # only m3 may be fetched

    count = scan_mailbox(client, "in:inbox", factory, skip_cached=True)

    assert count == 1  # only m3 newly fetched
    assert client.get_metadata.call_count == 1
    with factory() as s:
        assert len(s.scalars(select(Message)).all()) == 3


def test_load_cached_unknown_label_id_falls_back_to_raw_id(tmp_path):
    import json

    from mailbrain.storage.models import Message

    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        # message references a label id that has no row in the labels cache
        s.add(
            Message(
                gmail_id="mx",
                sender="a@b.com",
                subject="hi",
                label_ids=json.dumps(["Label_UNKNOWN"]),
            )
        )
        s.commit()

    _messages, current = load_cached(factory)
    assert current["mx"] == {"Label_UNKNOWN"}  # raw id used as name fallback
