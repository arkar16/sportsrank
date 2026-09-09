# Project Core Technologies

## Runtime and Hosting

- Python `>=3.12` with `uv.lock` drives generation and offline validation.
- Static HTML/CSS is the public product; optional JavaScript may progressively
  enhance it.
- Firebase Hosting serves `website/`; no Python, Node, or database runtime is
  deployed publicly.
- SQLite currently stores only the local request audit. A broader build-time
  SQLite architecture is accepted but deferred.

## External Source and Secret

- CFBD v2 is accessed through the project-only bearer token read from
  `CFBD_API_KEY`.
- Production requests must cross the Request Meter and Season Snapshot seam.
- Limits remain 100 scheduled calls, 500 historical-maintenance calls, and a
  2,500 application stop within the 3,000-call provider allowance.
- Schema 3 retains raw provider week, provider ID, date, and completion fields.
  `cfb/week_calendar.py` owns the source-backed, season-specific policy that
  maps only early provider Week 1 games to canonical Week 0. Unsupported
  seasons and deficient legacy metadata fail closed.

## Current Safety Status

The P0 release builder, validator, carryover behavior, ATS grading, calendar
normalization, and exact-artifact workflow are covered by the accepted V5
recovery candidate. Reviewer final acceptance was granted on 2026-09-09 and the
exact candidate is in tracked `website/`; detailed evidence and check results
remain in `agent_docs/latest_session_work.md`. Remote publication uses the
protected manual workflow, and Gate 2 production approval remains pending.

## Domain Constraints

PRESEASON and Week 0 are separate checkpoints. The provisional `1.75`
carryover produces PRESEASON; completed Week 0 games produce W0. CORS model
changes beyond the explicitly approved sequencing and ATS correction require a
separate decision and validation effort.
