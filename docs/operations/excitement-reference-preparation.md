# Excitement reference preparation

The calculation definitions live in [scoring v1](../plans/2026-game-excitement-scoring-v1.md).
SR-29 prepares the pure calculation and evaluation interfaces for SR-27. These
interfaces do not acquire data, publish scores, or certify empirical acceptance.

`cfb.excitement` accepts already-qualified home/away domain values. An adapter
must establish team/Game identity, eligible pregame ranks and margin, cutoff,
source identity, completed normal-format finals, and complete score-clock
coverage before calling the full-data path. A timeline's numerical consistency
cannot prove that the provider supplied every scoring event. Do not create
beginning/end points merely to make a partial capture appear complete.

`QualifiedPregame.forecast_id` is a reference to the SR-26 forecast contract;
it is not a second ledger or proof of pre-kickoff publication. Historical
reconstructions must retain their distinct upstream provenance. `None` CORS
margin supports an unavailable AEV surprise reference, but cannot produce BEV.
A market reference is already qualified, oriented predicted home margin: its
policy and source identities must resolve to frozen constituent lines and
pregame eligibility evidence. These modules select no provider policy and
never turn late retrieval into quote-time evidence.

Final inputs require verified completed normal games. Unsupported formats,
forfeits, abandoned games and unknown results must not be passed as ordinary
finals. Quarter inputs, if supplied, are all three **cumulative score pairs** at
Q1/Q2/Q3 end, not per-quarter points. The upstream qualifier must discard
partial/invalid quarter evidence and retain its reason so valid finals can use
the final-score tier. The pure interface rejects purportedly qualified but
inconsistent quarter scores. Unknown overtime stays `None`; it is not `False`.

Timeline points are after-event score states ordered by a stable sequence.
Regulation uses elapsed minutes from 0 through 60; overtime is untimed.
Normalize provider clock direction and scoring-time semantics before adapting.
Same-clock events have zero duration and retain genuine lead/comeback changes;
identical duplicate sequences cannot add drama. The full-data calculation
checks ordering, scores, final agreement and supplied quarter checkpoints.
`calculate_aev` routes absent/invalid flow to `estimate_aev`, retaining the
qualification reason. A missing/incompatible reference raises an error.

## Frozen reference preparation

Use `reference_game_from_timeline` on qualified full-data games, then
`fit_reference_artifact(games, artifact_id=...)` explicitly during preparation.
The artifact freezes source/Game identities, matching features, measured drama
and drama-with-overtime targets, scoring/feature/algorithm versions, per-tier
and overtime-pool scales, and its content checksum. Linear interpolated sample
quartiles define IQR; each divisor is at least one point. Builds load and verify
that artifact; they must not fit a new one or select a newer version implicitly.

`dump_reference_artifact` and `load_reference_artifact` support the frozen file.
The external expected checksum must be supplied when loading a reviewed
artifact for a build. A checksum stored inside the same file detects accidental
corruption but cannot establish trusted identity after intentional resealing.
Reference validation checks structural and mathematical consistency; it does
not independently prove that a declared source timeline was complete. Retain
private source qualification and measured-target evidence for review.

Estimates retain the actual reference checksum, selected Game identities,
feature divisors, evidence tier, overtime pool, support and cutoff distance.
Target exclusion never permits a target to serve as its own neighbor. Scaling
remains frozen to the prepared reference pool; ordinary scoring never refits
scales after excluding a target. Whole-season evaluation excludes held-out
seasons from fitting both references and scales.

## Offline empirical-evaluation preparation

`cfb.excitement_evaluation.evaluate_season_holdouts` consumes `EvaluationGame`
records with qualified full timelines. Every game is measured before hiding
its evidence. With no explicit partitions it performs whole-season development
folds; explicit disjoint training/evaluation seasons require a `reservation_id`
for the externally frozen partition and criteria. The function cannot prove
that a human never inspected a purported untouched set.

Each fold tests final and quarter inputs with overtime known and hidden. Reports
include coverage and unavailable reasons, signed bias, MAE, RMSE, score/rank
correlation, movement into/out of the top decile (including cutoff ties),
season/tier/overtime/support breakdowns, largest errors and a final-margin-only
nearest-neighbor baseline. Empty/constant correlations are unavailable, not
invented zeros. Row ordering does not change reference identity or results.

The caller explicitly identifies `synthetic` or `qualified_retained` evidence.
Every report returns `production_accepted: false`: numerical acceptance,
reference sufficiency and untouched empirical evidence require separate review.
If development results change the method or thresholds, reserve new untouched
evaluation evidence. The synthetic tests establish software behavior only.

Keep candidate references, raw captures and reports outside `website/`.
Raw/provider-derived reference artifacts belong under private retention until
review identifies which derived data are safe to export. SR-27's forecast
binding, static tables, independent public semantics, reference acceptance and
owner publication authority remain separate delivery gates.
