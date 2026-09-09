# Project Structure

## Primary Boundaries

```text
cfb/                    CFB ingestion, snapshot, calendar, ranking, release, and legacy code
website/                Firebase-hosted static site and permanent public paths
tests/                  Offline behavioral and release-contract verification
docs/adr/               Durable architecture decisions
docs/plans/             Active and superseded implementation contracts
docs/operations/        Operator procedures; legacy recovery runbook is paused
agent_docs/              Current Codex Workflow state and evidence
frontend/               Optional progressive-enhancement source
nfl/                    Unintegrated legacy helpers, outside current scope
```

Ignored local state belongs under `.sportsrank/`; generated candidate Releases
must not modify `website/` before an explicit promotion. The seven delegated
repairs are implemented and frozen in V5; isolated temporary promotion matched
the candidate, reviewer final acceptance was granted on 2026-09-09, and the
exact candidate is promoted into tracked `website/`. No Firebase deploy occurred.

## Active Interfaces

- `SeasonSource`, `SeasonSnapshotService`, and `RequestMeter` are the intended
  single CFBD v2 boundary.
- `week_calendar.py` owns source-backed provider-week normalization, retaining
  raw provider metadata and failing closed for unsupported seasons or deficient
  legacy metadata.
- `ranking_engine.py` owns FBS CORS semantics and Season Carryover.
- `release.py` owns static artifact construction and validation as mandatory
  full-site overlays with independently derived artifact graphs.
- `recovery.py` exposes explicit PRESEASON/week/FINAL phases and the original
  six-call staged backfill; the completed metadata repair adds three games-only
  calls in a separate root, and the V5 candidate validates 116/226/234 artifacts
  with zero structural failures and 142 added / 93 changed / 0 deleted paths.
- `firebase.json` points Hosting at `website/`; the manual GitHub workflow
  transports one exact validated artifact through the protected production
  gate.

## Ownership During the P0 Deployment

Heavy-route work is bounded into CFB ranking semantics, shared release
integrity, shared CI/publication, and independent testing. Cross-boundary
changes are coordinated by the main agent; workers preserve one another's
edits and escalate contradictory requirements immediately.

## Verification State

The clean export passes 193 tests with portable Schema 2/3 fixtures and no
private or ignored cache dependency; CI-focused checks pass 10/10. The original
exact six-call ledger/cache evidence and prior nine-row audit are preserved;
this repair made zero provider/network calls. The V5 credential scan has zero
matches and zero read errors across all 13 scopes. The V4 human Gate 1 package
is approved, and the seven delegated repairs are implemented, verified, and
reviewer-accepted in V5. Gate 2 production approval is pending, so no Firebase
publication is authorized.
