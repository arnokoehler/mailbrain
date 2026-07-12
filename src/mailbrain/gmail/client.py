"""Thin wrapper over the Gmail REST service (metadata reads only)."""

from __future__ import annotations

from typing import Any

USER_ID = "me"
_METADATA_HEADERS = ["From", "Subject"]


class GmailClient:
    """Read-only Gmail access used by the scanner. I/O boundary — mock in tests."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_message_ids(self, query: str) -> list[str]:
        api = self._service.users().messages()
        ids: list[str] = []
        request = api.list(userId=USER_ID, q=query)
        while request is not None:
            response = request.execute()
            ids.extend(m["id"] for m in response.get("messages", []))
            request = api.list_next(request, response)
        return ids

    def get_metadata(self, message_id: str) -> dict[str, Any]:
        api = self._service.users().messages()
        msg = api.get(
            userId=USER_ID,
            id=message_id,
            format="metadata",
            metadataHeaders=_METADATA_HEADERS,
        ).execute()
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
        response = self._service.users().labels().list(userId=USER_ID).execute()
        return {lbl["id"]: lbl["name"] for lbl in response.get("labels", [])}
