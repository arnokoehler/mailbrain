# ADR 0002: Native Notion digest publication

## Context

MailBrain generates a weekly Markdown digest locally. Notion and SQLite do not
share a transaction, and a failed create-page response can hide a successful
remote write. Replaying such a write can create duplicate weekly pages.

## Decisions

- Use the Notion REST API directly with the standard library and API version
  `2026-03-11`; this version accepts native Markdown during page creation.
- Read the integration token from an environment variable and identify the
  existing parent with an explicit page ID.
- Use `digests.iso_week` as the publish-once registry and a deterministic child
  title as the remote recovery key.
- Search for an existing exact-title child before creating a page. Persist a
  null reservation before the create and fill its page ID only after a confirmed
  response.
- Retry POST only for explicit rate-limit and overload responses. Preserve an
  unresolved reservation after any ambiguous create outcome and reconcile by
  reading Notion on the next invocation.
- Record publication state and errors per ISO week. Notion failures produce a
  warning rather than failing digest generation, and failed weeks can retry
  independently. Replaying an unresolved create requires explicit operator
  intent through `--retry-uncertain`.
- Never update an already published page in this feature slice, avoiding
  accidental replacement of manual edits.

## Risks

- A process that stops after reservation but before sending the request leaves a
  conservative unresolved row that requires manual database repair.
- Exact-title recovery depends on users not renaming a page before local
  completion.
- Publish-once behavior does not incorporate mail arriving after publication or
  later improvements to a generated summary.
- A direct REST implementation owns response validation and retry policy that an
  SDK would otherwise provide.
