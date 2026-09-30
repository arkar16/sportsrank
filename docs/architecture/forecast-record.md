# Forecast record and evaluation boundary

ADR-0019 measures forecasts that were actually issued. This module therefore
separates forecast content, public-release evidence, game timing evidence and
final-score revisions. A candidate is never an issued forecast by itself.

## Natural matchup interface

`ranking_engine.natural_matchup()` accepts finite CORS ratings with at most two
decimal places and returns a `NaturalMatchup`:

```python
matchup = natural_matchup("20.24", "20.00", neutral_site=False)
assert matchup.home_margin == Decimal("2.24")
assert matchup.home_handicap == Decimal("-2.24")
assert matchup.predicted_winner == "home"
```

The scoring margin is positive when the home team is predicted to win. The
displayed home handicap retains the existing opposite sign. Home-field
advantage remains 2.00, is omitted at neutral sites and is not tuned here.
Exactly zero is Pick'em. Invalid, missing, nonfinite or overprecise ratings fail
instead of becoming zero.

`spreads_for_week()` uses the natural margin by default and exposes
`home_margin`, `home_handicap`, `predicted_winner` and `precision`. Its explicit
`legacy_half_point=True` mode reproduces the old half-point `spread_value` for
validation of already-issued archives. It does not rewrite those archives.

## Candidate identity and provenance

`ForecastCandidate.create()` records a stable provider Game identity, season,
week, participants and classifications, neutral-site status, declared numerical
precision, selection and the two margin orientations. Its provenance pins both
pregame ratings and ranks, rating checkpoint and cutoff, immutable rating and
source-snapshot digests, model version, source kind, code revision and the
home-field value actually used.

The `version_id` is the SHA-256 digest of canonical candidate content. Candidate
JSON round trips through strict `to_dict()` and `from_dict()` methods; unknown
fields and a version/content mismatch fail. Optional `VersionBinding` entries
let a consumer pin another safe versioned artifact, including a future ADR-0021
scoring version, without putting excitement policy in this module.

An explicit pregame correction names its exact `predecessor_version_id` and a
reason. Both versions remain in the ledger. An unchanged re-publication remains
the same content-addressed forecast. Multiple unrelated originals or sibling
replacements are rejected rather than resolved by an arbitrary timestamp.

The public wire format contains derived participant identities, not provider Game
records. Internal domain attributes remain `home_team` and `away_team`; the
strict public identity uses `home`/`away` name and classification objects. A
representative candidate has this shape (digest values abbreviated):

```json
{
  "schema_version": "forecast-candidate/v1",
  "version_id": "sha256:<candidate-content>",
  "game": {
    "provider_id": "401752001",
    "season": 2026,
    "week": 1,
    "home": {"name": "Home", "classification": "fbs"},
    "away": {"name": "Away", "classification": "fbs"},
    "neutral_site": false
  },
  "forecast": {
    "home_margin": "2.24",
    "home_handicap": "-2.24",
    "precision": 2,
    "selection": "home"
  },
  "provenance": {
    "rating_checkpoint": "PRESEASON",
    "rating_cutoff": "before-week-0",
    "rating_artifact_digest": "sha256:<ratings>",
    "source_snapshot_digest": "sha256:<snapshot>",
    "model_version": "v0.4.0",
    "source_kind": "season-snapshot",
    "code_revision": "<git-revision>",
    "home_rating": "20.24",
    "away_rating": "20.00",
    "home_field_advantage": "2.00",
    "home_rank": 1,
    "away_rank": 2
  },
  "bindings": []
}
```

## Publication and temporal evidence

`PublicationReceipt` binds the exact candidate version and canonical candidate
artifact digest to a publication artifact, attempt and verification. It carries
exactly one supported public-time fact: an authenticated provider
`provider_published_at`, or the time an exact digest was `verified_public_by`.
Every fact has an `EvidenceRef` with its kind, retrievable reference and digest.

`GameTimingEvidence` is a separate record and carries exactly one of:

- an authoritative `actual_started_at`; or
- an authoritative observation that the game had `observed_not_started_at`.

A scheduled kickoff is deliberately absent. Release build time, a directory
name, workflow success, later reconciliation and an unpinned timestamp are also
not qualifying facts. A public-time fact qualifies when it is earlier than the
actual start, or no later than an authoritative not-started observation. When
that order cannot be established, selection returns no Graded Forecast and the
Release records an `unresolved_temporal_order` omission.

`select_graded_forecast(candidates, receipts, timing)` verifies exact candidate
bytes, follows the single explicit replacement chain and returns its last
qualifying member. A late or unverified replacement cannot displace an earlier
qualifying forecast. Postgame model changes have no path into the selection.

## Final scores, corrections and grading

`FinalScore` contains a contiguous chain of content-addressed `ScoreRevision`
records. A correction names its predecessor, evidence source and UTC observation
time. Grading always uses the latest score revision while retaining the selected
forecast version and its issuance evidence.

`grade_forecast()` calculates actual and predicted margins in home-team
orientation. Straight-up grades the known selected side. Coverage compares the
actual margin in the selected side's orientation with the absolute forecast:
greater is Cover, smaller is No cover and equality is Push. Pick'em and legacy
zero with unknown original selection remain ungraded for winner and coverage;
both retain margin error. A final tie supplies margin error and a straight-up
tie status rather than a winner.

The accepted independent examples are:

