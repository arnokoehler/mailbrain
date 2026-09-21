from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from mailbrain.scan import (
    ScanIncompleteError,
    ScanNotEligibleError,
    latest_scan_run,
    load_cached,
    require_eligible_scan,
    scan_mailbox,
)
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message, ScanRun

NOW = datetime(2026, 9, 11, 10, tzinfo=UTC)


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


def _fake_client(ids=("m1", "m2")):
    client = MagicMock()
    client.list_message_ids.return_value = list(ids)
    client.list_labels.return_value = {"INBOX": "INBOX", "Label_1": "Reizen"}
    metadata = {
        "m1": _meta("m1", "Receipt", "a@booking.com", ("INBOX", "Label_1")),
        "m2": _meta("m2", "Statement", "b@degiro.nl"),
        "m3": _meta("m3"),
    }
    client.get_metadata.side_effect = lambda message_id: metadata[message_id]
    return client


def _factory(tmp_path):
    db_path = tmp_path / "state.db"
    db.init_db(db_path)
    return db.session_factory(db_path)


def _scan_id(factory):
    scan = latest_scan_run(factory)
    assert scan is not None
    return scan.id


def test_scan_persists_successful_lifecycle_and_deduplicates_membership(tmp_path):
    factory = _factory(tmp_path)
    client = _fake_client(("m1", "m1", "m2"))

    count = scan_mailbox(client, "in:inbox", factory, now=lambda: NOW)

    assert count == 2
    assert [call.args[0] for call in client.get_metadata.call_args_list] == ["m1", "m2"]
    with factory() as session:
        scan = session.scalar(select(ScanRun))
        assert scan is not None
        assert scan.status == "succeeded"
        assert scan.started_at == NOW.replace(tzinfo=None)
        assert scan.finished_at == NOW.replace(tzinfo=None)
        assert scan.listed_count == 2
        assert scan.fetched_count == 2
        assert scan.failed_count == 0
        assert {row.last_scan_id for row in session.scalars(select(Message))} == {scan.id}
        assert session.scalar(select(Label).where(Label.name == "Reizen")) is not None


def test_scan_is_idempotent_upsert(tmp_path):
    factory = _factory(tmp_path)
    scan_mailbox(_fake_client(), "in:inbox", factory)
    scan_mailbox(_fake_client(), "in:inbox", factory)

    with factory() as session:
        assert len(session.scalars(select(Message)).all()) == 2
        assert len(session.scalars(select(ScanRun)).all()) == 2


def test_load_cached_is_scoped_to_requested_scan(tmp_path):
    factory = _factory(tmp_path)
    scan_mailbox(_fake_client(("m1", "m2")), "in:inbox", factory)
    first_scan_id = _scan_id(factory)
    scan_mailbox(_fake_client(("m2",)), "in:inbox", factory)
    second_scan_id = _scan_id(factory)

    first_messages, first_current = load_cached(factory, first_scan_id)
    second_messages, second_current = load_cached(factory, second_scan_id)

    assert {message.gmail_id for message in first_messages} == {"m1"}
    assert set(first_current) == {"m1"}
    assert {message.gmail_id for message in second_messages} == {"m2"}
    assert set(second_current) == {"m2"}


def test_rescan_updates_changed_fields_and_membership(tmp_path):
    factory = _factory(tmp_path)
    scan_mailbox(_fake_client(), "in:inbox", factory)
    updated = _fake_client()
    updated.get_metadata.side_effect = lambda message_id: (
        _meta("m1", "Receipt UPDATED", "a@booking.com", ("INBOX", "Label_1"))
        if message_id == "m1"
        else _meta("m2", "Statement", "b@degiro.nl")
    )

    scan_mailbox(updated, "in:inbox", factory)

    with factory() as session:
        row = session.scalar(select(Message).where(Message.gmail_id == "m1"))
        assert row is not None
        assert row.subject == "Receipt UPDATED"
        assert row.last_scan_id == _scan_id(factory)


def test_partial_fetch_failure_marks_scan_failed_and_raises(tmp_path):
    factory = _factory(tmp_path)
    client = _fake_client(("m1", "bad", "m2"))
    client.get_metadata.side_effect = [_meta("m1"), RuntimeError("boom"), _meta("m2")]

    with pytest.raises(ScanIncompleteError, match="1 message"):
        scan_mailbox(client, "in:inbox", factory, batch_size=1, now=lambda: NOW)

    with factory() as session:
        scan = session.scalar(select(ScanRun))
        assert scan is not None
        assert scan.status == "failed"
        assert scan.listed_count == 3
        assert scan.fetched_count == 2
        assert scan.failed_count == 1
        assert scan.finished_at == NOW.replace(tzinfo=None)
        assert session.scalar(select(Message).where(Message.gmail_id == "bad")) is None
        assert {row.last_scan_id for row in session.scalars(select(Message))} == {scan.id}


