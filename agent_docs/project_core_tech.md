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
- The original recovery bootstrap spent exactly six calls; a separate bounded
  metadata repair spent exactly three games-only calls, for nine cumulative
  rows. All subsequent build, validation, promotion, and deployment stages
  spend zero calls. The original bootstrap cache/audit remains unchanged.
- Schema 3 retains raw provider week, provider ID, date, and completion fields.
  `cfb/week_calendar.py` owns the source-backed, season-specific policy that
  maps only early provider Week 1 games to canonical Week 0. Unsupported
  seasons and deficient legacy metadata fail closed.

## Current Safety Status

The P0 release builder, validator, carryover behavior, ATS grading, calendar
normalization, and exact-artifact workflow pass independent review from a clean
export: 193 tests pass, including portable Schema 2/3 fixtures with no private
or ignored cache dependency; CI-focused checks pass 10/10; and compile,
locked-dependency, npm, and diff checks pass. The sealed V5 candidate validates
three releases with 116/226/234 checked artifacts and zero structural failures;
the final overlay has 142 added / 93 changed / 0 deleted paths. The V5
credential scan has zero matches and zero read errors across all 13 scopes. The
V4 human Gate 1 package is approved; V5 reviewer final acceptance was granted
on 2026-09-09 and the exact candidate is in tracked `website/`. This repair used
cached inputs only and made zero provider/network calls.
`npm run deploy` remains outside the approved publication path, and Gate 2
production approval remains pending.

## Domain Constraints

PRESEASON and Week 0 are separate checkpoints. The provisional `1.75`
carryover produces PRESEASON; completed Week 0 games produce W0. CORS model
changes beyond the explicitly approved sequencing and ATS correction require a
separate decision and validation effort.
