# MailBrain

Local-first CLI that classifies Gmail with deterministic rules, applies labels
and archives in bulk, and (later) publishes weekly Notion digests. Keeps a full
audit trail with rollback. **The tool never deletes mail** — deletion stays a
manual action in Gmail as a safeguard.

See `docs/superpowers/specs/2026-07-11-mailbrain-design.md` for the design.

## Requirements

- Python 3.13+ (managed automatically by `uv`)
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
uv run mailbrain init      # creates ~/.mailbrain/ and state.db
```

## Commands

```bash
mailbrain init                         # create ~/.mailbrain/ and the SQLite db
mailbrain scan  [--query "in:inbox"]   # fetch Gmail metadata into the local cache
mailbrain classify [--rules config/rules.yaml]  # dry-run: print what WOULD change
mailbrain apply    [--rules config/rules.yaml] [--dry-run] [--yes]  # apply the plan to Gmail
mailbrain rollback <run-id>            # reverse every change made by a previous run
```

- `scan` needs `~/.mailbrain/credentials.json` (see below) and does a live,
  read-only fetch — it never modifies mail.
- `classify` runs fully offline on the cached data and only prints a plan;
  nothing is applied.
- `apply` **writes to Gmail** — it prints the plan, then asks for confirmation
  before mutating (adding labels, removing `INBOX` to archive and `UNREAD` to
  mark-read). Use `--dry-run` to print the plan and stop, or `--yes`/`-y` to
  skip the prompt (for scripts). Every mutation is recorded under a run in the
  local db.
- `rollback <run-id>` replays a run's recorded mutations in reverse, restoring
  the pre-run label state. Both `apply` and `rollback` need `credentials.json`.
  **Nothing is ever deleted** — removing a message stays a manual Gmail action.

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

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run mypy            # type-check
```