def test_existing_message_not_refetched_keeps_previous_membership(tmp_path):
    factory = _factory(tmp_path)
    scan_mailbox(_fake_client(("m1",)), "in:inbox", factory)
    first_scan_id = _scan_id(factory)
    failed = _fake_client(("m1", "m2"))
    failed.get_metadata.side_effect = [RuntimeError("gone"), _meta("m2")]

    with pytest.raises(ScanIncompleteError):
        scan_mailbox(failed, "in:inbox", factory)

    failed_scan_id = _scan_id(factory)
    with factory() as session:
        m1 = session.scalar(select(Message).where(Message.gmail_id == "m1"))
        m2 = session.scalar(select(Message).where(Message.gmail_id == "m2"))
        assert m1 is not None and m1.last_scan_id == first_scan_id
        assert m2 is not None and m2.last_scan_id == failed_scan_id


def test_listing_failure_marks_scan_failed_and_raises_original_error(tmp_path):
    factory = _factory(tmp_path)
    client = _fake_client()
    client.list_message_ids.side_effect = RuntimeError("listing failed")

    with pytest.raises(RuntimeError, match="listing failed"):
        scan_mailbox(client, "in:inbox", factory, now=lambda: NOW)

    scan = latest_scan_run(factory)
    assert scan is not None
    assert scan.status == "failed"
    assert scan.listed_count == 0
    assert scan.fetched_count == 0
    assert scan.failed_count == 1
    assert scan.error_message == "listing failed"


def test_empty_scan_succeeds(tmp_path):
    factory = _factory(tmp_path)

    assert scan_mailbox(_fake_client(()), "in:inbox", factory, now=lambda: NOW) == 0

    scan = latest_scan_run(factory)
    assert scan is not None
    assert scan.status == "succeeded"
    assert scan.listed_count == 0
    assert scan.fetched_count == 0
    assert scan.failed_count == 0


def test_resume_performs_full_refresh_with_fresh_labels(tmp_path):
    factory = _factory(tmp_path)
    scan_mailbox(_fake_client(("m1",)), "in:inbox", factory)
    refreshed = _fake_client(("m1",))
    refreshed.get_metadata.side_effect = [_meta("m1", labels=("INBOX",))]

    assert scan_mailbox(refreshed, "in:inbox", factory, skip_cached=True) == 1

    refreshed.get_metadata.assert_called_once_with("m1")
    _, current = load_cached(factory, _scan_id(factory))
    assert current["m1"] == {"INBOX"}


@pytest.mark.parametrize("status", ["running", "failed", "invalidated"])
def test_non_succeeded_latest_scan_is_ineligible_without_fallback(tmp_path, status):
    factory = _factory(tmp_path)
    scan_mailbox(_fake_client(("m1",)), "in:inbox", factory, now=lambda: NOW)
    succeeded_scan_id = _scan_id(factory)
    with factory() as session:
        session.add(ScanRun(query="in:inbox", started_at=NOW, status=status))
        session.commit()

    with pytest.raises(ScanNotEligibleError, match="latest scan"):
        require_eligible_scan(factory, succeeded_scan_id, 60, now=lambda: NOW)


def test_scan_freshness_uses_start_time(tmp_path):
    factory = _factory(tmp_path)
    scan_mailbox(_fake_client(()), "in:inbox", factory, now=lambda: NOW)
    scan_id = _scan_id(factory)
    with factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan is not None
        scan.finished_at = NOW + timedelta(hours=2)
        session.commit()

    with pytest.raises(ScanNotEligibleError, match="too old"):
        require_eligible_scan(factory, scan_id, 60, now=lambda: NOW + timedelta(minutes=61))


def test_latest_succeeded_fresh_scan_is_eligible_at_age_boundary(tmp_path):
    factory = _factory(tmp_path)
    scan_mailbox(_fake_client(()), "in:inbox", factory, now=lambda: NOW)
    scan_id = _scan_id(factory)

    scan = require_eligible_scan(
        factory, scan_id, 60, now=lambda: NOW + timedelta(minutes=60)
    )

    assert scan.id == scan_id


def test_scan_with_unknown_label_id_is_incomplete(tmp_path):
    factory = _factory(tmp_path)
    client = _fake_client(("mx",))
    client.get_metadata.side_effect = [_meta("mx", labels=("Label_UNKNOWN",))]
    with pytest.raises(ScanIncompleteError, match="unknown label ids"):
        scan_mailbox(client, "in:inbox", factory)
    with factory() as session:
        assert session.scalar(select(ScanRun).order_by(ScanRun.id.desc())).status == "failed"


def test_scan_refreshes_labels_after_metadata_fetch(tmp_path):
    factory = _factory(tmp_path)
    client = _fake_client(("mx",))
    client.get_metadata.side_effect = [_meta("mx", labels=("Label_NEW",))]
    client.list_labels.side_effect = [
        {"INBOX": "INBOX"},
        {"INBOX": "INBOX", "Label_NEW": "Protected/Security"},
    ]

    scan_mailbox(client, "in:inbox", factory)
    _, current = load_cached(factory, _scan_id(factory))

    assert current["mx"] == {"Protected/Security"}
