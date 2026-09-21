from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.notion.client import (
    NotionChildPage,
    NotionCreateRejected,
    NotionCreateUncertain,
    NotionError,
    PublishedNotionPage,
)
from mailbrain.storage.models import Digest


class SupportsNotionPublishing(Protocol):
    def validate_child_page(self, parent_page_id: str, title: str, markdown: str) -> bytes: ...

    def list_child_pages(self, parent_page_id: str) -> tuple[NotionChildPage, ...]: ...

    def create_child_page(
        self, parent_page_id: str, title: str, markdown: str
    ) -> PublishedNotionPage: ...


@dataclass(frozen=True)
class PublicationResult:
    status: PublicationStatus
    page_id: str | None = None
    url: str | None = None
    error: str | None = None


PublicationStatus = Literal[
    "created", "recovered", "already_published", "failed", "uncertain"
]


class DigestPublisher:
    def __init__(
        self,
        factory: sessionmaker[Session],
        client: SupportsNotionPublishing,
        parent_page_id: str,
    ) -> None:
        self._factory = factory
        self._client = client
        self._parent_page_id = parent_page_id

    def publish(
        self, iso_week: str, markdown: str, *, retry_uncertain: bool = False
    ) -> PublicationResult:
        title = f"MailBrain TLDR — {iso_week}"
        with self._factory() as session:
            record = session.scalar(select(Digest).where(Digest.iso_week == iso_week))
            if record is not None and record.notion_page_id is not None:
                return PublicationResult("already_published", record.notion_page_id)
            previous_status = record.status if record is not None else None

        try:
            matches = tuple(
                page
                for page in self._client.list_child_pages(self._parent_page_id)
                if page.title == title
            )
        except (NotionError, OSError, TimeoutError) as exception:
            failure_status: Literal["failed", "uncertain"] = (
                "uncertain" if previous_status in {"pending", "uncertain"} else "failed"
            )
            self._record_failure(iso_week, failure_status, str(exception))
            return PublicationResult(failure_status, error=str(exception))
        if len(matches) > 1:
            ids = ", ".join(page.page_id for page in matches)
            failure_message = f"Multiple Notion pages match {title}: {ids}"
            self._record_failure(iso_week, "failed", failure_message)
            return PublicationResult("failed", error=failure_message)
        if matches:
            self._store_page_id(iso_week, matches[0].page_id)
            return PublicationResult("recovered", matches[0].page_id)
        if previous_status in {"pending", "uncertain"} and not retry_uncertain:
            failure_message = (
                f"Notion publication state for {iso_week} is uncertain; no matching page "
                "was found and no create retry was attempted"
            )
            self._record_failure(iso_week, "uncertain", failure_message)
            return PublicationResult("uncertain", error=failure_message)

        try:
            self._client.validate_child_page(self._parent_page_id, title, markdown)
        except NotionError as error:
            self._record_failure(iso_week, "failed", str(error))
            return PublicationResult("failed", error=str(error))
        with self._factory() as session:
            record = session.scalar(select(Digest).where(Digest.iso_week == iso_week))
            if record is None:
                record = Digest(iso_week=iso_week, created_at=datetime.now(UTC))
                session.add(record)
            record.status = "pending"
            record.last_error = None
            record.last_attempt_at = datetime.now(UTC)
            session.commit()
        try:
            page = self._client.create_child_page(self._parent_page_id, title, markdown)
        except NotionCreateRejected as error:
            self._record_failure(iso_week, "failed", str(error))
            return PublicationResult("failed", error=str(error))
        except NotionCreateUncertain as error:
            self._record_failure(iso_week, "uncertain", str(error))
            return PublicationResult("uncertain", error=str(error))
        self._store_page_id(iso_week, page.page_id)
        return PublicationResult("created", page.page_id, page.url)

    def _store_page_id(self, iso_week: str, page_id: str) -> None:
        with self._factory() as session:
            record = session.scalar(select(Digest).where(Digest.iso_week == iso_week))
            if record is None:
                record = Digest(iso_week=iso_week, created_at=datetime.now(UTC))
                session.add(record)
            record.notion_page_id = page_id
            record.status = "published"
            record.last_error = None
            record.last_attempt_at = datetime.now(UTC)
            record.published_at = datetime.now(UTC)
            session.commit()

    def _record_failure(
        self, iso_week: str, status: Literal["failed", "uncertain"], error: str
    ) -> None:
        with self._factory() as session:
            record = session.scalar(select(Digest).where(Digest.iso_week == iso_week))
            if record is None:
                record = Digest(iso_week=iso_week, created_at=datetime.now(UTC))
                session.add(record)
            record.status = status
            record.last_error = error
            record.last_attempt_at = datetime.now(UTC)
            session.commit()