| Game | Predicted home margin | Actual home margin | Straight-up | Coverage | Absolute error |
| --- | ---: | ---: | --- | --- | ---: |
| A | 2.24 | 2 | Correct | No cover | 0.24 |
| B | -2 | -2 | Correct | Push | 0 |
| C | -2 | 3 | Incorrect | No cover | 5 |
| D | 0, confirmed Pick'em | -7 | Ungraded | Ungraded | 7 |
| E | 0.02 | 1 | Correct | Cover | 0.98 |

Across the five games, MAE is `13.22 / 5 = 2.644`; RMSE is
`sqrt(75.018 / 5) = 3.873448...`. Straight-up is 3-1 over four selections.
Coverage is one Cover, two No cover and one Push; its percentage is `1 / 3`
because pushes and unselected games are outside that denominator. Season
aggregation operates on games, not weekly averages, and rejects a duplicate
Game identity.

The Release layer assigns one explicit disposition to every game:
`evaluated`, `pending`, `canceled`, `ineligible_classification`,
`invalid_rating`, `missing_forecast`, `unverified_publication`,
`unresolved_temporal_order`, or `missing_final_score`. Empty metric samples use
an unavailable value and count zero. These separate statuses prevent a missing
score from becoming a cancellation and prevent absent evidence from becoming a
zero forecast.

The record contains sanitized identities, numerical inputs, version pins and
evidence references. It contains no raw provider response, credentials or actor
metadata. Candidate fixtures and authentic retained-data validation remain
separate from production website artifacts until reviewed publication.

## Release operation

New builds use artifact contract 3. Its manifest and latest run name the exact
`forecast-ledger/v1` and `forecast-evaluation/v1` paths. Validation expects
contract 3 independently of the candidate's claims, so deleting the forecast
artifacts and resealing a downgraded manifest cannot turn a new Release into a
legacy one. Exact inherited run entries must equal the supplied base manifest's
run prefix. Explicit `expected_manifest_version=2` is reserved for offline
inspection of old archives; new builds never emit it.

Contract 3 is frozen to the ADR-0019 forecast graph. A future feature must add a
new artifact contract branch and advance `CURRENT_ARTIFACT_CONTRACT`; it must not
add requirements to the literal contract-3 validation branch. The independent
current-contract defaults live on `validate_release`, `ReleaseValidator`,
`promote_release`, `validate_release_chain`, and their aliases. Contract 2 is
accepted only when a caller explicitly requests legacy inspection.

A same-source contract-2 to contract-3 transition is recorded as an
`artifact-contract-upgrade`. It preserves every inherited spread forecast page
byte-for-byte, including an already published active-week half-point page, and
marks those pages as `retained-legacy-pages` in Release metadata. New canonical
natural-margin candidates are preparation-only at that transition. The season
page states that boundary and links the newly generated authenticated forecast
results; weekly and season result pages replace the old ATS presentation. The
upgrade run seals the exact retained path/digest map. Later runs carry that map
unchanged and keep those historical pages byte-for-byte; newly scheduled weeks
render ledger-selected natural forecasts. A
later same-checkpoint contract-3 run is allowed only as a
`forecast-evidence-refresh` or explicit `forecast-correction`. It preserves the
earlier run bytes and source checkpoint; an evidence refresh rewrites only the
ledger, evaluation, forecast result pages and Release metadata. Ordinary
same-checkpoint duplicates without that forecast contract remain invalid.

The Release builder accepts only `VerifiedForecastPublication` capabilities
reconstructed through the immutable publication archive, plus separate
`GameTimingEvidence` values. A path-only validation of a ledger with issued
receipts must also receive those capabilities and timing facts; inherited JSON
is not authority. A typical offline call is:

```python
release = build_release(
    snapshot,
    output_root,
    published_site=published_site,
    forecast_publications=verified_publications,
    timing_evidence=game_timing,
)
validate_release(
    release,
    published_site=published_site,
    forecast_publications=verified_publications,
    timing_evidence=game_timing,
).raise_for_failure()
```

`forecast_replacements` maps an existing terminal candidate version id to a
nonempty correction reason. The builder emits the new child from the same exact
source checkpoint; unknown predecessors and forked correction histories fail.
Provider Game timing stays a separate `GameTimingEvidence` input and is never
inferred from a schedule date or a later publication observation.

The ledger preserves candidates, receipts, publication provenance, timing facts
and score-revision chains. Individual candidate files use canonical JSON with no
trailing newline at
`cfb/years/<season>/forecasts/<version-hex>.json`; this is the byte contract
checked by the immutable publication bridge. `evaluation.json` records every
Game disposition and the exact weekly and season denominators. The existing
weekly spread-result URL becomes the issued-forecast result table for 2026, with
Graded Forecast, Predicted Winner, CORS line coverage and margin error. A season
summary lives at `<season>_FBS_forecast_results.html`. Legacy archives retain
their historical half-point spread forecast bytes; the contract upgrade replaces
result URLs with the truthful evidence-qualified evaluator.

The report shows the graded home-handicap sign, both final scores, actual home
margin, correction time, metric denominators and explicit omission counts.
Unavailable values render as a dash with count zero. Domain JSON keeps exact
decimal aggregates; the page rounds MAE/RMSE to three decimals and labels
coverage and straight-up rates as percentages. Forecast-only pages add a mobile
viewport and horizontally scrollable tables without changing legacy page bytes.
`forecast_html.py` owns only that per-table scroll and sticky identity-column
presentation; Release and the forecast record modules remain the independent
oracles for graph, field, grading and numerical semantics.
