# Original CORS workbook as a design reference

Read-only inspection on 2026-09-28 of
[CORS CFB 2020_21.xlsx](<../inbox/CORS CFB 2020_21.xlsx>) and the
[owner's evaluation proposal](../inbox/proposed_adr0019.md). Findings below come
from stored worksheet formulas, labels and cached values, not a fresh Excel
recalculation or a validated historical forecast backtest. Forecast-evaluation
decisions belong in
[ADR-0019](../adr/0019-evaluate-predictions-against-market-lines.md); excitement
design is being explored in the
[game-excitement plan](../plans/2026-game-excitement.md).

The retained public reference has SHA256
`24c991a1584ec224356baa09b43de7574a869d81e51e2fbf31ab28fd1c6239b6`.
Current-tree publication evidence permits only these exact workbook bytes at
the linked repository path; it does not permit other Office files or archives,
changed workbook contents, or workbook inclusion in website exports. Read-only
container inspection on October 5 found 32 XML/relationship/directory entries,
valid XML and no macros, external-link parts, or unsafe member paths. This
qualification preserves an existing public design reference, not a private CFBD
source exception or a change to the forecast acceptance oracle.

## Features to consider in future planning

| Feature | Workbook evidence | Interpretation |
| --- | --- | --- |
| Historical and current ratings and spread estimates for each game | `WLSpread!D1:P2`, within the game table `A1:AH664` | Preserve the distinction between an original forecast and a retrospective estimate using later ratings. The column labels alone do not prove a saved value was available before kickoff. |
| Week-by-week ranking history | `HistoricalRank!A1:S531` | Week 0–16 snapshots support a view of ranking progression. These are saved values, not a recalculating historical model. |
| Current rankings and conference championship matchup estimates | `CurrentRank!B1:K4`, with final team points in `C3:C132` | Matchup comparisons are formula outputs. No standalone interactive prediction tool was verified. |
| Team and conference standings and comparisons | `Standings!A1:R131`, plus `ConfStandings`, `Calculations` and `Realignment` sheets | Useful presentation references. The presence of these tables does not establish a realignment simulation. |
| Team-specific normal and COVID home-field assumptions | `HFA!A1:D132` | No worksheet formula directly references this sheet. Treat it as a source of model ideas, not proof those assumptions drive the game forecasts. |
| Actual scores, winners, overtime and a straight-up flag | `WLSpread!Z1:AG2` | Actual outcomes and a rudimentary winner check exist, but many result cells are saved values rather than formulas. |
| Before and After Excitement Value, plus a confidence-style calculation | `WLSpread!Q1:T2` and `Y2` | BEV rewards rank quality and projected closeness; AEV uses rank quality, actual margin and overtime. No calibration evidence establishes the confidence-style calculation as a win probability. |

No native chart objects, projected scores/totals, bookmaker lines, or systematic
per-week/per-team accuracy summaries were verified.

## Forecast signs and scaling

`WLSpread!O2` calculates `(D2 - (H2 + L2)) / 2`, using the historical away and
home ratings and the historical HFA value. `P2` substitutes current ratings
`E2` and `I2` but still uses `L2`, despite a separate "HFA Now" column `M`.
The intended HFA choice for the retrospective estimate needs clarification.

A positive workbook spread means the away team is favored. This agrees with
the owner's home-handicap display convention: home `+2` means away favored by
two. The current Python engine instead stores the predicted home margin in
`spread_value` and reverses its sign for the displayed home handicap. The two
representations must be normalized before comparison.

The workbook's division by two is a fixed rating-to-margin conversion. No fit
or rationale for that coefficient was verified. The current engine uses the
rating difference plus HFA, rounded to half-points. This difference motivates
checking model versions and rating scales; it does not establish that the
current implementation omitted a required division by two.

## Grading limitations

- `WLSpread!AG2` checks whether the predicted favorite in `X2` equals the actual
  winner in `Z2`, returning 1 or 0 when its prerequisite is present.
- The only populated cell under ATS, `WLSpread!AH8`, repeats that favorite/winner
  comparison. It never compares the model spread with the actual margin, so it
  is not a valid ATS or model-line coverage grader.
- No formula was verified that creates an explicit push result from equality
  between the actual margin and the model line.
- `WLSpread!S2:T2` grade an actual result using margin, rank value and overtime.
  They do not compare the result with the forecast spread and are not forecast
  error measurements.

The workbook is a useful record of product and model ideas. Its saved grades
and ad hoc counts are not an acceptance oracle for the new evaluator.

## Before and After Excitement Value

Follow-up inspection on 2026-09-28 identifies the merged headers `Q1:R1` as
`BEV` and `S1:T1` as `AEV`. Columns `Q/S` hold numeric values and `R/T` hold
letter grades. With availability guards omitted, the stored row-2 formulas
simplify to:

- **BEV (`Q2`):** `1 - (abs(O2) + N2) / 200`, where `O2` is the historical
  Model Spread and `N2 = (F2 + J2) / 2` is the mean of the two historical ranks.
  Better-ranked teams and smaller predicted margins increase the value.
- **AEV (`S2`):** `min(1, 1 - (AE2 + N2) / 200 + 0.02 * AD2)`, where `AE2`
  is the winner-minus-loser score margin and `AD2` is the stored overtime value.
  This substitutes final margin for predicted margin and adds an overtime
  bonus. It does not measure lead changes, comebacks, or win-probability swings,
  nor compare the result with a market line.

These formulas establish the workbook's design idea, not a validated measure
of entertainment. AEV can remain high for a large-margin game between highly
ranked teams. The row-2 formulas impose no lower bound on BEV or AEV.

`Y2` (`Conf`) divides the predicted favorite's historical CORS by the sum of
the two teams' historical CORS values, adds `0.05`, and multiplies by `100`.
BEV is only an availability guard in that calculation; it is not an input to
the numeric confidence result. This is not a calibrated win probability.

The stored formulas in `D2/F2/H2/J2` refer to `Week1`, defined as
`HistoricalRank!$A$1:$C$132`. Later game rows mostly retain saved values rather
than those formulas, so their exact rating source and timing cannot be
established from the workbook alone. `E2/I2/G2/K2` instead refer to
`CurrentRank`, whose headers identify FINAL. The “before” label therefore
does not establish a verified observation before each game's kickoff.

Across the 663 game rows, `Q/R/S/T` preserve only their row-2 formulas; later
populated values are saved constants. Reusing the concepts requires a new
explicit formula, timing policy and behavioral validation.

## Current implementation comparison

The modern [ranking engine](../../cfb/ranking_engine.py) already emits forecasts
in the owner's home-handicap display convention. The
[Release grader](../../cfb/release.py) already publishes per-game coverage of
CORS's own line and recognizes pushes. It does not yet emit an explicit
straight-up result or margin-error metrics. The
[legacy summary generator](../../cfb/spread_results.py) has SU/ATS aggregates
with different edge-case handling; those counts cannot be mixed with the
modern results without reconciliation.
