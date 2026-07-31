# Changelog

All notable changes to MailBrain are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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
