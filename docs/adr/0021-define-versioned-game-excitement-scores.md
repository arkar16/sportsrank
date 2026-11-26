---
status: accepted
---

# Define versioned game-excitement scores

SportsRank will publish BEV to help readers choose games beforehand and AEV to
compare how games unfolded. The owner-approved scope and examples are in the
[game-excitement plan](../plans/2026-game-excitement.md). These scores describe
viewing appeal; they do not change CORS or its forecast-accuracy measures.

## Method decisions

SportsRank owns explicit, versioned scoring formulas. Use CORS ranks
for matchup quality and Model Spreads for anticipated competitiveness. For
AEV, use the score timeline and game clock to measure sustained suspense,
lead changes and comebacks. CFBD's Excitement Index and win probabilities can
help evaluate the result without defining its scale. This avoids inheriting
an undocumented excitement formula and the provider's 2025 probability-model
break; see the [data review](../evidence/2026-09-28-cfbd-game-drama.md).

On 2026-09-29 the owner accepted the score-and-clock full-data AEV v1 method
and the revised BEV formula. A close #1-versus-#2 matchup should receive a
score in the high 90s without needing market disagreement; the accepted BEV
formula gives 98.6 for a two-point forecast with no market input. The
[v1 calculations](../plans/2026-game-excitement-scoring-v1.md) own both accepted
formulas and their synthetic examples. Synthetic checks do not establish
historical calibration.

## Comparability and publication

The owner chose a fixed 0-100 range, frozen BEV per published forecast, and
combined AEV rankings that retain estimated values with an asterisk. Those
choices require retaining the input snapshot, scoring version and evidence
level. A later model or data improvement must not silently rewrite a published
BEV. Corrections remain subject to the existing artifact-preservation rules.

On 2026-09-29 the owner also accepted estimating AEV against the same target
as full-data AEV. The method
averages the measured drama of comparable complete games, using quarter
scores when available and final scores otherwise. Keep known quality and
surprise bonuses outside the estimate and freeze the reference artifact.
The calculation document specifies the initial matching and missing-overtime
rules. Test on whole seasons withheld from fitting. The asterisk identifies
the estimate; it does not establish numerical comparability. The approach is
accepted. Each reference artifact requires empirical validation. On 2026-11-26
the owner approved publishing starred football quarter-score estimates if the
frozen reference passed the reserved 2025 season with mean absolute error
at most 5 points, absolute signed bias at most 2 points and Spearman rank
correlation at least 0.90. The [delivery issue](https://github.com/arkar16/sportsrank/issues/179)
records the confirmation and coverage.

## Men’s basketball

On 2026-11-26 the owner selected the same rank quality, scoring weights and
pregame/postgame distinction for Division I men’s basketball and requested
possession-based point scales. A three-point margin represents one possession
on the scoreboard. Two possessions set the closeness and surprise scale. Three
set the comeback scale. Use the regulation clock for two 20-minute halves.
Each verified overtime contributes to observed lead changes and the winner’s
deficit. The two-point overtime bonus applies once. Football and basketball
have separate scoring versions and reference populations. The
[basketball delivery](https://github.com/arkar16/sportsrank/issues/181) owns
the empirical coverage and acceptance.

On 2026-11-26 the owner set the release standard for basketball games with a
verified halftime score, final and overtime status. The frozen basketball
reference had to average at most 7 AEV points of absolute error, stay within
2 points of signed bias in either direction and reach a Spearman rank
correlation of at least 0.85 on the reserved 2024–25 season. It passed all
three criteria. The season guides rank these starred estimates alongside
measured scores. A separately labelled final-only estimate covers games without a verified halftime score.

## Coverage of the API game archive

On 2026-11-26 the owner requested a BEV and AEV for every historical football
and men's basketball game with a verified final, and limited the public site to
sports data originating in an API. A scheduled game gets BEV. AEV follows a
verified final. A numeric score carries its evidence label wherever it appears.
A prior-season aggregate or an inferred scoreline cannot establish that two
schools played. Retain an early basketball season when its API supplies
individual games. See the [scoring rules](../plans/2026-game-excitement-scoring-v1.md)
and [football delivery](https://github.com/arkar16/sportsrank/issues/182).

## Delivery prerequisites

- Inspect the API's actual provider coverage before finalizing the excitement
  market reference. The owner is comfortable averaging available lines or
  standardizing on a suitable provider; a mandatory median was not selected.
  Preserve the individual inputs and weekly capture basis. This does not
  select the evaluation benchmark in ADR-0019. A historical line with
  unestablished pregame eligibility is unavailable for the market component;
  retrieval time alone does not establish quote time. Apply the accepted
  missing-market rules rather than inserting uncertain retrospective lines.
- Establish reference sufficiency and numerical release criteria from coverage
  and held-out results before accepting a production artifact for combined
  rankings. A failed validation returns the artifact for revision; it does not
  authorize removing estimates, silently changing the scale or weakening the
  accepted viewing preferences.

Initial coverage and presentation are settled in the plan. These data and
validation prerequisites belong in the first-pass implementation contract;
they do not reopen the accepted scoring direction. Acceptance of this ADR
does not itself authorize live CFBD calls, implementation dispatch, merge or
production publication.

## Public source decision, 26 November 2026

The owner selected CollegeFootballData for football and CollegeBasketballData
for men's basketball. Public pregame checkpoints, schedules, final scores,
scoring evidence and comparison references must come from these providers.
Each sport qualifies its period estimator against its reserved season. Every
verified ordinary final can receive a labeled final-score estimate when richer
evidence is unavailable. Scheduled games receive pregame appeal.
