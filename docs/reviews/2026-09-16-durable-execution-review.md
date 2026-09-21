# Review — durable execution, safety limits and run recovery

**Date:** 2026-09-16
**Scope:** uncommitted working tree on `master` (34 modified files, 10 new; +3368 / −815)
**Base:** `fd3641f` (merge of PR #1)

New modules: `src/mailbrain/safety.py`, `src/mailbrain/locking.py`, `src/mailbrain/runs.py`,
`src/mailbrain/storage/migrations/` (alembic, revisions `0001_baseline` … `0004_recovery_proof`).
New tests: `tests/test_safety.py`, `tests/test_locking.py`, `tests/test_runs.py`,
`tests/test_cli_runs.py`, `tests/storage/test_migrations.py`.

## Verdict

Do not merge as-is. The engineering is good and the change closes the real gaps — partial
apply-writes, stale scans, concurrent writers, file permissions. Two changes in it are each
defensible alone but together remove every way to undo anything already applied, on a mailbox
where roughly 3000 labels and 941 archives have already been written. Fix findings 1 and 2
first; ship the rest.

### Verified

| Check | Result |
| --- | --- |
| `ruff check .` | clean |
| `mypy` (strict, 30 files) | clean |
| `pytest` | 221 passed (was 144) |
| `mailbrain db upgrade` on a copy of the live 9.8 MB `state.db` | succeeded; backup written at mode 0600 |
| Data after migration | 22770 messages, 4102 mutations, 5 runs, 288 labels — all preserved |

The migration path is careful: a legacy database is validated against the canonical legacy
schema (`storage/db.py:120`), refused if unrecognised, backed up (`storage/db.py:124`), stamped
at baseline and only then upgraded. Good.

## Blocking

### 1. Nothing from runs 1–5 can be reversed any more

Two independent changes close the same exit:

- `scripts/reconcile.py` write mode is disabled — it exits 2 and points at `mailbrain rollback`
  (`scripts/reconcile.py:127`). Same for `scripts/unlabel.py`.
- After migration all five existing runs get `status = "legacy"`, and `plan_rollback` returns an
  **empty** plan for that status (`src/mailbrain/rollback.py:70`).

Observed on the migrated copy:

```
run 1 type=apply status=legacy intended=0 confirmed=0 failed=0 uncertain=0 conflict=0
...
run 5 type=apply status=legacy intended=0 confirmed=0 failed=0 uncertain=0 conflict=0
```

So reconcile is no longer permitted and rollback is no longer possible. Everything runs 1–5
wrote is now permanent as far as the tooling goes.

`rollback` is also not a substitute for reconcile, independent of the legacy status.
`plan_rollback` takes only a `run_id` (`rollback.py:51`) and reverses the whole run. Run 2
applied 2435 labels of which 365 were wrong; reconcile removed exactly those 365, rollback would
discard all 2435. Different problems. Rollback additionally raises `RollbackConflictError` when a
later run touched any of the same labels (`rollback.py:112`), which after five runs is close to
certain.

Disabling the scripts is justified — they wrote to Gmail with no safety caps, no lock and no
audit run. The fix is to move that capability into the audited CLI, not to drop it.

**Repairable.** All 4102 legacy mutations still carry the name-based history:

```sql
SELECT status, COUNT(*), SUM(gmail_label_ids_before IS NULL), SUM(labels_before IS NOT NULL)
FROM mutations GROUP BY status;
-- legacy | 4102 | 4102 | 4102
```

`labels_before` / `labels_after` are populated for every row and the `labels` table holds 288
name→id mappings. A backfill migration that derives `gmail_label_ids_before/add/remove` from the
names restores rollback eligibility for the historical runs.

### 2. The existing 22770-message cache is unusable without a re-scan

`classify` now requires a `ScanRun` record, which a legacy database does not have. On the
migrated copy:

```
$ mailbrain classify
No scan is available; run mailbrain scan first.
$ mailbrain apply --dry-run
No scan is available; run mailbrain scan first.
```

Correct as the stale-scan fix, but it means a full re-scan before anything works again, and
`--resume` now deliberately performs a fresh scan instead of trusting the cache
(`README.md`), so that shortcut is gone too. Worth stating in the README upgrade notes rather
than leaving the operator to discover it.

## Correctness

### 3. Gmail quota rises by roughly two orders of magnitude

`_sync_cached_message` issues one `get_metadata` call **per message** after every
`batch_modify` — called from the loop at `src/mailbrain/apply.py:398`, the call itself at
`apply.py:410`. For a 1000-message batch that is 1 batch call plus 1000 metadata calls — in
quota units, 50 versus 5050.

`plan_rollback` does the same per candidate mutation, sequentially, inside one open session
(`rollback.py:119`). For run 2 that would be 2435 serial API calls holding a SQLite transaction.

The local fallback already exists (`apply.py:413`: apply the recorded add/remove delta to the
cached ids). Making the live read opt-in, or batching it, keeps the cache accurate without
paying per message.

### 4. One timestamp for an entire run

`execute_plan` computes `timestamp` once (`apply.py:97`) and passes it to every `started_at`,
`finished_at`, `prepared_at` and `reconciled_at` in the run. Every mutation therefore records
start == end and every run has zero duration. For a table whose purpose is the audit trail, the
timing information is lost. Take `datetime.now(UTC)` at each state transition.

### 5. Batch labels are read from `mutations[0]`

`apply.py:368` derives `add_ids` / `remove_ids` from the first mutation in the batch and applies
them to all its members. Correct today, because `_resolve_mutation_ids` groups precisely on
`(add_ids, remove_ids)` (`apply.py:313`). It is an unguarded invariant: if grouping ever changes,
this silently writes the first message's labels to up to 1000 messages. Carry the group key
through, or assert that all members agree.

### 6. `reconcile_run` cannot establish success

A mutation whose live labels exactly match the desired set stays `uncertain`
(`src/mailbrain/runs.py:114`); only `conflict` is ever distinguished. This is deliberate and
pinned by `tests/test_runs.py:33` (`test_reconcile_marks_in_flight_uncertain_without_writing`,
live labels `{INBOX, L1}` == desired, asserted `uncertain`).

Label intents are treated differently — a visible label is promoted to `resolved`
(`runs.py:148`). The asymmetry is what stands out.

Consequences: `applied_count` under-reports, and `abandon_run` marks provably applied mutations
as `abandoned` (`runs.py:217`). The evidence for a positive verdict is already computed —
`live_ids & affected_ids == desired_ids & affected_ids` at `runs.py:125`. "Never claim success"
is a defensible stance, but it means a run that fully succeeded and then crashed can never be
closed as succeeded.

## Minor

| Location | Note |
| --- | --- |
| `runs.py:12` | imports `_refresh_run_counts` and `_sync_cached_message`, both private, from `apply`. Promote them or move to a shared module. |
| `runs.py:253` | function-local `import json` with no cycle to avoid. |
| `runs.py:255` | `list(json.loads(value or "[]"))` silently yields keys if the payload is an object. |
| `safety.py:45` | `preview_allowed` always returns `True`. |
| `safety.py:17` | `max_scan_age_minutes` is in `REQUIRED_LIMITS` but unused in `validate_plan`; enforcement lives at `cli.py:285`. Split responsibility. |
| `locking.py:5` | `fcntl` is Unix-only. |
| `storage/db.py:330` | the backup is written 0600 but the live `state.db` stays 0644. File permissions were an explicit goal; the live database holds the same mail metadata. |
| `apply.py:180` | the scan is marked `invalidated` before any write, so an apply that then fails at label resolution still burns the scan. Safe direction, costs a re-scan. |
| `apply.py:67` | HTTP 409 is classified indefinite. Defensible, and the safe direction. |

## Good

- Intent is committed before any write; `assert_no_unresolved_intents` (`apply.py:70`) stops a
  second writer while state is ambiguous.
- `is_definite_response_failure` (`apply.py:62`) separates 4xx from 408/409/429 and 5xx, so a
  timeout is recorded as uncertain rather than failed. That distinction is the whole point.
- Label creation is idempotent under retry: live labels are re-read before creating, and after a
  failure the code checks whether the label appeared anyway (`apply.py:244`).
- `SafetySettings` is fail-closed: `writes_allowed` requires no reasons **and** no missing limits
  (`safety.py:50`), so an unconfigured limit blocks writes instead of defaulting to permissive.
- Strict pydantic types with `extra="forbid"` on the settings models — a typo in
  `settings.yaml` now fails loudly.
- Protected-message criteria cover payslips, mortgage, security alerts and boarding passes, which
  matches the classes that went wrong in the previous round.
- `--dry-run` previews still work with missing limits and report them as blockers, so the
  operator can see what to configure.

## Recommended order

1. Backfill migration `0005` populating `gmail_label_ids_{before,add,remove}` from the name
   columns, so runs 1–5 become rollback-eligible again.
2. `mailbrain repair RUN_ID` — what `scripts/reconcile.py --apply` did (remove labels no current
   rule justifies, restore wrongly archived mail), but with safety validation, the process lock
   and its own audit run. Then disabling the script is unambiguously correct.
3. Finding 3 (quota), then 4, 5 and 6.
4. README upgrade note covering the required re-scan and the safety limits that must be set
   before writes are allowed.
