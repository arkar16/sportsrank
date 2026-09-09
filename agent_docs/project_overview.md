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
  `request_meter.py` form the supported metered CFBD v2 source boundary.
- `cfb/week_calendar.py` owns the explicit source-backed provider Week 1 to
  canonical Week 0 policy; raw provider week, ID, date, and completion fields
  remain in Schema 3 snapshots.
- `cfb/ranking_engine.py` calculates CORS, records, and spreads from normalized
  snapshots; legacy orchestration remains present for compatibility.
- `cfb/release.py` and `cfb/recovery.py` stage, independently validate, and
  promote static artifacts under the explicit phase, full-overlay, cumulative
  graph, source-boundary, and delegated-repair contracts in
  `docs/plans/2026-p0-recovery-and-backfill.md`.
- `website/` is the current Published Site and historical URL base. It must not
  be replaced by a partial candidate.
- `.github/workflows/firebase-hosting-publish.yml` is manual-only and carries
  one content-addressed artifact from full-SHA validation through the protected
  production approval.

## Operational Boundary

Only an explicit fetch/refresh may call CFBD; calculation, rendering,
validation, promotion, and deployment reuse cached snapshots. The key belongs
only in `CFBD_API_KEY`, and all source access crosses the accepted meter and
snapshot boundary. The V4 human Gate 1 package is approved, and reviewer final
acceptance of the sealed V5 candidate was granted on 2026-09-09; that exact
candidate is in tracked `website/`. The detailed evidence identities and check
results remain in `agent_docs/latest_session_work.md`. Gate 2 production
approval remains pending, and Pi/T7 provisioning, scheduling, backups, and
notifications remain deferred as recorded in the authoritative plan.

## Sources of Truth

`CONTEXT.md` owns domain terms, accepted ADRs own durable rationale, active
plans own implementation requirements, and Codex Workflow documents own
current state/evidence. Any contradiction among those sources or executable
interfaces blocks work until reconciled.
