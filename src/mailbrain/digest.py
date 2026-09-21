from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parseaddr
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mailbrain.config import AISettings, Rule
from mailbrain.rules.engine import classify
from mailbrain.rules.models import MessageMeta
from mailbrain.storage.models import Label, Message


@dataclass(frozen=True)
class DigestMessage:
    gmail_id: str
    sender: str
    subject: str
    snippet: str
    internal_date: datetime
    labels: tuple[str, ...]


@dataclass(frozen=True)
class DigestSection:
    title: str
    messages: tuple[DigestMessage, ...]


@dataclass(frozen=True)
class DigestSummary:
    executive_summary: tuple[str, ...] = ()
    action_items: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeeklyDigest:
    iso_week: str
    start_date: date
    end_date: date
    sections: tuple[DigestSection, ...]
    summary: DigestSummary = DigestSummary()

    @property
    def message_count(self) -> int:
        return sum(len(section.messages) for section in self.sections)


Summarizer = Callable[[tuple[DigestMessage, ...]], DigestSummary]


def parse_iso_week(value: str) -> tuple[str, datetime, datetime]:
    try:
        year_text, week_text = value.upper().split("-W", maxsplit=1)
        monday = date.fromisocalendar(int(year_text), int(week_text), 1)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid ISO week {value!r}; expected YYYY-Www") from error
    start = datetime.combine(monday, datetime.min.time(), tzinfo=UTC)
    normalized = monday.isocalendar()
    return f"{normalized.year}-W{normalized.week:02d}", start, start + timedelta(days=7)


def current_iso_week(now: datetime | None = None) -> str:
    current = (now or datetime.now(UTC)).isocalendar()
    return f"{current.year}-W{current.week:02d}"


def load_week_messages(
    factory: sessionmaker[Session], start: datetime, end: datetime
) -> list[DigestMessage]:
    with factory() as session:
        label_names = {label.gmail_id: label.name for label in session.scalars(select(Label))}
        rows = session.scalars(
            select(Message)
            .where(Message.internal_date >= start, Message.internal_date < end)
            .order_by(Message.internal_date.asc(), Message.gmail_id.asc())
        ).all()
        messages = []
        for row in rows:
            raw_labels = json.loads(row.label_ids or "[]")
            internal_date = row.internal_date
            if internal_date is None:
                continue
            if internal_date.tzinfo is None:
                internal_date = internal_date.replace(tzinfo=UTC)
            messages.append(
                DigestMessage(
                    gmail_id=row.gmail_id,
                    sender=row.sender or "",
                    subject=row.subject or "(zonder onderwerp)",
                    snippet=row.snippet or "",
                    internal_date=internal_date,
                    labels=tuple(sorted(label_names.get(item, item) for item in raw_labels)),
                )
            )
        return messages


def build_digest(
    iso_week: str,
    messages: list[DigestMessage],
    rules: list[Rule],
    summarizer: Summarizer | None = None,
) -> WeeklyDigest:
    normalized_week, start, end = parse_iso_week(iso_week)
    metadata = [
        MessageMeta(
            gmail_id=message.gmail_id,
            sender=message.sender,
            subject=message.subject,
            internal_date=message.internal_date,
            current_labels=message.labels,
        )
        for message in messages
    ]
    classifications = {
        item.gmail_id: item for item in classify(metadata, rules, datetime.now(UTC))
    }
    selected: list[DigestMessage] = []
    sections: dict[str, list[DigestMessage]] = defaultdict(list)
    for message in messages:
        result = classifications.get(message.gmail_id)
        if result is None or not any(
            rule_id.startswith("nieuwsbrief-") for rule_id in result.matched_rule_ids
        ):
            continue
        selected.append(message)
        labels = [
            label
            for label in (*result.add_labels, *message.labels)
            if label not in {"INBOX", "UNREAD", "to-be-removed"}
            and not label.startswith("CATEGORY_")
        ]
        section = _section_title(labels[0] if labels else "Nieuwsbrieven")
        sections[section].append(message)
    digest_sections = tuple(
        DigestSection(title=title, messages=tuple(section_messages))
        for title, section_messages in sorted(sections.items())
    )
    summary = (
        summarizer(tuple(selected)) if summarizer is not None and selected else DigestSummary()
    )
    return WeeklyDigest(
        iso_week=normalized_week,
        start_date=start.date(),
        end_date=(end - timedelta(days=1)).date(),
        sections=digest_sections,
        summary=summary,
    )


def render_markdown(digest: WeeklyDigest) -> str:
    lines = [
        f"# Week {digest.iso_week.split('-W')[1]} — {digest.iso_week[:4]}",
        "",
        f"{digest.start_date.isoformat()} t/m {digest.end_date.isoformat()} · "
        f"{digest.message_count} nieuwsbrieven",
    ]
    if digest.summary.executive_summary:
        lines.extend(["", "## Kernpunten", ""])
        lines.extend(f"- {item}" for item in digest.summary.executive_summary)
    if digest.summary.action_items:
        lines.extend(["", "## Acties", ""])
        lines.extend(f"- [ ] {item}" for item in digest.summary.action_items)
    for section in digest.sections:
        lines.extend(["", f"## {section.title}", ""])
        for message in section.messages:
            sender = parseaddr(message.sender)[0] or parseaddr(message.sender)[1] or message.sender
            link = f"https://mail.google.com/mail/u/0/#all/{message.gmail_id}"
            lines.append(f"- [{message.subject}]({link}) — {sender}")
    if not digest.sections:
        lines.extend(["", "Geen nieuwsbrieven gevonden voor deze week."])
    return "\n".join(lines) + "\n"


def mistral_summarizer(settings: AISettings) -> Summarizer:
    if settings.provider != "mistral":
        raise ValueError(f"unsupported AI provider: {settings.provider}")
    api_key = os.environ.get(settings.api_key_env)
    if not api_key:
        raise ValueError(f"{settings.api_key_env} is required when AI is enabled")

    def summarize(messages: tuple[DigestMessage, ...]) -> DigestSummary:
        payload_messages = [
            {
                "sender": message.sender,
                "subject": message.subject,
                "snippet": message.snippet[:500],
            }
            for message in messages[: settings.max_digest_messages]
        ]
        body = json.dumps(
            {
                "model": settings.model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Vat nieuwsbriefmetadata in het Nederlands samen. Geef geldige JSON "
                            "met executive_summary en action_items als arrays van korte strings. "
                            "Verzin "
                            "niets. Action_items bevat alleen concrete acties of toekomstige data."
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload_messages, ensure_ascii=False)},
                ],
            }
        ).encode()
        request = Request(
            "https://api.mistral.ai/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            response_body = json.load(response)
        content = json.loads(response_body["choices"][0]["message"]["content"])
        return DigestSummary(
            executive_summary=tuple(str(item) for item in content.get("executive_summary", [])),
            action_items=tuple(str(item) for item in content.get("action_items", [])),
        )

    return summarize


def _section_title(label: str) -> str:
    root = label.split("/", maxsplit=1)[0]
    return root.replace("-", " ").capitalize()
