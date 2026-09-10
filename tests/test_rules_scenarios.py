"""Regression scenarios for the shipped ruleset in config/rules.yaml.

Each case is a mailbox situation that was once classified wrongly. They run
against the real rules file, so a rule edit that reintroduces one fails here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mailbrain.config import load_rules
from mailbrain.rules.engine import classify
from mailbrain.rules.models import MessageMeta

RULES = load_rules(Path("config/rules.yaml")).rules
NOW = datetime(2026, 9, 9, tzinfo=UTC).replace(tzinfo=None)
PROMO = ("INBOX", "UNREAD", "CATEGORY_PROMOTIONS")
PRIMARY = ("INBOX", "UNREAD", "CATEGORY_PERSONAL")


def outcome(sender: str, subject: str, labels: tuple[str, ...] = PRIMARY, days_old: int = 1):
    msg = MessageMeta(
        gmail_id="m",
        sender=sender,
        subject=subject,
        internal_date=NOW - timedelta(days=days_old),
        current_labels=labels,
    )
    results = classify([msg], RULES, NOW)
    if not results:
        return frozenset(), False
    return frozenset(results[0].add_labels), results[0].archive


@pytest.mark.parametrize(
    ("sender", "subject"),
    [
        ("noreply@marktplaats.nl", "Bevestiging van je bestelling"),
        ("info@service.donaldduck.nl", "Je factuur van de bestelling"),
        ("news@asmodee.net", "Your order has shipped"),
    ],
)
def test_promo_archive_never_sweeps_a_transaction(sender, subject):
    _, archived = outcome(sender, subject, labels=PROMO)
    assert not archived


@pytest.mark.parametrize(
    "subject",
    [
        "MEGA DEAL: scoor 20% korting op je bestelling!",
        "Bestel nu en krijg: GRATIS bezorging!",
    ],
)
def test_an_advert_quoting_transactional_words_is_still_swept(subject):
    _, archived = outcome("info@tacomundo.com", subject, labels=PROMO)
    assert archived


def test_promotional_payslip_is_not_archived():
    labels, archived = outcome(
        "bedankemail@ah.nl", "Bekijk hier je salaris van periode 2", PROMO
    )
    assert "Administratie/Loonstrook" in labels
    assert not archived


def test_purchase_in_promotions_still_gets_a_purchase_label():
    labels, archived = outcome(
        "no-reply@coolblue.nl", "Je Coolblue bestelling is verstuurd", PROMO
    )
    assert "Administratie/Aankopen/pakketjes/coolblue" in labels
    assert not archived


def test_advert_in_promotions_gets_no_purchase_label_and_is_swept():
    labels, archived = outcome(
        "info@noreply.coolblue.eu", "Arno, laatste kans voor onze Back to School-deals", PROMO
    )
    assert not any(lbl.startswith("Administratie/Aankopen") for lbl in labels)
    assert archived


@pytest.mark.parametrize("subject", ["Your subscription renewal", "JetBrains invoice 2026"])
def test_jetbrains_billing_is_not_archived(subject):
    labels, archived = outcome("news@jetbrains.com", subject)
    assert "Administratie/Aankopen/Software" in labels
    assert not archived


def test_klm_travel_document_in_promotions_is_classified():
    labels, archived = outcome("noreply@klm.com", "Uw KLM-boardingpas voor uw vlucht", PROMO)
    assert "Reizen" in labels
    assert not archived


def test_rabobank_subdomain_mortgage_mail_is_mortgage():
    labels, _ = outcome("noreply@e-mail.rabobank.nl", "Jouw hypotheek offerte")
    assert "hypotheek" in labels


def test_io_lease_mail_carries_both_work_and_lease():
    labels, _ = outcome("hr.utrecht@iodigital.com", "Re: Bestellen nieuwe leaseauto")
    assert labels == frozenset({"Werk/iOFrontmen", "Administratie/leaseauto"})


def test_booking_job_mail_is_recruitment_not_travel():
    labels, _ = outcome("toma.skimelyte@booking.com", "A job that fits your profile")
    assert "Werk/recruitment" in labels
    assert "Reizen" not in labels


def test_boekenbalie_transaction_is_a_purchase_not_a_book_tip():
    labels, archived = outcome(
        "klantenservice@boekenbalie.nl", "Factuur van je bestelling (3001711304)"
    )
    assert "Administratie/Aankopen" in labels
    assert "Kennis/Boeken" not in labels
    assert not archived


def test_boekenbalie_newsletter_is_still_a_book_tip():
    labels, archived = outcome(
        "nieuwsbrief@boekenbalie.nl", "100 boeken die je gelezen moet hebben"
    )
    assert "Kennis/Boeken" in labels
    assert archived
