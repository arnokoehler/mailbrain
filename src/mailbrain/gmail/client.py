"""Thin wrapper over the Gmail REST service (metadata reads + label writes)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from mailbrain.gmail.backoff import is_retryable_read, with_backoff

USER_ID = "me"
_METADATA_HEADERS = ["From", "Subject"]


class GmailClient:
    """Gmail access used by the scanner and applier. I/O boundary — mock in tests."""

    def __init__(
        self,
        service: Any,
        sleep: Callable[[float], None] = time.sleep,
        page_size: int = 500,
    ) -> None:
        self._service = service
        self._sleep = sleep
        self._page_size = page_size

    def list_message_ids(self, query: str) -> list[str]:
        api = self._service.users().messages()
        ids: list[str] = []
        request = api.list(userId=USER_ID, q=query, maxResults=self._page_size)
        while request is not None:
            response = with_backoff(
                request.execute,
                sleep=self._sleep,
                is_retryable=is_retryable_read,
            )
            ids.extend(m["id"] for m in response.get("messages", []))
            request = api.list_next(request, response)
        return ids

    def get_metadata(self, message_id: str) -> dict[str, Any]:
        """Fetch one message's metadata.

        Returns a dict with keys:
          gmail_id: str, thread_id: str | None, snippet: str | None,
          sender: str, subject: str, label_ids: list[str],
          internal_date_ms: int (epoch milliseconds).
        """
        api = self._service.users().messages()
        request = api.get(
            userId=USER_ID,
            id=message_id,
            format="metadata",
            metadataHeaders=_METADATA_HEADERS,
        )
        msg = with_backoff(
            request.execute,
            sleep=self._sleep,
            is_retryable=is_retryable_read,
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        return {
            "gmail_id": msg["id"],
            "thread_id": msg.get("threadId"),
            "snippet": msg.get("snippet"),
            "sender": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "label_ids": msg.get("labelIds", []),
            "internal_date_ms": int(msg.get("internalDate", "0")),
        }

    def list_labels(self) -> dict[str, str]:
        request = self._service.users().labels().list(userId=USER_ID)
        response = with_backoff(
            request.execute,
            sleep=self._sleep,
            is_retryable=is_retryable_read,
        )
        return {lbl["id"]: lbl["name"] for lbl in response.get("labels", [])}

    def create_label(self, name: str) -> str:
        """Create a label (nested paths use '/' in the name); return its Gmail id."""
        body = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        created = self._service.users().labels().create(userId=USER_ID, body=body).execute()
        return created["id"]  # type: ignore[no-any-return]

    def batch_modify(
        self,
        message_ids: list[str],
        add_label_ids: list[str],
        remove_label_ids: list[str],
    ) -> None:
        """Add/remove label ids on up to 1000 messages in one call. Never deletes."""
        body = {
            "ids": message_ids,
            "addLabelIds": add_label_ids,
            "removeLabelIds": remove_label_ids,
        }
        self._service.users().messages().batchModify(userId=USER_ID, body=body).execute()
