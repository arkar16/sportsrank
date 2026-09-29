# Zero-spread presentation in local CORS forecasts

Read-only inspection on 2026-09-28 of the generated forecast pages in this
checkout for 2024, 2025 and 2026 Week 0. These are local, retrospectively
generated artifacts, not proof of forecasts issued before kickoff or a check
of the live site. The evaluation decision is [ADR-0019](../adr/0019-evaluate-predictions-against-market-lines.md).

| Season | Forecast pages | Matchup rows | Zero stored spreads | Exact zero before spread rounding |
| --- | ---: | ---: | ---: | ---: |
| 2024 | 23 | 798 | 29 | 0 |
| 2025 | 23 | 808 | 23 | 0 |
| 2026 Week 0 | 1 | 8 | 0 | 0 |
| Total | 47 | 1,614 | 52 | 0 |

The margin before spread rounding was reconstructed using decimal arithmetic:
home CORS minus away CORS, plus the current two-point HFA for non-neutral games.
The [ranking engine](../../cfb/ranking_engine.py) already rounds CORS ratings
to two decimal places before this calculation. It then rounds the resulting
margin to half-points and renders the opposite sign as a home-team handicap.
This reconstruction recovers the inputs to that final rounding step, not an
unrounded internal team-strength estimate.

Examples:

- [2024 Week 12 Auburn–UL Monroe](../../website/cfb/years/2024/spread/2024_W12_FBS_spread.html):
  `9.19 - 11.44 + 2 = -0.25`; displayed as `Auburn +0`.
- [2024 Week 1 Texas A&M–Notre Dame](../../website/cfb/years/2024/spread/2024_W1_FBS_spread.html):
  the pre-rounding home margin is `+0.25`; displayed as `Texas A&M +0`.
- [2025 Week 15 Ohio State–Indiana](../../website/cfb/years/2025/spread/2025_W15_FBS_spread.html):
  the neutral-site pre-rounding home margin is `+0.02`; displayed as `Ohio State +0`.

All 52 observed zeros retain a directional model edge before half-point rounding.
Preserving that margin would permit a winner selection in these cases without
inventing a nonzero spread. This does not prove that genuine exact ties cannot
occur; they still need an explicit selection policy if every eligible matchup
must have a Predicted Winner.

Current forecast rows do not retain the pre-rounding matchup margin, a selected
winner or a win probability. A reconstructed winner must not be represented as
an originally published pick. No empirical win probability or uncertainty level
was established by this inspection.
