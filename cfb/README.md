# CFB recovery and rankings

The CFB boundary currently supports FBS data from CFBD and renders static
HTML. The supported recovery entry point is `python -m cfb.recovery`; it keeps
external fetching, cached snapshots, Release generation, validation, and local
promotion as separate stages. V3 is superseded. The V4 human Gate 1 package is
approved. The repaired V5 implementation and offline verification are complete:
193 tests pass, CI-focused tests pass 10/10, and three V5 release validations
check 116/226/234 artifacts with zero structural failures. The final overlay has
142 added / 93 changed / 0 deleted paths; W0 slates are 4/5/8; and all 35
affected pick'em rows are corrected. Reviewer final acceptance of the sealed V5
candidate was granted on 2026-09-09; the candidate is promoted into tracked
`website/`, with 335 inherited legacy findings deferred.
The original six-call ledger and prior nine-row audit remain unchanged; this
repair deployment made zero provider/network calls. The actual-key scan found
zero matches and zero read errors across all 13 scopes. Isolated temporary
promotion matched the candidate, and tracked promotion is byte-identical. The
live Firebase site is unchanged; Gate 2 production approval remains pending. See
`agent_docs/latest_session_work.md`
for current deployment state.

A P1 postseason-calendar diagnosis found 46 postseason provider Week 1 games in
each of 2024 and 2025 incorrectly classified as canonical Week 1. The frozen V6
baseline completed with 218/218 tests and 152/298/306 checked artifacts with
zero current failures. The current V7 follow-up passes 225/225 tests in 17.978
seconds and compilation. Fresh reconstruction confirms all three direct and
immediate releases with zero failures and zero added/changed/deleted paths; exact
site and release JSON bytes match frozen V6, so no candidate regeneration was
needed. V7 reviewer acceptance and Gate 2 production approval remain pending.

## Local setup

From the repository root:

```sh
uv sync --locked
export SPORTSRANK_DATA_DIR="$PWD/.sportsrank"
```

Set `CFBD_API_KEY` only in the local terminal that performs a fetch. The
adapter reads it as a bearer token and the request meter stores credential-free
audit rows in `$SPORTSRANK_DATA_DIR/cfbd_requests.sqlite3`.

## Supported commands

```sh
uv run python -m cfb.recovery --help
uv run python -m cfb.recovery prime 2026 --category scheduled
uv run python -m cfb.recovery fetch YEAR --classification FBS
uv run python -m cfb.recovery refresh YEAR --classification FBS
uv run python -m cfb.recovery build YEAR --classification FBS \
  --phase preseason --release-id RELEASE_ID \
  --published-site website --output-root "$PWD/.sportsrank/releases"
uv run python -m cfb.recovery validate CANDIDATE_PATH \
  --published-site "$PWD/website" --json
# Future reviewed candidates only; V5 is already promoted.
uv run python -m cfb.recovery promote CANDIDATE_PATH website
```

The original approved P0 sequence is recorded below as a contract only. It has
already run and must not be rerun:

```sh
uv run python -m cfb.recovery backfill \
  --published-site "$PWD/website" \
  --output-root "$PWD/.sportsrank/releases"
```

`backfill` primes 2026 teams, fetches historical 2024 and 2025 teams/games,
fetches only 2026 games using the cached teams, and requires the shared audit
ledger to contain exactly those six successful calls. That six-call ledger and
its cache/audit bytes are preserved evidence; do not rerun it for the current
metadata repair. Its output is superseded by the metadata-repair continuation;
do not run it again.

The metadata-repair allowance is spent. It used a new task data root seeded from
the original ledger and cache, reused all cached teams, and made exactly these
three explicit metered GAMES-only requests. The commands are shown for audit
identity only; do not rerun them:

```sh
uv run python -m cfb.recovery refresh 2024 --classification FBS \
  --category historical \
  --cache-dir "$PWD/.sportsrank/gate1-recovery-20260908/metadata-refresh-data"
uv run python -m cfb.recovery refresh 2025 --classification FBS \
  --category historical \
  --cache-dir "$PWD/.sportsrank/gate1-recovery-20260908/metadata-refresh-data"
uv run python -m cfb.recovery refresh 2026 --classification FBS \
  --category scheduled \
  --cache-dir "$PWD/.sportsrank/gate1-recovery-20260908/metadata-refresh-data"
```

The refreshed root records nine cumulative successful rows. Preserve the
original six-call bytes and issue no further CFBD requests. Authentication,
schema, quota, transport, or unexpected call-count failures stop the operation;
there is no automatic retry, silent refresh, or automatic fallback. Continue
with cache-only rebuild, verification, validation, and promotion preparation.

