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

A representative sanitized candidate has this shape (digest values abbreviated):

```json
{
  "schema_version": "forecast-candidate/v1",
  "version_id": "sha256:<candidate-content>",
  "game": {
    "provider_id": "401752001",
    "season": 2026,
    "week": 1,
    "home_team": "Home",
    "away_team": "Away",
    "home_classification": "fbs",
    "away_classification": "fbs",
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
