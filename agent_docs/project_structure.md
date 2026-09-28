# Project Structure

## Primary Boundaries

```text
cfb/                    Supported CFB/FBS runtime, recovery, and legacy compatibility modules
website/                Firebase-hosted static site and permanent public paths
tests/                  Offline behavioral and release-contract verification
docs/adr/               Durable architecture decisions
docs/plans/             Active and superseded implementation contracts
docs/operations/        Operator procedures and runbooks
agent_docs/              Current Codex Workflow state and evidence
legacy/nfl/             Unsupported historical NFL experiments
tools/scripts/          Checked-in publication and post-deploy helpers
mainpage.py             Root path-sensitive legacy helper
webconfig.py            Root path-sensitive legacy helper
```

Ignored local state belongs under `.sportsrank/`; generated candidate Releases
must not modify `website/` before an explicit promotion. The accepted
postseason-corrected candidate is in tracked `website/`; Gate 2 still protects remote Firebase
publication.

## Active Interfaces

- `SeasonSource`, `SeasonSnapshotService`, and `RequestMeter` are the intended
  single CFBD v2 boundary.
- `week_calendar.py` owns source-backed provider-week normalization, retaining
  raw provider metadata and failing closed for unsupported seasons or deficient
  legacy metadata.
- `postseason_registry.py` owns the pinned 92-row historical correction
  registry; fresh provider IDs rely on explicit provider phase instead.
- `ranking_engine.py` owns FBS CORS semantics and Season Carryover.
- `release.py` owns static artifact construction and validation as mandatory
  full-site overlays with independently derived artifact graphs.
- `recovery.py` exposes explicit PRESEASON/week/FINAL phases and staged
  backfill/recovery commands, including cache-only Schema 3→4 postseason
  migration; cached build, validation, and promotion keep source access separate.
- `firebase.json` points Hosting at `website/`; the manual GitHub workflow
  transports one exact validated artifact through the protected production
  gate.

## Ownership

The main agent owns cross-boundary integration and acceptance gates. Exact
workflow evidence and continuation state belong in
`agent_docs/latest_session_work.md`; this document records the repository
boundaries and ownership map.

## Verification State

The Gate 1 postseason-corrected candidate is reviewer-accepted and promoted into tracked
`website/`. Exact identities, verification results, and preserved historical
evidence belong in `agent_docs/latest_session_work.md`. Gate 2 production
approval remains pending, so no Firebase publication is authorized.
