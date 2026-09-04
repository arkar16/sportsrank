# 2026 season recovery plan

Status: local implementation is review-ready on `feature/season-2026-recovery`;
live recovery and publication remain credential- and infrastructure-gated.

## Implementation checkpoint

- Offline implementation verification: 59 tests passed, including 23 unittest
  subtests; Python compilation, lock verification, hosting-tree verification,
  and diff whitespace checks passed.
- An independently verified fixture Release validates with 21 checked
  artifacts and no current or inherited failures.
- No live CFBD request, remote push, Firebase deployment, Raspberry Pi change,
  T7 change, or Git history rewrite was performed.
- The exact credential-gated recovery and publication procedure is recorded in
  `docs/operations/2026-season-recovery-morning.md`.

## Goal

Publish a validated 2026 FBS Week 0 site locally, then establish fully automated Monday Ranking Runs on the Raspberry Pi without changing the CORS model or breaking historical URLs.

## Immediate constraints

- The baseline is commit `0f40dbd3`, tagged `baseline/static-cfb-v2-2026-09-03` locally.
- The remote `main` still contains the abandoned client-rendering migration.
- A push to `main` currently invokes stale GitHub Actions that can attempt a live Firebase deployment.
- The latest complete final ranking is 2023; 2024 stops at Week 9, 2025 is an empty shell, and 2026 has no site tree.
- Season Carryover requires the prior final ranking, so catch-up order is 2024 final → 2025 final → 2026 Week 0.
- The CFBD allowance is 3,000 requests per month; the new key must remain outside Git and logs.
- Every candidate Release must validate locally before the Pi or Firebase is involved.

## P0 — season-opening recovery

- [x] Replace the temporary source-text rollback guards with behavior-focused acceptance tests where practical.
- [x] Add recorded CFBD v2 fixtures and a fake adapter so normal tests make zero live requests.
- [x] Define and enforce CFBD request-count, cache, retry, and monthly-budget contracts.
- [x] Upgrade CFBD and the remaining supported project dependencies behind compatibility tests and a regenerated lockfile.
- [x] Route every CFBD request through one Request Meter that records endpoint, purpose, cache decision, result, and budget impact without recording credentials.
- [x] Enforce initial monthly budgets of 100 scheduled calls, 500 historical-maintenance calls, and an absolute stop at 2,500 total calls.
- [x] Fetch teams once at Season initialization and reuse the result throughout that Season.
- [x] Make API and pipeline failures visible through a concise run summary plus structured stage and cause details.
- [x] Make the command exit nonzero on every unexpected or validation failure.
- [x] Remove calendar guesses, require an explicit target season, and derive target week and season end from the CFBD Season Snapshot.
- [x] Build a Season Snapshot once per run and share it across records, results, slates, rankings, and spreads.
- [x] Generate every candidate Release into a staging directory without changing the Published Site.
- [x] Validate determinism, team/rank completeness, finite values, record reconciliation, completed-game boundaries, spread integrity, required metadata, valid HTML, and internal links.
- [x] Disable automatic live Firebase deployment until this validation gate is wired into publication.
- [ ] Run one read-only local CFBD v2 smoke test after `CFBD_API_KEY` is set locally.
- [ ] Recalculate and validate the missing 2024 weeks and 2024 final ranking.
- [ ] Recalculate and validate the complete 2025 season and final ranking from the 2024 final.
- [ ] Generate and validate 2026 Week 0 from the 2025 final.
- [x] Review the full local diff and fixture-generated site before any remote push or Firebase publication. Production catch-up output remains a morning-review gate.

## P1 — automated Monday publication

- [x] Publish a validated Release atomically while retaining the last known-good site on failure.
- [x] Upgrade permanent pages at their canonical URLs and retain previous revisions in immutable Release archives.
- [ ] Provision narrow CFBD and Firebase credentials on the Pi outside the repository.
- [ ] Install a persistent `systemd` timer for Monday 06:00 Eastern with automated 07:00 and 09:00 retries.
- [x] Avoid redeployment when validated content is unchanged.
- [x] Record release metadata: sport, classification, season, week, model version, code revision, source snapshot, timestamps, and checksums.
- [ ] Store the database and Release archives on the attached Samsung T7.
- [ ] Add automated off-device backups; Google Drive through the Pi/OpenClaw environment is a candidate, not yet selected.
- [ ] Select a final failure-notification channel.

## P1 — repository modernization after first publication

- [x] Introduce shared release, validation, and publication boundaries plus a dedicated CFB sport boundary.
- [ ] Make delegated-agent ownership align with one sport boundary or one shared infrastructure boundary.
- [ ] Introduce SQLite migrations and repositories without making the public site database-backed.
- [ ] Move generated HTML out of Git only after permanent storage, restore, deployment, and URL-compatibility drills pass.
- [x] Preserve the current CORS model while moving code; make model changes separately and explicitly.

## P2 — security and future classifications

- [ ] Rewrite public Git history to remove historical bytecode, caches, logs, and credential-bearing artifacts after 2026 publication is stable.
- [ ] Design and validate the Comparable Scale mechanism before implementing FCS, Division II, or Division III rankings.

## Accepted P0 invariants

- Identical inputs and model version produce identical rankings.
- Every expected FBS team appears exactly once and ranks are unique and contiguous.
- Required CORS, record, and spread values are finite and present.
- Ranking order and tie-break behavior follow the documented model contract.
- Records reconcile with completed games through the target week; incomplete and future games do not contribute.
- Every spread references valid teams and contains finite values.
- Required ranking, spread, history, navigation, and metadata artifacts exist.
- Every internal link resolves and every public page has a last-update timestamp.
- Any failed invariant prevents publication and returns a nonzero process status.

## Open decisions

- Exact manually verified ranking fixtures and numerical tolerances.
- SQLite schema, migration policy, backup format, and restore drill.
- Correction revision metadata and immutable Release archive format.
- Failure-notification channel.
- Cross-Classification CORS calibration.
- Longer-term notification channel and whether usage reconciliation should later be automated.
