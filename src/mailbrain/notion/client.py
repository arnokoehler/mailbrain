from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_API_VERSION = "2026-03-11"
MAX_ATTEMPTS = 5
MAX_PAYLOAD_BYTES = 490_000
MAX_ESTIMATED_BLOCKS = 950


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class UrllibHttpTransport:
    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    response.status, dict(response.headers.items()), response.read()
                )
        except HTTPError as error:
            return HttpResponse(error.code, dict(error.headers.items()), error.read())


@dataclass(frozen=True)
class NotionChildPage:
    page_id: str
    title: str


@dataclass(frozen=True)
class PublishedNotionPage:
    page_id: str
    url: str | None


class NotionError(RuntimeError):
    pass


class NotionCreateRejected(NotionError):
    pass


class NotionCreateUncertain(NotionError):
    pass


class NotionPayloadTooLarge(NotionError):
    pass


class NotionClient:
    def __init__(
        self,
        token: str,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.25),
    ) -> None:
        self._transport = transport or UrllibHttpTransport()
        self._sleep = sleep
        self._jitter = jitter
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "mailbrain/0.1.0",
        }

    def list_child_pages(self, parent_page_id: str) -> tuple[NotionChildPage, ...]:
        pages: list[NotionChildPage] = []
        cursor: str | None = None
        while True:
            parameters = {"page_size": "100"}
            if cursor is not None:
                parameters["start_cursor"] = cursor
            url = (
                f"{NOTION_API_URL}/blocks/{parent_page_id}/children?"
                f"{urlencode(parameters)}"
            )
            response = self._request("GET", url, None)
            if response.status != 200:
                raise NotionError(self._error_message(response))
            payload = self._json_object(response, NotionError)
            results = payload.get("results")
            if not isinstance(results, list):
                raise NotionError("Notion returned an invalid child-page response")
            for item in results:
                if not isinstance(item, dict) or item.get("type") != "child_page":
                    continue
                child_page = item.get("child_page")
                page_id = item.get("id")
                if isinstance(child_page, dict) and isinstance(page_id, str):
                    title = child_page.get("title")
                    if isinstance(title, str):
                        pages.append(NotionChildPage(page_id=page_id, title=title))
            if payload.get("has_more") is not True:
                return tuple(pages)
            next_cursor = payload.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise NotionError("Notion pagination omitted the next cursor")
            cursor = next_cursor

    def create_child_page(
        self, parent_page_id: str, title: str, markdown: str
    ) -> PublishedNotionPage:
        body = self.validate_child_page(parent_page_id, title, markdown)
        try:
            response = self._request("POST", f"{NOTION_API_URL}/pages", body)
        except (OSError, TimeoutError) as error:
            raise NotionCreateUncertain(
                "Notion page creation failed with an uncertain transport result"
            ) from error
        if response.status != 200:
            message = self._error_message(response)
            if response.status in {400, 401, 403, 404, 429, 529}:
                raise NotionCreateRejected(message)
            raise NotionCreateUncertain(message)
        payload_response = self._json_object(response, NotionCreateUncertain)
        page_id = payload_response.get("id")
        if payload_response.get("object") != "page" or not isinstance(page_id, str) or not page_id:
            raise NotionCreateUncertain("Notion returned an invalid create-page response")
        url = payload_response.get("url")
        return PublishedNotionPage(page_id=page_id, url=url if isinstance(url, str) else None)

    def validate_child_page(self, parent_page_id: str, title: str, markdown: str) -> bytes:
        payload = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {
                "title": {
                    "type": "title",
                    "title": [{"type": "text", "text": {"content": title}}],
                }
            },
            "markdown": markdown,
        }
        body = json.dumps(payload, ensure_ascii=False).encode()
        estimated_blocks = sum(1 for line in markdown.splitlines() if line.strip())
        if len(body) > MAX_PAYLOAD_BYTES:
            raise NotionPayloadTooLarge("Notion digest exceeds the 490 KB request limit")
        if estimated_blocks > MAX_ESTIMATED_BLOCKS:
            raise NotionPayloadTooLarge("Notion digest exceeds the 950 block safety limit")
        return body

    def _request(self, method: str, url: str, body: bytes | None) -> HttpResponse:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._transport.send(method, url, self._headers, body, 60)
            except (OSError, TimeoutError):
                if method != "GET" or attempt == MAX_ATTEMPTS - 1:
                    raise
                self._wait(None, attempt)
                continue
            retryable = response.status in {429, 529} or (
                method == "GET" and response.status in {500, 502, 503, 504}
            )
            if not retryable or attempt == MAX_ATTEMPTS - 1:
                return response
            self._wait(response.headers.get("Retry-After"), attempt)
        raise AssertionError("unreachable")

    def _wait(self, retry_after: str | None, attempt: int) -> None:
        try:
            delay = float(retry_after) if retry_after is not None else min(2**attempt, 30)
        except ValueError:
            delay = min(2**attempt, 30)
        self._sleep(max(delay, 0) + self._jitter())

    @staticmethod
    def _json_object(
        response: HttpResponse, error_type: type[NotionError]
    ) -> dict[str, Any]:
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise error_type("Notion returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise error_type("Notion returned an invalid JSON object")
        return payload

    @staticmethod
    def _error_message(response: HttpResponse) -> str:
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return f"Notion request failed with HTTP {response.status}"
        if not isinstance(payload, dict):
            return f"Notion request failed with HTTP {response.status}"
        code = payload.get("code")
        message = payload.get("message")
        details = ": ".join(str(item) for item in (code, message) if item)
        suffix = f": {details}" if details else ""
        return f"Notion request failed with HTTP {response.status}{suffix}"
