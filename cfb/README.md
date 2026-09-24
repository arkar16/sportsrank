# CFB recovery and rankings

The CFB boundary currently supports FBS data from CFBD and renders static
HTML. The supported recovery entry point is `python -m cfb.recovery`; it keeps
external fetching, cached snapshots, Release generation, validation, and local
promotion as separate stages. See SportsRank task SR-7 (`bb tasks show SR-7`)
for the recorded approval boundary and [historical Gate 1 reports](../agent_docs/latest_session_work.md)
for historical verification. Commands below describe interfaces; fetch and
publication examples do not grant authorization to execute them.

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
# Future reviewed candidates only; consult current work before promotion.
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
Deep validation reverses the migration to the full Schema 3 checksum.
V5/V6/V7 acceptance and test counts are historical comparison evidence. The
current delivery reconstructs the same accepted ranking behavior from the
retained inputs and binds the complete overlay to the verified Firebase
baseline; SR-7 and SR-16 own current acceptance evidence.

For a future candidate, `build` writes only to its staging output. Review its
manifest and run `validate` before the explicit `promote` step. Promotion is
local and should be followed by committing the reviewed `website/` tree, then
using the protected, manual **Publish validated static site to Firebase Hosting**
workflow only after production approval.
The original prepared website is retained as private comparison evidence.
Current provider-bound reconstruction uses the privately retained, hash-pinned
source-input bundle with zero live CFBD calls. Local promotion follows
independent acceptance; remote publication still requires owner merge and
separate Gate 2 approval. The private storage provider and replacement
transport are not yet selected.
For a cumulative final candidate, `validate --published-site` must name the
original Published Site, not an intermediate Release. This invokes the
independent cumulative-chain validator and reports added, changed, and deleted
public paths in JSON output before any explicit promotion.
The protected publication workflow on `main` offers `prepare`, `seal-only`,
`execute`, `reconcile`, and `verify-only`. Preparation validates the provider-
backed baseline and retained inputs, then binds the exact site/configuration
package to the actual merged commit. `seal-only` retains the exact package and
intent and emits canonical `SealedAttemptReference` JSON. A fresh separately
owner-approved `execute` consumes that reference once. `normal`, `rollback`,
and `correction` are purposes of the sealed attempt, not one-shot CLI routes.

`reconcile` and `verify-only` use the same durable reference and make zero
provider writes. They remain usable when the execution runner or Actions state
upload was lost. Existing execution claims forbid replay; reconcile first and
seal a new attempt for any further write. Normal successors and recovery
purposes require a freshly verified predecessor. Only the explicitly unknown-
historical initial baseline may omit predecessor evidence. CLI-only
`reconcile-external` requires a complete fresh live capture and sanitized
retained evidence; archived history alone cannot establish live origin.

Use `uv run --locked python -m cfb.publication_cli --help` and each operation's
`--help` for the canonical grammar. The [publication runbook](../docs/operations/2026-season-recovery-morning.md)
owns exact transport and recovery instructions. Its authenticated bootstrap
verifies the actual GitHub commit/tree, candidate bundle and package/record
attestation before extracting runtime source, installing dependencies or using
the gated secret. The protected runtime never checks out a mutable source ref
or rebuilds the package. Committed `config/sr7-recovery-inputs.json` records
trusted baseline and source-input identities independently of downloaded
metadata; it does not authorize public raw-input transport. The current
Actions-artifact input path is incompatible with the revised private-retention
policy and pending remediation.

Public evidence is limited to intended generated static output and safe
hashes/provenance. Raw `baseline.tar.gz`, raw source snapshots,
`source-inputs.tar.gz`, the original-prepared archive containing them, and
provider actor/auth metadata remain private. Candidate Git bundles/history are
subject to the same boundary when they contain raw inputs. The allowlisted
`baseline-public.tar.gz` and `baseline-sanitizer.json` require the private-input
proof gate before public retention; the current transport does not satisfy that
gate. Concrete CLI adapters may make live provider/archive/approval requests;
ordinary tests use offline fakes.
`GITHUB_TOKEN` supplies authenticated provenance and approval reads,
`FIREBASE_ACCESS_TOKEN` is confined to the protected production job, and
`CFBD_API_KEY` is absent. A Pi-local path is never passed to a runner.

[ADR-0016](../docs/adr/0016-bind-publication-to-verified-live-content.md)
separates live identity from verification; [ADR-0018](../docs/adr/0018-retain-publication-evidence-in-github-releases.md)
records durable evidence and two-dispatch recovery. Setup and final acceptance
remain separately tracked in SR-15 and SR-16. Local Firebase commands are for
the emulator only; use the gated workflow for publication.

The older `cfb/main.py` script and batch file are retained for historical
compatibility; new recovery work should use the staged interface above.
