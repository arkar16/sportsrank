# CFBD data candidates for game drama

Official documentation inspected on 2026-09-28 for the
[game-excitement exploration](../plans/2026-game-excitement.md).
No authenticated CFBD requests were made. Documented schema availability is
not evidence of populated values for any particular game or season.

## Useful endpoints

| Endpoint | Documented data | Potential use |
| --- | --- | --- |
| `GET /games` | Nullable `excitementIndex`, per-team line scores, final scores and game metadata | Existing aggregate drama candidate; the current SportsRank normalization does not retain it. |
| `GET /metrics/wp` with `gameId` | Per-play home win probability, play/game IDs, play number, scores, possession indicator, field position and down/distance | Reconstruct probability swings and explain why a game was dramatic. |
| `GET /plays` | Period, clock, offense/defense, scores, play/drive sequence, scoring flag and play text | Locate comebacks, lead changes and late decisive plays; join with probability records where IDs and coverage permit. |

Sources: [Games reference](https://apinext.collegefootballdata.com/api/games),
[Metrics API](https://github.com/CFBD/cfbd-python/blob/main/docs/MetricsApi.md),
[PlayWinProbability schema](https://github.com/CFBD/cfbd-python/blob/main/docs/PlayWinProbability.md),
[Play schema](https://github.com/CFBD/cfbd-python/blob/main/docs/Play.md).

## Interpretation and limits

CFBD defines Excitement Index as an aggregate of in-game win-probability
movement, increasing with larger and more frequent swings. It describes the
game path rather than team quality or final-margin closeness alone. That makes
it a plausible baseline candidate for AEV; adopting it is still a design
choice. The inspected documentation does not specify an exact excitement
formula or a bounded display scale, and describes the underlying probability
models as proprietary.

Stored 2025-and-later probabilities use a revamped model with regulation,
clutch and overtime components. Values through 2024 were not backfilled;
CFBD explicitly warns that this affects excitement comparisons across the
boundary. Any historical comparison should identify those eras rather than
assume a stable common scale. Source:
[win-probability methodology](https://apinext.collegefootballdata.com/win-probability).

The probability concepts must remain distinct. CFBD's pregame probability is
derived from a market spread. Its postgame probability estimates winning
frequency for similar aggregate performance; it is not the play-by-play path
or an excitement value. Source:
[metrics definitions](https://apinext.collegefootballdata.com/metrics-and-definitions).

The documentation lists in-game win-probability coverage from 2014, with
completeness varying by game and season. A missing metric cannot be treated
as evidence of a dull game. Source:
[data availability](https://apinext.collegefootballdata.com/data-availability).

## Implications to investigate

- Evaluate the supplied index against representative games before choosing
  it as the AEV basis. Its presence in the existing games response could avoid
  an additional per-game endpoint solely for the aggregate, if populated in
  the relevant snapshots. A bounded local inspection found no excitement,
  line-score or win-probability fields in 103 game/snapshot JSON files across
  this checkout's `.sportsrank/`, `website/`, `build/` and `tests/fixtures/`
  paths. Those files cannot supply a real game-flow example for calibration.
  The separately retained private raw-input archive was not inspected; this
  finding does not establish its contents.
- A CORS drama formula could add explicit late-game tension, comeback and
  lead-change components. Those would be SportsRank design choices, not
  documented components of CFBD's aggregate.
- Deriving an overtime count or late-game measure needs validated period/clock
  conventions and complete game sequences. Endpoint existence alone does not
  establish those contracts.
- Preserve provider provenance and distinguish missing data from low scores.
  Define coverage, historical comparability and any new request allowance
  before implementation or acquisition.
