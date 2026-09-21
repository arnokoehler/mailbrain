# ADR 0001: Safe execution boundaries

## Context

MailBrain coordinates SQLite state with Gmail, but those systems do not share a
transaction. Cached message state can become stale, concurrent processes can
prepare conflicting work, and a network timeout can hide a successful write.

## Decisions

- Acquire one non-blocking OS file lock at each stateful CLI or repair-script
  boundary. Services do not acquire nested locks.
- Treat only the latest complete successful scan as usable. Commit scan
  invalidation together with apply intent before the first Gmail mutation, and
  invalidate the latest successful scan before rollback writes.
- Persist the complete intended run and mutations before writes. Mark batches
  in flight before calling Gmail and checkpoint confirmed results afterward.
- Treat ambiguous write outcomes as uncertain. Reconciliation reads Gmail and
  updates local status/cache only; it does not replay Gmail mutations.
- Retry bounded Gmail reads only. Gmail writes are sent once because an HTTP
  or transport failure can arrive after the remote mutation was accepted.
- Require explicit operator abandonment before unresolved intent stops blocking
  independent writers. Abandonment preserves the audit trail and never revives
  an invalidated scan.
- Permit rollback only for confirmed durable mutations whose affected labels
  still match the expected state and do not overlap later MailBrain mutations.
  Apply rollback volume limits without classification protection heuristics.

## Risks

- Gmail can change after live revalidation and before a write; this is not a
  compare-and-swap guarantee.
- A timeout after Gmail accepted a write cannot prove causality. Such mutations
  remain uncertain and are not automatically replayed or rolled back.
- Removing and later re-adding the same Gmail label can produce the same final
  set and cannot be inferred from snapshots.
- Protected-message heuristics are incomplete. Volume limits and explicit
  confirmation reduce impact but do not prove classification correctness.
