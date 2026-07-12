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
