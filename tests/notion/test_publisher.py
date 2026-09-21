from datetime import UTC, datetime

from sqlalchemy import select

from mailbrain.notion.client import (
    NotionChildPage,
    NotionCreateRejected,
    NotionCreateUncertain,
    PublishedNotionPage,
)
from mailbrain.notion.publisher import DigestPublisher
from mailbrain.storage import db
from mailbrain.storage.models import Digest


class FakeNotionClient:
    def __init__(self, pages=(), created=None, error=None):
        self.pages = tuple(pages)
        self.created = created or PublishedNotionPage("created-page", "https://notion/created")
        self.error = error
        self.created_requests = []
        self.validated_requests = []
        self.list_count = 0

    def validate_child_page(self, parent_page_id, title, markdown):
        self.validated_requests.append((parent_page_id, title, markdown))
        return b"valid"

    def list_child_pages(self, parent_page_id):
        self.list_count += 1
        return self.pages

    def create_child_page(self, parent_page_id, title, markdown):
        self.created_requests.append((parent_page_id, title, markdown))
        if self.error is not None:
            raise self.error
        return self.created


def factory(tmp_path):
    database = tmp_path / "state.db"
    db.init_db(database)
    return db.session_factory(database)


def test_publish_creates_and_persists_page_once(tmp_path):
    sessions = factory(tmp_path)
    client = FakeNotionClient()
    publisher = DigestPublisher(sessions, client, "parent")

    first = publisher.publish("2026-W38", "# Week")
    second = publisher.publish("2026-W38", "# Changed")

    assert first.status == "created"
    assert second.status == "already_published"
    assert len(client.created_requests) == 1
    assert client.list_count == 1
    assert len(client.validated_requests) == 1
    with sessions() as session:
        record = session.scalar(select(Digest).where(Digest.iso_week == "2026-W38"))
        assert record is not None
        assert record.notion_page_id == "created-page"


def test_publish_recovers_existing_remote_page(tmp_path):
    sessions = factory(tmp_path)
    with sessions() as session:
        session.add(Digest(iso_week="2026-W38", created_at=datetime.now(UTC)))
        session.commit()
    client = FakeNotionClient(
        pages=(NotionChildPage("remote-page", "MailBrain TLDR — 2026-W38"),)
    )

    result = DigestPublisher(sessions, client, "parent").publish("2026-W38", "body")

    assert result.status == "recovered"
    assert result.page_id == "remote-page"
    assert client.created_requests == []


def test_unresolved_publication_is_not_recreated(tmp_path):
    sessions = factory(tmp_path)
    with sessions() as session:
        session.add(Digest(iso_week="2026-W38", created_at=datetime.now(UTC)))
        session.commit()
    client = FakeNotionClient()

    result = DigestPublisher(sessions, client, "parent").publish("2026-W38", "body")

    assert result.status == "uncertain"
    assert client.created_requests == []


def test_definite_rejection_records_retryable_failure(tmp_path):
    sessions = factory(tmp_path)
    client = FakeNotionClient(error=NotionCreateRejected("forbidden"))

    result = DigestPublisher(sessions, client, "parent").publish("2026-W38", "body")

    assert result.status == "failed"
    with sessions() as session:
        record = session.scalar(select(Digest))
        assert record is not None
        assert record.status == "failed"
        assert record.last_error == "forbidden"


def test_uncertain_create_preserves_reservation(tmp_path):
    sessions = factory(tmp_path)
    client = FakeNotionClient(error=NotionCreateUncertain("timeout"))

    result = DigestPublisher(sessions, client, "parent").publish("2026-W38", "body")

    assert result.status == "uncertain"
    with sessions() as session:
        record = session.scalar(select(Digest))
        assert record is not None
        assert record.notion_page_id is None
        assert record.status == "uncertain"


def test_duplicate_remote_pages_block_creation(tmp_path):
    sessions = factory(tmp_path)
    title = "MailBrain TLDR — 2026-W38"
    client = FakeNotionClient(
        pages=(NotionChildPage("one", title), NotionChildPage("two", title))
    )

    result = DigestPublisher(sessions, client, "parent").publish("2026-W38", "body")

    assert result.status == "failed"
    assert result.error is not None
    assert "one, two" in result.error
    assert client.created_requests == []


def test_failed_week_can_retry_and_uncertain_week_requires_opt_in(tmp_path):
    sessions = factory(tmp_path)
    with sessions() as session:
        session.add(
            Digest(
                iso_week="2026-W38",
                status="failed",
                last_error="temporary configuration error",
                created_at=datetime.now(UTC),
            )
        )
        session.add(
            Digest(
                iso_week="2026-W39",
                status="uncertain",
                last_error="timeout",
                created_at=datetime.now(UTC),
            )
        )
        session.commit()
    client = FakeNotionClient()
    publisher = DigestPublisher(sessions, client, "parent")

    failed_retry = publisher.publish("2026-W38", "body")
    uncertain_retry = publisher.publish("2026-W39", "body", retry_uncertain=True)

    assert failed_retry.status == "created"
    assert uncertain_retry.status == "created"
    assert len(client.created_requests) == 2
