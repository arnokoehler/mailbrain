import json

import pytest

from mailbrain.notion.client import (
    HttpResponse,
    NotionClient,
    NotionCreateUncertain,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def send(self, method, url, headers, body, timeout_seconds):
        self.requests.append((method, url, headers, body, timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def response(status, payload, headers=None):
    return HttpResponse(status, headers or {}, json.dumps(payload).encode())


def test_create_page_uses_native_markdown_and_required_headers():
    transport = FakeTransport(
        [response(200, {"object": "page", "id": "page-1", "url": "https://notion/page-1"})]
    )
    client = NotionClient("secret-token", transport, sleep=lambda _: None, jitter=lambda: 0)

    page = client.create_child_page("parent-1", "MailBrain TLDR — 2026-W38", "# Digest\n")

    assert page.page_id == "page-1"
    method, url, headers, body, timeout = transport.requests[0]
    assert method == "POST"
    assert url == "https://api.notion.com/v1/pages"
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Notion-Version"] == "2026-03-11"
    assert timeout == 60
    payload = json.loads(body)
    assert payload["parent"] == {"type": "page_id", "page_id": "parent-1"}
    assert payload["markdown"] == "# Digest\n"
    assert "allow_async" not in payload


def test_list_child_pages_follows_pagination_and_ignores_other_blocks():
    transport = FakeTransport(
        [
            response(
                200,
                {
                    "results": [
                        {"id": "paragraph", "type": "paragraph"},
                        {
                            "id": "page-1",
                            "type": "child_page",
                            "child_page": {"title": "Week one"},
                        },
                    ],
                    "has_more": True,
                    "next_cursor": "cursor value",
                },
            ),
            response(
                200,
                {
                    "results": [
                        {
                            "id": "page-2",
                            "type": "child_page",
                            "child_page": {"title": "Week two"},
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
            ),
        ]
    )
    client = NotionClient("token", transport, sleep=lambda _: None, jitter=lambda: 0)

    pages = client.list_child_pages("parent")

    assert [(page.page_id, page.title) for page in pages] == [
        ("page-1", "Week one"),
        ("page-2", "Week two"),
    ]
    assert "start_cursor=cursor+value" in transport.requests[1][1]


def test_post_retries_rate_limit_but_not_server_error():
    sleeps = []
    transport = FakeTransport(
        [
            response(429, {"code": "rate_limited"}, {"Retry-After": "2"}),
            response(200, {"object": "page", "id": "page-1"}),
        ]
    )
    client = NotionClient("token", transport, sleep=sleeps.append, jitter=lambda: 0.1)

    client.create_child_page("parent", "title", "body")

    assert sleeps == [2.1]
    assert len(transport.requests) == 2

    failing_transport = FakeTransport([response(503, {"code": "service_unavailable"})])
    failing_client = NotionClient(
        "token", failing_transport, sleep=lambda _: None, jitter=lambda: 0
    )
    with pytest.raises(NotionCreateUncertain):
        failing_client.create_child_page("parent", "title", "body")
    assert len(failing_transport.requests) == 1


def test_errors_do_not_include_token():
    transport = FakeTransport([response(503, {"message": "temporarily unavailable"})])
    client = NotionClient(
        "secret-token", transport, sleep=lambda _: None, jitter=lambda: 0
    )

    with pytest.raises(NotionCreateUncertain) as error:
        client.create_child_page("parent", "title", "body")

    assert "secret-token" not in str(error.value)