Week normalization is an explicit SportsRank policy, not a CFBD-provided
conversion. Classify each Game's phase before assigning its Week. Regular
provider Week 1 games before the midnight America/New_York boundaries
2024-08-26, 2025-08-25, and 2026-08-31 map to Week 0; later regular provider
weeks remain unchanged. Positively classified postseason games use
`canonical_week = 1 + floor((local_date - week1_boundary_date).days / 7)` on
the same Season lattice. Mixed regular/postseason Weeks and gaps are valid;
public Week URLs do not change, and there is no separate W17 lattice. The
regular and postseason policy IDs are `cfb-provider-week-v1` and
`cfb-postseason-week-lattice-v1`. The inclusive 2026 America/New_York postseason
window is 2026-12-12 through 2027-01-25; December 12 is the FCS Celebration Bowl
and December 15 is the first FBS bowl. Schema 3 remains the immutable source;
Schema 4 carries raw provider week, ID, date, completion, `season_type`, and
`playoff` fields plus migration provenance. Newly provider-backed missing or
unknown phase, unsupported seasons, and deficient legacy metadata fail closed.
Fresh provider-backed postseason IDs do not require membership in the historical
92-row recovery registry, but raw provider phase mismatches fail at cache and
Release validation. Historical recovery rows continue to use
`phase_source=recovery_registry`.
Each Release independently rederives the mapping and binds its policy identity,
Season, boundary, timezone, primary source URLs, registry version, and checksum.

The historical V5 candidate was built from the refreshed root and independently
validated before its publication step. V6 remains the frozen comparison
candidate, and V7 reuses it after verified byte equivalence. Real W0 counts are
4, 5, and 8 for 2024, 2025, and 2026. Ordinary offline verification uses
portable Schema 2/3 fixtures and does not depend on private or ignored caches.

`build` requires an explicit `--phase` of `preseason`, `week`, or `final`.
Numbered weeks also require `--through-week`; there is no ambiguous weekly
default or clone flag.

`smoke`/`prime` performs one metered teams request when teams are missing and
persists them in the normal snapshot cache; a repeated prime is a zero-call
cache hit. A first `fetch` normally uses one teams request and one games
request; cached `build`, `validate`, and `promote` operations do not call CFBD.
`refresh` reuses cached teams and refreshes games only. A failed request is
recorded without exposing the API key.

The deployment's fetch/refresh allowance is spent; use the refreshed cache for
remaining work and do not issue another provider request.

## Postseason calendar migration

The P1 repair uses the cache-only migration entry point below. `--source-root`
must be the directory containing `cfb-fbs-YEAR.json`, not the parent audit root;
`--destination-root` must be new and empty:

```sh
uv run python -m cfb.recovery migrate-postseason \
  --source-root "$PWD/.sportsrank/gate1-recovery-20260908/metadata-refresh-data/snapshots" \
  --destination-root "$PWD/.sportsrank/postseason-calendar-repair-20260909/data"
```

It defaults to seasons 2024, 2025, and 2026; repeat `--season` for a subset.
The command makes zero CFBD calls and leaves the Schema 3 inputs unchanged. It
writes derived Schema 4 snapshots with raw provider `season_type` and
`playoff` when available, plus source schema/checksum, target schema, calendar,
and registry provenance. Regular data uses `cfb-provider-week-v1`; postseason
data uses `cfb-postseason-week-lattice-v1` and the pinned `postseason-recovery-v2`
registry checksum `244b5ed82b7d0add35cae95cf48ce664639f17a9243fbda8acc83e6720467549`.
The 92 recovery rows use `phase_source=recovery_registry`; missing or stripped
origin provenance fails closed, and their provider phase metadata remains null.
The P1 roots are separate:
`.sportsrank/postseason-calendar-repair-20260909/{data,releases-v6,evidence}`.
Deep validation reverses the migration to the full Schema 3 checksum. The V6
chain remains the frozen baseline; V7 focused and full verification plus fresh
reconstructed-byte equivalence pass. Reviewer acceptance remains pending.

The seven delegated repairs are implemented and verified in the historical V5
candidate. The V6 calendar correction remains the frozen comparison baseline;
the V7 window/provider-phase correction is verified through 225/225 tests and
exact reconstructed-byte equivalence. Reviewer acceptance remains pending.

For a future candidate, `build` writes only to its staging output. Review its
manifest and run `validate` before the explicit `promote` step. Promotion is
local and should be followed by committing the reviewed `website/` tree, then
using the protected, manual **Publish validated static site to Firebase Hosting**
workflow only after production approval.
The isolated temporary promotion and byte comparison are complete for V5, and
the exact V5 candidate is now in tracked `website/`. The V6 candidate also
passed isolated public-CLI promotion rehearsal and post-validation; it remains
separate from tracked `website/` pending reviewer acceptance. Do not rebuild it
or issue provider requests. Remote Firebase publication still requires PR merge
and the separate Gate 2 production approval.
For a cumulative final candidate, `validate --published-site` must name the
original Published Site, not an intermediate Release. This invokes the
independent cumulative-chain validator and reports added, changed, and deleted
public paths in JSON output before any explicit promotion.
The workflow must be dispatched with the full 40-character `candidate_sha` of
the merged candidate and the full 40-character `base_sha` of the currently
deployed commit. It checks ancestry and merge status, validates `website/`
once, packages and attests one content-addressed artifact, and deploys that
exact artifact after the protected `production` approval; the publish job does
not check out a ref. A Pi-local `.sportsrank/releases` path is never passed to
a runner.

Local Firebase commands are for the emulator only. Public publication has no
local npm or Firebase deployment shortcut; use the gated workflow above.

The older `cfb/main.py` script and batch file are retained for historical
compatibility; new recovery work should use the staged interface above.
