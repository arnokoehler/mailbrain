from unittest.mock import MagicMock

import pytest

from mailbrain.gmail.client import GmailClient


def _service_with_pages(pages):
    """Build a fake Gmail service whose users().messages().list_next paginates."""
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    list_calls = [MagicMock(execute=MagicMock(return_value=p)) for p in pages]
    messages_api.list.side_effect = list_calls
    messages_api.list_next.side_effect = list_calls[1:] + [None]
    return service, messages_api


def test_list_message_ids_paginates():
    pages = [
        {"messages": [{"id": "a"}, {"id": "b"}]},
        {"messages": [{"id": "c"}]},
    ]
    service, _ = _service_with_pages(pages)
    client = GmailClient(service)
    assert client.list_message_ids("in:inbox") == ["a", "b", "c"]


def test_list_message_ids_empty():
    service, _ = _service_with_pages([{}])
    client = GmailClient(service)
    assert client.list_message_ids("in:inbox") == []


def test_get_metadata_extracts_headers():
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    messages_api.get.return_value.execute.return_value = {
        "id": "m1",
        "threadId": "t1",
        "snippet": "hello there",
        "internalDate": "1751328000000",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Booking <no-reply@booking.com>"},
                {"name": "Subject", "value": "Your receipt"},
            ]
        },
    }
    client = GmailClient(service)
    meta = client.get_metadata("m1")
    assert meta["sender"] == "Booking <no-reply@booking.com>"
    assert meta["subject"] == "Your receipt"
    assert meta["label_ids"] == ["INBOX", "UNREAD"]
    assert meta["thread_id"] == "t1"
    assert meta["snippet"] == "hello there"
    assert meta["internal_date_ms"] == 1751328000000


def test_get_metadata_missing_headers_default_empty():
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    messages_api.get.return_value.execute.return_value = {
        "id": "m2",
        "internalDate": "0",
        "labelIds": [],
        "payload": {"headers": []},
    }
    client = GmailClient(service)
    meta = client.get_metadata("m2")
    assert meta["sender"] == ""
    assert meta["subject"] == ""


def test_list_labels_maps_id_to_name():
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": [
            {"id": "Label_1", "name": "Reizen"},
            {"id": "INBOX", "name": "INBOX"},
        ]
    }
    client = GmailClient(service)
    assert client.list_labels() == {"Label_1": "Reizen", "INBOX": "INBOX"}


def test_get_metadata_missing_payload_entirely():
    # Gmail may return a message with no payload key at all (header-only / edge).
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    messages_api.get.return_value.execute.return_value = {
        "id": "m3",
        "internalDate": "0",
        "labelIds": [],
    }
    client = GmailClient(service)
    meta = client.get_metadata("m3")
    assert meta["sender"] == ""
    assert meta["subject"] == ""
    assert meta["label_ids"] == []


def test_list_labels_empty_response():
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {}
    client = GmailClient(service)
    assert client.list_labels() == {}


class HttpFailure(Exception):
    def __init__(self, status, reason=None):
        self.resp = type("Resp", (), {"status": status})()
        self.content = (
            f'{{"error":{{"errors":[{{"reason":"{reason}"}}]}}}}'.encode()
            if reason
            else b"{}"
        )


@pytest.mark.parametrize("method", ["list_message_ids", "get_metadata", "list_labels"])
def test_reads_retry_transport_failures(method):
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    labels_api = service.users.return_value.labels.return_value
    messages_api.list_next.return_value = None
    messages_api.list.return_value.execute.side_effect = [TimeoutError(), {}]
    messages_api.get.return_value.execute.side_effect = [TimeoutError(), {"id": "m1"}]
    labels_api.list.return_value.execute.side_effect = [TimeoutError(), {}]
    client = GmailClient(service, sleep=lambda _: None)

    if method == "list_message_ids":
        client.list_message_ids("in:inbox")
        execute = messages_api.list.return_value.execute
    elif method == "get_metadata":
        client.get_metadata("m1")
        execute = messages_api.get.return_value.execute
    else:
        client.list_labels()
        execute = labels_api.list.return_value.execute

    assert execute.call_count == 2


def test_read_retries_are_bounded():
    service = MagicMock()
    execute = service.users.return_value.labels.return_value.list.return_value.execute
    execute.side_effect = HttpFailure(503)
    client = GmailClient(service, sleep=lambda _: None)

    with pytest.raises(HttpFailure):
        client.list_labels()

    assert execute.call_count == 5


def test_rate_limit_403_is_retried():
    service = MagicMock()
    execute = service.users.return_value.labels.return_value.list.return_value.execute
    execute.side_effect = [HttpFailure(403, "rateLimitExceeded"), {}]
    client = GmailClient(service, sleep=lambda _: None)

    assert client.list_labels() == {}
    assert execute.call_count == 2


def test_permanent_auth_403_is_not_retried():
    service = MagicMock()
    execute = service.users.return_value.labels.return_value.list.return_value.execute
    execute.side_effect = HttpFailure(403, "forbidden")
    client = GmailClient(service, sleep=lambda _: None)

    with pytest.raises(HttpFailure):
        client.list_labels()

    assert execute.call_count == 1
