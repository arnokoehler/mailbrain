# MailBrain — Design Spec v0.2

**Date:** 2026-07-11
**Status:** Approved design (supersedes `MailBrain_Spec.md` v0.1 where they conflict)
**Origin:** Pressure-test of v0.1 spec. This document records the decisions that resolve the ambiguities and gaps found in v0.1. v0.1 remains the reference for tech stack, repo layout, rule format, and built-in rule list.

---

## Purpose of this document

v0.1 described *what* MailBrain is. It left several architectural questions unanswered — questions that shape storage, the apply pipeline, and the AI boundary. This spec resolves each one and defines the phasing. Read this alongside v0.1.

---

## Core principle refined: the trust model

The single most important decision. MailBrain divides all mail actions into **auto-apply** and **never**.

| Action | Behaviour |
|---|---|
| `add_labels` | Auto-apply (rules always; AI only Phase 2, `confidence ≥ floor`) |
| `mark_read` | Auto-apply |
| `archive` | Auto-apply |
| **delete / trash** | **Never performed by the tool.** |

Rationale: the whole point of MailBrain is to *not* review mail one-by-one. Labelling and archiving are reversible (rollback + Notion digest surface mistakes), so they run unattended. Deletion is irreversible-in-spirit and stays a **manual human action in Gmail** as a safeguard.

`to-be-removed` is a **marker label only**. Mail the rules deem junk gets labelled `to-be-removed` and archived. The user later selects that label in Gmail and deletes manually. MailBrain has no `purge`, no `trash` action, no destructive command.

**Consequence — the review queue nearly vanishes.** The only thing that ever needs a human decision is a genuine **conflict** (see Rules engine). There is no per-item approval step for labels or archiving.

---

## Idempotency

v0.1 said "idempotent" without a mechanism. Two orthogonal mechanisms:

### Apply idempotency — desired-state diff (authoritative)
Each run computes the **desired** label/state set for every candidate message from the current rules, then diffs against the message's **current** Gmail state, and mutates only the delta.

- Re-running with no rule changes and no new mail → empty delta → no-op.
- Editing a rule → new delta appears on affected messages → applied. No "processed" flag, so rule changes are never stale.
- The `mutations` table (labels-before / labels-after per message per run) **is** the diff record and the rollback record — one structure, two uses.

A `processed` boolean flag is explicitly **rejected**: it would freeze old mail against rule changes.

### Scan idempotency / efficiency — Gmail `historyId` incremental sync
`scan` uses `users.history.list` from the last stored `historyId` to fetch only messages changed since the previous scan, instead of re-listing the whole archive.

- Stored as a `sync_state` row (or column) plus `messages.history_id`.
- **Deferrable**: Phase 1 may ship full-list scan first; incremental sync is a later optimization milestone. It affects scan *cost*, not correctness.

---

## Rules engine

### Conflict handling
When multiple rules match one message:

- **`add_labels` are unioned** — additive. A message can legitimately be `Reizen` *and* `Administratie`. No conflict.
- **Boolean actions** (`archive`, `mark_read`) that **disagree** across matching rules (one says archive, another implies keep) → the message goes to the **review queue** flagged `conflict`. The tool does not guess.

This is the *only* population of the review queue.

### Review queue
- Table: `review_queue`. Fields: `message_id`, `run_id`, `suggested` (JSON: labels + booleans), `source` (`conflict` in Phase 1; `ai` reserved for Phase 2 if ever needed), `reason`, `status` (`pending` | `resolved`).
- Surfaced via `mailbrain review` as a human-readable list (Rich table + optional YAML export to `~/.mailbrain/reports/`). Expected to be near-empty in normal operation.

---

## Label resolution

Rules name labels by string, including nested paths (`Beleggen/deGiro`, `Administratie/Aankopen`). Gmail mutates by label **ID**, not name.

- **Auto-create missing labels**, including nested: create parent (`Beleggen`) before child (`Beleggen/deGiro`) if absent. Gmail represents nesting as `/` inside the label name.
- The `labels` table caches the **name ↔ id** map. Cache is refreshed on `scan`.
- Fail-fast (require pre-existing labels) was rejected as too much setup friction.

---

## Rollback

Trivial given the no-delete rule. `mailbrain rollback <run-id>` reverses every mutation recorded for that run:

- Added labels → removed.
- Removed labels → re-added.
- Archived → unarchived.
- Marked read → marked unread.

All operations are Gmail-reversible and idempotent (re-running a rollback is a no-op). No data is ever destroyed by the tool, so rollback can never fail to recover state.

---

## Notion digest (approach 9C, phased)

Sections (Finance, Travel, Health, Work, …) map to labels per v0.1.

- **Phase 1 — rule-aggregated, no AI:** per section, output counts + grouped subject lines + Gmail deep-links. Deterministic, cheap, fully testable. Never copies email bodies (per v0.1).
- **Phase 2 — Mistral-authored sections:** the two sections that need synthesis — **Executive summary** and **Action items** — are generated by Mistral from the minimal metadata (sender, subject, snippet). Everything else stays rule-aggregated. This caps AI cost to the parts that genuinely need it.

### Commands
- `digest` = generate + publish the current ISO-week Notion page only.
- `weekly` = orchestrator: `scan → classify → apply → digest` in one command, suitable for cron.

---

## AI classifier — all Phase 2, provider = Mistral

- Runs **only** when no deterministic rule matches a message ("rules before AI").
- Receives only: sender, subject, snippet, existing labels, allowed labels (per v0.1). Never bodies.
- Returns `{label, confidence, reason}`.
- **`confidence ≥ floor` → auto-apply the label** (same trust tier as a rule — reversible).
- **`confidence < floor` → skip**: leave the message unlabelled, retry on a future run or wait for a rule to be authored. No review queue entry, no manual step.
- `floor` default **0.85**, tunable in `settings.yaml`.
- Provider is **Mistral** (user account). Not OpenAI (v0.1) and not Anthropic.

---

## Gmail operations / quota

For "large archives":

- `users.messages.list` paginated (500 / page); metadata cached to SQLite.
- Fetch with `format=metadata` (headers + snippet only) — no bodies pulled. Cheaper and privacy-aligned.
- `users.messages.batchModify` chunked to **≤ 1000 ids per call**.
- Exponential-backoff retry wrapper on all Gmail calls for `429` / `403 rateLimitExceeded`.

---

## Phasing

### Phase 1 — rules-only, zero external AI (a complete product)
`init`, `auth`, `scan`, rules engine (with conflict→review), `classify`, dry-run reporting, `apply` (labels / archive / mark_read, desired-state diff), `rollback`, rule-aggregated Notion `digest`, `weekly` orchestrator, metrics.

Delivers a fully working tool with no dependency on any external AI provider.

### Phase 2 — Mistral
AI classifier (floor 0.85, auto-label above / skip below) + AI-authored digest sections (Executive summary, Action items).

---

## Unchanged from v0.1

Tech stack, repository layout, local storage paths, SQLite table list (`messages`, `threads`, `labels`, `runs`, `mutations`, `digests`; add `review_queue` and `sync_state`), rule YAML format, built-in rule list, metrics displayed after each run, and the incremental build/milestone discipline all carry over from `MailBrain_Spec.md` v0.1.

### Schema additions vs v0.1
- `review_queue` (conflicts).
- `sync_state` / `messages.history_id` (incremental scan).
- `mutations` explicitly doubles as the desired-state-diff / rollback record.
