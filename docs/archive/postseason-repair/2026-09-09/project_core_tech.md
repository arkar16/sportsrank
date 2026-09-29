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
- Schema 3 retains raw provider week, provider ID, date, and completion fields
  and is immutable migration input. The cache-only `migrate-postseason` CLI
  derives Schema 4 snapshots that retain available `season_type` and `playoff`
  fields, with source schema/checksum, target schema, calendar identity, and
  registry provenance.
  `cfb/week_calendar.py` owns regular `cfb-provider-week-v1`; postseason uses
  `cfb-postseason-week-lattice-v1` and the pinned `postseason-recovery-v2`
  registry checksum
  `244b5ed82b7d0add35cae95cf48ce664639f17a9243fbda8acc83e6720467549`.
  The fixed recovery windows are 2024-12-14 through 2025-01-20 and 2025-12-13
  through 2026-01-19, inclusive. The inclusive 2026 America/New_York window is
  2026-12-12 through 2027-01-25; December 12 is the FCS Celebration Bowl and
  December 15 is the first FBS bowl. Provider-backed missing or unknown phase
  and deficient legacy metadata fail closed, and reverse validation must
  reproduce the full Schema 3 checksum. Fresh provider-backed postseason IDs do
  not require historical registry membership, but raw phase mismatches reject at
  cache and Release validation.

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
production approval remains pending. A P1 diagnosis found 46 postseason
provider Week 1 games in each of 2024 and 2025 incorrectly classified as
canonical Week 1. The Schema 4 migration and sequential V6 rebuild remain the
frozen baseline. The current V7 window/provider-phase follow-up passes 225/225
tests and compilation; all three direct/immediate reconstructions have zero
failures and zero path deltas with exact bytes matching V6. Reviewer acceptance
remains pending.

## Domain Constraints

PRESEASON and Week 0 are separate checkpoints. The provisional `1.75`
carryover produces PRESEASON; completed Week 0 games produce W0. CORS model
changes beyond the explicitly approved sequencing and ATS correction require a
separate decision and validation effort.
