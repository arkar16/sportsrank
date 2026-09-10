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
- Schema 3 is immutable migration input. Schema 4 additionally retains provider
  `season_type`/playoff metadata and seals calendar, registry, and migration
  provenance. `cfb/week_calendar.py` owns the regular Week 0 policy and the
  fixed postseason date lattice; `cfb/postseason_registry.py` owns only the
  exact 92-row historical repair. Unsupported or contradictory phase metadata
  fails closed.

## Current Safety Status

The P0 release builder, validator, carryover behavior, ATS grading, calendar
normalization, and exact-artifact workflow are covered by the accepted Gate 1
candidate. The postseason-corrected 11,634-file candidate is in tracked
`website/`; detailed evidence and check results
remain in `agent_docs/latest_session_work.md`. Remote publication uses the
protected manual workflow, and Gate 2 production approval remains pending.

## Domain Constraints

PRESEASON and Week 0 are separate checkpoints. The provisional `1.75`
carryover produces PRESEASON; completed Week 0 games produce W0. CORS model
changes beyond the explicitly approved sequencing and ATS correction require a
separate decision and validation effort.
