# Native Notion Digest Publishing

## Goal

Publish the Markdown produced by `mailbrain digest` as one native child page of
the existing TLDR page per ISO week. Publishing remains optional and never
reads from or writes to Gmail.

## Configuration

```yaml
notion:
  enabled: false
  parent_page_id: "39a840ba5dab8129a775ccb343e24bee"
  token_env: NOTION_TOKEN
```

The integration token is read only from the configured environment variable.
The parent page must be shared with the Notion connection with read and insert
content capabilities.

Missing or invalid optional integration configuration produces a warning after
the local report is written. It does not turn digest generation into a failed
run.

## Command

`mailbrain digest [--week YYYY-Www]` always regenerates the local Markdown
report. When Notion is enabled, it additionally publishes that report under the
configured parent with title `MailBrain TLDR — YYYY-Www`.

## Idempotency

The existing `digests.iso_week` unique key is the local publication registry.
A completed row contains the Notion page ID and makes later runs a no-op for
Notion. Before the first create, MailBrain searches direct child pages for the
exact deterministic title and adopts one matching page.

MailBrain commits a row with a null page ID before creating the remote page. If
the create result is uncertain, the row remains unresolved. A later invocation
searches the parent and recovers one exact match. It never automatically repeats
an uncertain create unless the operator supplies `--retry-uncertain`. Multiple
exact matches are recorded as a failed attempt without blocking other weeks.

## API

- `GET /v1/blocks/{parent_page_id}/children` discovers existing child pages and
  follows pagination cursors.
- `POST /v1/pages` creates a child page with `markdown` using Notion API version
  `2026-03-11`.
- GET retries rate limits, overload, transient server responses, and transport
  failures. POST retries only HTTP 429 and 529, following `Retry-After`.
- Markdown requests above 490 KB or an estimated 950 blocks are rejected before
  a reservation or remote write.

## Failure Semantics

HTTP 400, 401, 403, and 404 are definite rejections and mark the week failed so
a later run can retry it. Timeouts, transport failures, conflicts, server errors,
and malformed success responses are uncertain and preserve the null reservation
for reconciliation. Publication failures are warnings: the local report and CLI
run remain successful, and other weeks remain publishable.

No database transaction remains open during a network request. A successful
Notion create followed by a failed local commit is recovered by exact child-page
title on the next invocation.

## Non-Goals

- Updating or replacing an already published page.
- Publishing into a Notion database.
- Notion OAuth or hosted user accounts.
- Automatic replay when a reservation exists but no remote page can be found.
- A combined `weekly` command.
