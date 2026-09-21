# Changelog

All notable changes to MailBrain are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added (safe execution)
- Explicit settings-backed scan query, Gmail page/batch sizes, mutation limits,
  archive limits, scan freshness, and protected-message safety checks.
- Process locking for migrations, scans, cache-based planning, apply, rollback,
  and run recovery; lock contention exits with status 2.
- Versioned database initialization and `mailbrain db upgrade` with safe legacy
  schema validation and backup.
- Explicit scan lifecycle and membership. Classification and dry-run report the
  latest scan's ID, query, age, status, and safety blockers.
- Durable apply and rollback intent, checkpointed batches, deterministic rules
  and safety hashes, live drift checks, and actual execution status reporting.
- `mailbrain runs`, `runs inspect`, read-only `runs reconcile`, and confirmed
  `runs abandon` recovery commands.

### Changed (safe execution)
- Gmail writes now require `apply --scan-id ID`; `--yes` bypasses confirmation
  only. Safety, freshness, locking, and live revalidation always remain active.
- Rollback now supports `--dry-run` and `--yes`, checks live affected labels,
  enforces volume limits, and uses a fresh live INBOX population for inverse
  archive budgets.
- Repair scripts retain read-only analysis but reject `--apply`; safe repair is
  routed through the audited rollback executor.
- `scan --resume` now performs a full fresh scan rather than skipping cached IDs.

### Fixed (safe execution)
- Audit `read_before` and `read_after` retain the historical meaning: the value
  records membership in Gmail's `UNREAD` label.
- Apply reports persisted confirmed/failed/uncertain/conflict counts rather than
  claiming every planned mutation succeeded.

### Added
- `has_label` match criterion: rules can screen on labels the message already
  carries, so Gmail's own `CATEGORY_PROMOTIONS` becomes a second axis next to
  the sender domain. A vendor rule can now say "label this a purchase unless
  Gmail already filed it as promotional".
- `exclude` accepts a list of criteria sets as well as a single one; any
  satisfied set vetoes the rule (OR of NOTs). Needed to screen a rule on both
  a label and a subject pattern.
- `scripts/coverage.py`: rule coverage against the live Gmail inbox, listing
  the highest-volume unmatched sender domains.
- `scripts/audit_rules.py`: flags `from_domain` rules whose label disagrees
  with the label history the mailbox had before MailBrain first wrote to it —
  catches domains the >=70% derivation overfitted.
- `scripts/reconcile.py`: removes labels a past run added that the current
  ruleset no longer assigns, so fixing a rule also repairs applied mail.
- `scripts/unlabel.py`: reverse one label from one run, scoped by sender domain.
- `scripts/review.py`: five read-through reports over every cached message —
  archived-but-actionable, vendor labels on personal mail, our label versus the
  tree the mail already sat in, what each subject-only rule reaches, and a
  per-rule sample.
- `scripts/reconcile.py` also repairs archiving: messages a run archived that no
  current rule would archive are put back in the inbox.

### Added (screening)
- `subject_not_contains` match criterion. It is a qualifier, never a criterion
  on its own, which lets one exclude set say "promotional **and** not
  transactional" — the shape the OR-of-NOTs `exclude` list cannot express.
- `tests/test_rules_scenarios.py` runs mailbox scenarios against the shipped
  `config/rules.yaml`, so a rule edit that reintroduces a past misclassification
  fails the suite.

### Fixed
- Gmail's promotional bucket no longer strips the purchase label off a real
  order it misfiled: the promo veto now spares transactional subjects. The same
  carve-out keeps a KLM travel document classified when it lands in Promotions.
- `promo-archive` and `promo-category-archive` no longer sweep transactions
  (orders, invoices, payslips, subscription renewals) — while still sweeping
  adverts that quote those words ("20% korting op je bestelling", "GRATIS
  bezorging"), via a promotional-wording carve-out on the transactional screen.
- Boekenbalie order mail is `Administratie/Aankopen` instead of `Kennis/Boeken`.
- JetBrains renewal and subscription mail is no longer archived.
- `booking.com` mail whose subject only says "job" is recruitment rather than
  falling between the travel and recruitment rules.
- Promotional mail from purchase vendors no longer gets `Administratie/Aankopen`
  (and friends): all 19 purchase rules now exclude `CATEGORY_PROMOTIONS` and
  promotional subject wording.
- Overfitted domain rules corrected: `iodigital.com` and `klm.com` are employer
  domains (`Werk/iOFrontmen`, `Werk/KLM`), not `Administratie/leaseauto` and
  `Reizen`; `nwwi.nl` files under `Albrecht Thaerlaan 57`; `eu.erply.io` under
  Decathlon; `rabobank.nl` splits banking from mortgage and outing promos;
  JetBrains invoices split from its newsletter.
- `booking.com` recruitment mail (applications, assessments) is
  `Werk/recruitment`, not `Reizen` — the domain both sells trips and hires.
- Newsletter archiving is split by sender type: editorial senders archive
  unscreened, since their headlines quote words like "pakket" and "retour",
  while senders that also run a webshop (Boekenbalie, JetBrains) have their
  order and invoice mail screened out of the sweep.
- `promo-category-archive` no longer sweeps a genuine appointment or callback
  that Gmail happened to file as promotional.
- `administratie-korting` no longer labels municipal parking-permit notices,
  which mention "korting" without being a coupon.
- `promo-category-archive` never sweeps travel-operations or account-security
  mail, however Gmail filed it. Airline upsell mail is deliberately worded like
  a flight change ("Update on your upcoming flight"), so the possessive forms
  ("your flight", "je vlucht") are treated as operational while price alerts
  ("vluchten naar ...") are not.

### Added (earlier)
- `exclude` (NOT) block on rules: a rule matches only if its `match` criteria
  hold and its optional `exclude` criteria do not — for vetoing shared tokens
  (e.g. `factuur` but not `Kenteken`/`visum`).
- `scan --resume`: skip ids already cached to resume an interrupted scan
  cheaply; per-batch progress logging (`scan --quiet` to silence).
- `classify`/`apply --verbose`: a "Matched rules" column showing which rule(s)
  fired per message.
- `classify`/`apply --pager`: page the plan table through `$PAGER` (use
  `PAGER='less -R'` for colour).
- Rules regenerated from Gmail label history: 83 `from_domain` rules derived
  from ~21k already-labelled messages, plus a round-2 batch for high-volume
  vendors and a subject-based `Administratie/Korting` coupon rule.

### Changed
- `apply` now writes to Gmail by default (with a confirmation prompt). Use
  `--dry-run` to preview and `--yes`/`-y` to skip the prompt. The old
  `--execute` gate is removed; `classify` remains the pure offline preview.

### Fixed
- `scan` survives a single message that fails to fetch (e.g. Gmail 400
  `failedPrecondition`): the id is logged and skipped instead of aborting.
- `apply` syncs the local cache (`Message.label_ids`) after writing, so a later
  `classify` no longer re-lists changes already applied.
- Rule corrections: `ah.nl` no longer forces `Loonstrook` onto recipe/deal mail
  (payslips matched by subject instead); recruiter `jpeople.co.uk` moved from
  `Kennis/Training` to `Werk/recruitment`.

## [0.1.0]

- Initial local-first Gmail classifier: `init`, `scan`, `classify`, `apply`,
  `rollback`; deterministic rule engine; SQLite cache; Gmail write retry with
  exponential backoff.
