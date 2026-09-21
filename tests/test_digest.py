import json
from datetime import datetime

from mailbrain.config import Rule, RuleActions, RuleMatch
from mailbrain.digest import (
    DigestSummary,
    build_digest,
    load_week_messages,
    parse_iso_week,
    render_markdown,
)
from mailbrain.storage import db
from mailbrain.storage.models import Label, Message


def newsletter_rule() -> Rule:
    return Rule(
        id="nieuwsbrief-archive-tldr-covered",
        match=RuleMatch(from_domain=["example.com"]),
        actions=RuleActions(archive=True),
    )


def test_build_digest_selects_newsletters_and_groups_existing_labels():
    _, start, _ = parse_iso_week("2026-W38")
    messages = [
        _message("newsletter", "news@example.com", "Useful news", start, ("Werk/AI",)),
        _message("receipt", "shop@store.com", "Your receipt", start, ("Administratie",)),
    ]

    digest = build_digest(
        "2026-W38",
        messages,
        [newsletter_rule()],
        lambda selected: DigestSummary(
            executive_summary=(f"{len(selected)} nieuwsbrief",),
            action_items=("Lees het onderzoek",),
        ),
    )

    assert digest.message_count == 1
    assert digest.sections[0].title == "Werk"
    assert digest.summary.executive_summary == ("1 nieuwsbrief",)
    markdown = render_markdown(digest)
    assert "## Kernpunten" in markdown
    assert "- [ ] Lees het onderzoek" in markdown
    assert "https://mail.google.com/mail/u/0/#all/newsletter" in markdown
    assert "Your receipt" not in markdown


def test_load_week_messages_only_loads_requested_week(tmp_path):
    database = tmp_path / "state.db"
    db.init_db(database)
    factory = db.session_factory(database)
    _, start, end = parse_iso_week("2026-W38")
    with factory() as session:
        session.add(Label(gmail_id="work", name="Werk/AI"))
        session.add_all(
            [
                Message(
                    gmail_id="inside",
                    sender="news@example.com",
                    subject="Inside",
                    snippet="Relevant",
                    internal_date=start,
                    label_ids=json.dumps(["work"]),
                ),
                Message(
                    gmail_id="outside",
                    sender="news@example.com",
                    subject="Outside",
                    snippet="Old",
                    internal_date=end,
                    label_ids=json.dumps(["work"]),
                ),
            ]
        )
        session.commit()

    messages = load_week_messages(factory, start, end)

    assert [message.gmail_id for message in messages] == ["inside"]
    assert messages[0].labels == ("Werk/AI",)


def test_parse_iso_week_rejects_invalid_week():
    try:
        parse_iso_week("week 38")
    except ValueError as error:
        assert "YYYY-Www" in str(error)
    else:
        raise AssertionError("invalid ISO week accepted")


def _message(
    gmail_id: str,
    sender: str,
    subject: str,
    internal_date: datetime,
    labels: tuple[str, ...],
):
    from mailbrain.digest import DigestMessage

    return DigestMessage(
        gmail_id=gmail_id,
        sender=sender,
        subject=subject,
        snippet="A useful snippet",
        internal_date=internal_date,
        labels=labels,
    )
