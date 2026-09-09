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
conversion. Only provider Week 1 games before the midnight America/New_York
boundaries 2024-08-26, 2025-08-25, and 2026-08-31 map to Week 0; later provider
weeks remain unchanged. Unsupported seasons and deficient legacy metadata fail
closed. Schema 3 retains raw provider week, ID, date, and completion fields;
`cfb/week_calendar.py` owns the explicit policy. Each Release binds the
`week_calendar` policy identity, Season, boundary, timezone, and primary source
URLs while independently rederiving the mapping.

The V5 candidate is built from the refreshed root and independently validated
before any publication step. Real W0 counts are 4, 5, and 8 for 2024, 2025, and
2026. Ordinary offline verification uses portable Schema 2/3 fixtures and does
not depend on private or ignored caches.

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

The seven delegated repairs are implemented and verified. Reviewer final
acceptance of V5 is complete; V4 remains the historical human-review evidence
baseline.

`build` writes only to its staging output. Review its manifest and run
`validate` before the explicit `promote` step. Promotion is local and should
be followed by committing the reviewed `website/` tree, then using the
protected, manual **Publish validated static site to Firebase Hosting**
workflow only after production approval.
The isolated temporary promotion and byte comparison are complete for V5, and
the exact candidate is now in tracked `website/`. Do not rebuild it or issue
provider requests. Remote Firebase publication still requires PR merge and the
separate Gate 2 production approval.
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
