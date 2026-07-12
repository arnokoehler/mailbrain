from unittest.mock import MagicMock

from mailbrain.gmail.client import GmailClient


def test_create_label_returns_id():
    service = MagicMock()
    labels_api = service.users.return_value.labels.return_value
    labels_api.create.return_value.execute.return_value = {"id": "Label_9", "name": "Reizen"}
    client = GmailClient(service)

    result = client.create_label("Reizen")

    assert result == "Label_9"
    _, kwargs = labels_api.create.call_args
    assert kwargs["userId"] == "me"
    assert kwargs["body"]["name"] == "Reizen"


def test_batch_modify_sends_body():
    service = MagicMock()
    messages_api = service.users.return_value.messages.return_value
    client = GmailClient(service)

    client.batch_modify(["m1", "m2"], add_label_ids=["Label_9"], remove_label_ids=["INBOX"])

    _, kwargs = messages_api.batchModify.call_args
    assert kwargs["userId"] == "me"
    assert kwargs["body"] == {
        "ids": ["m1", "m2"],
        "addLabelIds": ["Label_9"],
        "removeLabelIds": ["INBOX"],
    }
    messages_api.batchModify.return_value.execute.assert_called_once()


class _Http429(Exception):
    """Minimal google HttpError stand-in: default_is_retryable reads exc.resp.status."""

    def __init__(self) -> None:
        self.resp = type("Resp", (), {"status": 429})()


def test_batch_modify_retries_on_transient_error():
    service = MagicMock()
    execute = service.users.return_value.messages.return_value.batchModify.return_value.execute
    execute.side_effect = [_Http429(), None]  # fail once (429), then succeed
    client = GmailClient(service, sleep=lambda _: None)

    client.batch_modify(["m1"], add_label_ids=["L1"], remove_label_ids=[])

    assert execute.call_count == 2


def test_create_label_retries_on_transient_error():
    service = MagicMock()
    execute = service.users.return_value.labels.return_value.create.return_value.execute
    execute.side_effect = [_Http429(), {"id": "Label_9"}]
    client = GmailClient(service, sleep=lambda _: None)

    result = client.create_label("Reizen")

    assert result == "Label_9"
    assert execute.call_count == 2


def test_batch_modify_gives_up_on_non_retryable():
    service = MagicMock()
    execute = service.users.return_value.messages.return_value.batchModify.return_value.execute
    execute.side_effect = ValueError("fatal")  # no .resp.status -> not retryable
    client = GmailClient(service, sleep=lambda _: None)

    import pytest

    with pytest.raises(ValueError):
        client.batch_modify(["m1"], add_label_ids=[], remove_label_ids=[])
    assert execute.call_count == 1
