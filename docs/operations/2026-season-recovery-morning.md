# 2026 recovery publication runbook

Do not run the old live-fetch, promotion, or Firebase instructions. V3 is
superseded. Metadata repair completed with exactly three explicit metered
GAMES-only requests (2024 and 2025 historical, 2026 scheduled), no retries or
teams calls, and nine cumulative successful audit rows in
`.sportsrank/gate1-recovery-20260908/metadata-refresh-data`. The original
six-call ledger, cache, and audit bytes remain unchanged. Schema 3 now retains
raw provider week, ID, date, and completion fields; the source-backed calendar
policy verifies W0 counts of 4, 5, and 8 for 2024, 2025, and 2026. The V4 human
Gate 1 package is approved. V5 implementation and offline verification are
complete: 193 tests pass, CI-focused tests pass 10/10, and three V5 validations
check 116/226/234 artifacts with zero structural failures. Reviewer final
acceptance of the sealed V5 candidate was granted on 2026-09-09, and the exact
candidate is now in tracked `website/`; Gate 2 production approval remains
pending. Post-acceptance diagnosis found 46 postseason provider Week 1 games in
each of 2024 and 2025 incorrectly classified as canonical Week 1, contaminating
actual W1 outputs and the sequential FINAL path. The cache-only V6 correction is
the frozen comparison baseline: its 218/218 tests and 152/298/306-artifact
validation remain prior evidence. The current V7 window/provider-phase follow-up
passes 225/225 tests in 17.978 seconds and compilation; frozen V6 remains
byte-identical and reconstructed-byte equivalence passes. Reviewer acceptance
is complete and the exact candidate is in tracked `website/`. See
`agent_docs/latest_session_work.md` for current deployment state.

The implementation and test contract is
[`../plans/2026-p0-recovery-and-backfill.md`](../plans/2026-p0-recovery-and-backfill.md).
The actual-key credential scan is recorded in the repair evidence and required
no API call. The three-call metadata-repair allowance is spent; preserve the
original six-call bytes and do not rerun that sequence or issue further CFBD
requests. Continue from the refreshed root with cached-input review and
validation only. The normalization is an explicit SportsRank policy, not a
CFBD conversion: classify each Game's phase before assigning its Week. For
regular-season games, provider Week 1 dates before the midnight
America/New_York boundaries 2024-08-26, 2025-08-25, and 2026-08-31 map to Week 0.
Positively classified postseason games use the same Season lattice with
`canonical_week = 1 + floor((local_date - week1_boundary_date).days / 7)`;
mixed regular/postseason Weeks and gaps are valid, and public Week URLs remain
unchanged. The inclusive 2026 America/New_York postseason window is 2026-12-12
through 2027-01-25; December 12 is the FCS Celebration Bowl and December 15 is
the first FBS bowl. Unsupported seasons, deficient legacy metadata, and newly
provider-backed missing or unknown phase fail closed, with no silent refresh or
automatic fallback after a failed refresh. Fresh provider-backed postseason IDs
do not require membership in the historical 92-row recovery registry, but raw
phase mismatches reject at cache and Release validation. See the supported staged
commands in
[`../../cfb/README.md`](../../cfb/README.md) for the cache-only rebuild path.
`cfb/week_calendar.py` owns the explicit policy; each Release independently
rederives it and binds the policy identity, Season, boundary, timezone, and
primary source URLs in provenance.

The P1 executor must use the cache-only
`python -m cfb.recovery migrate-postseason --source-root <directory containing
cfb-fbs-YEAR.json> --destination-root <new empty snapshots directory>` command,
defaulting to 2024/25/26 with repeatable `--season` selection. The source is
`.sportsrank/gate1-recovery-20260908/metadata-refresh-data/snapshots`, not its
parent audit root; the new roots are
`.sportsrank/postseason-calendar-repair-20260909/{data,releases-v6,evidence}`.
Derived Schema 4 data retains available raw `season_type` and `playoff` and
binds source Schema 3/checksum, target schema, calendar identity, and the
`postseason-recovery-v2` registry checksum
`244b5ed82b7d0add35cae95cf48ce664639f17a9243fbda8acc83e6720467549`. Regular
and postseason IDs are `cfb-provider-week-v1` and
`cfb-postseason-week-lattice-v1`; reverse validation must reproduce the full
Schema 3 checksum. The command makes zero CFBD calls and does not mutate its
inputs. This repair completed from cached inputs with source, tests, and
workflow changes frozen. The V6 candidate remains the frozen comparison
baseline; V7 focused and full verification plus fresh reconstructed-byte
equivalence pass with zero path deltas. No CFBD/build-transport request or
Firebase deployment occurred. V7 reviewer acceptance is complete; Gate 2
production approval remains pending.

## Gate 2 publication path after PR review

After PR review and merge, use only the manually
dispatched **Publish validated static site to Firebase Hosting** workflow. Gate 1
applies to the reviewed V7 candidate, and Gate 2 production approval remains
separate. Enter
the full 40-character `candidate_sha` of the merged candidate and the full
40-character `base_sha` of the currently deployed commit. The workflow rejects
mutable refs and unmerged candidates, validates the candidate site once,
creates a content-addressed artifact plus attestation, and carries that exact
artifact into the protected GitHub `production` environment. The publish job
has no source checkout. Do not invoke local hosting publication commands; Gate
2 is the pending production approval.
