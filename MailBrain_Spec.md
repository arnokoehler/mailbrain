# MailBrain -- Product Spec & Build Prompt (v0.1)

## Goal

Build **MailBrain**, a local-first CLI that classifies Gmail, applies
labels in bulk, generates weekly Notion digests, and keeps a complete
audit trail with rollback support.

Target audience: power users with large Gmail archives.

## Tech stack

-   Python 3.13+
-   Typer (CLI)
-   Pydantic
-   Rich
-   SQLAlchemy
-   SQLite (single local file)
-   Google Gmail API
-   Notion API
-   PyYAML
-   Optional: OpenAI API

## Design principles

-   Local-first
-   Rules before AI
-   Dry-run by default
-   Idempotent
-   Rollback for every mutation
-   Configuration over code
-   Batch Gmail operations
-   No Docker required

## Repository

``` text
mailbrain/
├── pyproject.toml
├── README.md
├── config/
│   ├── rules.yaml
│   └── settings.yaml
├── src/mailbrain/
│   ├── cli.py
│   ├── config.py
│   ├── gmail/
│   ├── notion/
│   ├── rules/
│   ├── ai/
│   ├── digest/
│   ├── reporting/
│   └── storage/
└── tests/
```

## CLI

``` bash
mailbrain init
mailbrain auth
mailbrain scan
mailbrain classify
mailbrain review
mailbrain apply
mailbrain rollback <run-id>
mailbrain digest
mailbrain weekly
```

## Local storage

    ~/.mailbrain/
        credentials.json
        token.json
        state.db
        reports/
        cache/

SQLite schema:

-   messages
-   threads
-   labels
-   runs
-   mutations
-   digests

## Classification pipeline

1.  Fetch Gmail metadata
2.  Load YAML rules
3.  Match exact rules
4.  Detect conflicts
5.  Optional AI classification
6.  Generate plan
7.  Dry-run report
8.  Apply Gmail mutations
9.  Persist audit
10. Publish Notion digest

## Rule format

``` yaml
rules:
  - id: booking-payment
    match:
      from_domain:
        - booking.com
      subject_contains:
        - payment received
        - receipt
    actions:
      add_labels:
        - Reizen
      archive: true

  - id: password-reset
    match:
      subject_regex:
        - "(?i)password reset"
      older_than_days: 3
    actions:
      add_labels:
        - to-be-removed
      archive: true
      mark_read: true
```

## AI interface

The model receives only:

-   sender
-   subject
-   snippet
-   existing labels
-   allowed labels

Returns:

``` json
{
  "label":"Reizen",
  "confidence":0.98,
  "reason":"Booking payment confirmation"
}
```

Only used if no deterministic rule matches.

## Weekly Notion digest

Parent:

    Personal/
      Mail System/
        Weekly Digest/

Create one page per ISO week.

Sections:

-   Executive summary
-   Action items
-   Finance
-   Home
-   Travel
-   Jamie & Family
-   Health
-   Work
-   Development
-   Investing
-   Communities (JCrete)
-   FYI
-   Statistics

Never copy entire email bodies. Store summaries, actions and Gmail links
only.

## Rollback

Every mutation stores:

-   run id
-   message id
-   labels before
-   labels after

CLI:

``` bash
mailbrain rollback <run-id>
```

## Metrics

Display after every run:

-   scanned
-   classified by rules
-   classified by AI
-   archived
-   labeled
-   review queue
-   errors
-   duration

## Initial built-in rules

-   Booking → Reizen
-   Campings → Reizen
-   Password reset (\>3d) → to-be-removed
-   Android Weekly → to-be-removed
-   BNNVARA newsletters → to-be-removed
-   Follow This → to-be-removed
-   Social Schools → to-be-removed
-   Library newsletters → to-be-removed
-   JCrete / Heinz Kabutz → JCrete
-   BUX reports → Beleggen
-   DEGIRO → Beleggen/deGiro
-   Energy/internet for home → Albrecht Thaerlaan 57
-   Orders/invoices → Administratie/Aankopen

## Prompt for GPT-5.6

You are the lead software architect.

Build this project incrementally.

Requirements:

-   Production-quality Python.
-   Modern typing everywhere.
-   Small cohesive modules.
-   Testable architecture.
-   Ruff + mypy clean.
-   90%+ unit test coverage for rules engine.
-   Never generate placeholder implementations.
-   Prefer composition over inheritance.
-   Explain architectural decisions briefly.
-   After every milestone, stop and wait for review.
-   Keep commits small and self-contained.

Milestones:

1.  Project skeleton
2.  Configuration
3.  SQLite storage
4.  Gmail authentication
5.  Gmail scanner
6.  Rule engine
7.  Dry-run reporting
8.  Gmail mutations
9.  Rollback
10. Notion digest
11. AI classifier
12. Documentation
