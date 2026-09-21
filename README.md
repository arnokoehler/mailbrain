# MailBrain

Local-first CLI that classifies Gmail with deterministic rules, applies labels
and archives in bulk, and generates weekly newsletter digests. Keeps a full
audit trail with rollback. **The tool never deletes mail** — deletion stays a
manual action in Gmail as a safeguard.

See `docs/superpowers/specs/2026-07-11-mailbrain-design.md` for the design.

## Requirements

- Python 3.13+ (managed automatically by `uv`)
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
uv run mailbrain init
```

Set explicit safety limits in `config/settings.yaml` before enabling writes.
Preview commands work with missing limits but report them as blockers.

## Commands

```bash
mailbrain init
mailbrain db upgrade
mailbrain scan [--settings config/settings.yaml] [--query "in:inbox"]
mailbrain classify [--settings config/settings.yaml] [--rules config/rules.yaml]
mailbrain digest [--week 2026-W38] [--output digest.md]
mailbrain apply --dry-run [--settings config/settings.yaml]
mailbrain apply --scan-id ID [--settings config/settings.yaml] [--yes]
mailbrain rollback RUN_ID --dry-run [--settings config/settings.yaml]
mailbrain rollback RUN_ID [--settings config/settings.yaml] [--yes]
mailbrain runs
mailbrain runs inspect RUN_ID
mailbrain runs reconcile RUN_ID [--settings config/settings.yaml]
mailbrain runs abandon RUN_ID --reason "operator resolution" --yes
```

- `--settings` defaults to `config/settings.yaml`. Its Gmail query, page size,
  batch size, freshness limit, volume limits, and protected-message criteria
  are enforced by the CLI.
- `scan` creates a complete explicit scope. Failed scans are never used, and
  `--resume` deliberately performs a fresh scan rather than trusting old cache.
- `classify` and `apply --dry-run` use the latest scan only and show its ID,
  query, age, status, and all safety blockers. A successful empty scan is valid.
- `digest` selects messages covered by `nieuwsbrief-*` rules from the local
  cache and writes a deterministic Markdown overview under `reports/`. Set
  `ai.enabled: true` and `MISTRAL_API_KEY` to add synthesized Dutch key points
  and action items from sender, subject, and at most 500 characters of snippet.
- With `notion.enabled: true`, `digest` also publishes one native Notion child
  page per ISO week. Repeated runs do not replace an existing published page.
- Gmail writes require `apply --scan-id ID`, a fresh latest successful scan,
  complete safety settings, confirmation, and unchanged live Gmail metadata.
  `--yes` skips only confirmation. Intent is committed before any write.
- `rollback` previews and confirms the eligible inverse plan. It checks current
  Gmail state and configured volume limits first. Any inverse archive also uses
  a freshly listed `in:inbox` population for its fraction budget.
- `runs reconcile` performs Gmail reads and local recovery updates only. It
  never mutates Gmail. `runs abandon` is an explicit, audited local closure.
- All stateful commands use one non-blocking process lock under
  `MAILBRAIN_HOME`; contention exits with code 2.
- `scripts/reconcile.py --apply` and `scripts/unlabel.py --apply` are disabled.
  Their analysis modes remain read-only.

Gmail and SQLite cannot provide an atomic transaction. A timeout after a Gmail
write remains uncertain and requires `runs reconcile`; it is not retried or
automatically rollback-eligible. Rollback compares only labels touched by the
original mutation, so remove-and-readd history that ends in the same state
cannot be detected. Protected-message heuristics are conservative but cannot
identify every important message. **Nothing is ever deleted.**

## Gmail API credentials (manual, one-time)

MailBrain needs an OAuth **Desktop** client. This step is manual — do it once
before running `mailbrain scan`:

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or pick one).
3. Enable the **Gmail API** (APIs & Services → Library → Gmail API → Enable).
4. Configure the OAuth consent screen (External; add yourself as a test user).
5. Credentials → Create Credentials → **OAuth client ID** → Application type
   **Desktop app**.
6. Download the JSON and save it as `~/.mailbrain/credentials.json`.

Requested scope is `https://www.googleapis.com/auth/gmail.modify` — read, label,
and archive only. No delete scope is ever requested.

## Notion publishing

Create a Notion connection in the developer portal, enable read and insert
content capabilities, and share the TLDR parent page with that connection. Keep
the token outside configuration:

```bash
export NOTION_TOKEN="secret_..."
```

Enable publishing in `config/settings.yaml`:

```yaml
notion:
  enabled: true
  parent_page_id: "39a840ba5dab8129a775ccb343e24bee"
  token_env: NOTION_TOKEN
```

`mailbrain digest` first writes the local Markdown report and then creates
`MailBrain TLDR — YYYY-Www` below that parent. The local `digests` table prevents
duplicate publication. If a create result is uncertain, rerunning performs an
exact-title reconciliation and never blindly creates a second page. A Notion
failure is recorded and reported as a warning without failing local digest
generation or blocking another week. After checking Notion manually, use
`--retry-uncertain` to explicitly retry an unresolved week when no page exists.
Likewise, an unavailable Mistral integration falls back to the deterministic
overview instead of preventing the report.

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run mypy            # type-check
```
