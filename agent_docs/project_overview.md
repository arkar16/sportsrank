# Project Overview

## Purpose and Product Boundary

SportsRank calculates CORS rankings for College Football and publishes a
permanent, static statistical-reference site. CFB/FBS is the active product
scope. Core public content is generated HTML/CSS; JavaScript is progressive
enhancement only, and Firebase Hosting has no application runtime.

## Domain Lifecycle

The prior Season's FINAL initializes a distinct PRESEASON Ranking through the
provisional `1.75` Season Carryover rule. PRESEASON supplies Week 0 spreads.
Week 0 is a normal scored week, and W0 is published only after completed Week 0
games. This lifecycle applies to rebuilt Seasons from 2024 onward.

## Current Architecture

- `cfb/season_source.py`, `season_snapshot.py`, `snapshot_cache.py`, and
  `request_meter.py` form the intended metered CFBD v2 source boundary.
- `cfb/week_calendar.py` owns the explicit source-backed provider Week 1 to
  canonical Week 0 policy; raw provider week, ID, date, and completion fields
  remain in Schema 3 snapshots.
- `cfb/ranking_engine.py` calculates CORS, records, and spreads from normalized
  snapshots; legacy orchestration remains present for compatibility.
- `cfb/release.py` and `cfb/recovery.py` stage, independently validate, and
  promote static artifacts under the explicit phase, full-overlay, cumulative
  graph, original six-call bootstrap, metadata-repair, and delegated repair
  contracts in
  `docs/plans/2026-p0-recovery-and-backfill.md`.
- `website/` is the current Published Site and historical URL base. It must not
  be replaced by a partial candidate.
- `.github/workflows/firebase-hosting-publish.yml` is manual-only and carries
  one content-addressed artifact from full-SHA validation through the protected
  production approval.

## Operational Boundary

Only an explicit fetch/refresh may call CFBD; calculation, rendering,
validation, promotion, and deployment must reuse cached snapshots and spend
zero calls. The original six-call bootstrap and separate three-call metadata
repair are complete; no further provider call is authorized. The key belongs
only in `CFBD_API_KEY`. The clean offline export passes 193 tests, including
portable Schema 2/3 fixtures with no private or ignored cache dependency; CI-
focused checks pass 10/10 and compile, locked-dependency, npm, and diff checks
pass. V5 validates three releases with 116/226/234 checked artifacts and zero
structural failures; the final overlay has 142 added / 93 changed / 0 deleted
paths, real W0 slates of 4/5/8 games, and 35 corrected pick'em rows. History
preserves 127 baseline rows and carries 129 rows in the candidate; 335
inherited findings remain deferred. The actual-key V5 scan has zero matches and
zero read errors across all 13 scopes. The V4 human Gate 1 package is approved;
reviewer final acceptance of the sealed V5 candidate was granted on 2026-09-09,
and that exact candidate is in tracked `website/`. This repair used cached
inputs only and made zero provider/network calls.
Gate 2 production approval remains pending. Pi/T7 provisioning, scheduling,
backups, and notifications remain deferred as recorded in the authoritative
plan.

## Sources of Truth

`CONTEXT.md` owns domain terms, accepted ADRs own durable rationale, active
plans own implementation requirements, and Codex Workflow documents own
current state/evidence. Any contradiction among those sources or executable
interfaces blocks work until reconciled.
