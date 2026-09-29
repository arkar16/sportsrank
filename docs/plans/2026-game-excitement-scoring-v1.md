# Game excitement: v1 calculations

Status: BEV, full-data AEV and the comparable-game estimated-AEV approach
accepted on 2026-09-29 under
[ADR-0021](../adr/0021-define-versioned-game-excitement-scores.md).
No historical calibration is claimed.
The [product plan](2026-game-excitement.md) owns the accepted behavior.

## Shared definitions

`clip(x)` limits a value to 0-1. `rA` and `rB` are the teams' pregame CORS
rank positions, starting at 1. They are not public rankings or CORS Rating
values. No 0-100 scale is assumed for the underlying CORS Ratings.

Matchup quality used by accepted BEV and full-data AEV v1:

```text
Q = exp(-(rA + rB - 2) / 100)
```

This is a fixed function of mean CORS rank, not a percentile recalculated for
each week's slate. Same ranks produce the same quality component across
seasons. Model Spread separately accounts for the strength gap and site.
The rank horizon is 50 places per team.

## BEV

The owner accepted the revised BEV formula below on 2026-09-29 after
challenging the initial numerical scale. The prior `60*Q + 35*C + B`, with
`C = exp(-abs(s)/14)`, produced 89.7 for #1 versus #2 by two points. Even a
pick'em between those teams reached only 94.4 without a market boost. The
accepted revision removes that dependency on bookmaker disagreement and
reduces the penalty on already-close forecasts. It does not change AEV.

Let `s` be CORS's predicted margin for Team A and `m` the market's predicted
margin for that same team. Positive means Team A is favored; this internal
notation does not change the site's home-handicap display convention.

```text
C = exp(-(abs(s) / 14)^2)
closer = clip((abs(m) - abs(s)) / 14)
upset = clip(abs(s) / 2) if s * m < 0, otherwise 0
B = C * (3 * closer + 2 * upset)
base = 60 * Q + 40 * C
remaining = 100 - base
BEV = base + B * remaining / (5 + remaining)
```

When the market reference is absent, set `B = 0` and record that it is missing;
do not substitute the CORS forecast as its own market reference. A zero CORS
margin receives maximum closeness but no predicted-upset bonus. A zero market
margin identifies no market underdog and contributes no boost.

Quality supplies up to 60 base points and predicted closeness up to 40. The
market comparison adds a small bonus that tapers as the base approaches 100.
An elite close matchup can therefore reach the high 90s without a market
bonus, while adding one cannot overflow the scale. Multiplying `B` by `C`
limits the attraction of an
expected blowout even when CORS picks the opposite winner from the market.
The additional upset component reaches its two-point cap at a two-point CORS
underdog-win forecast; it tapers to zero near Pick'em rather than jumping when
the forecast changes sign by a hundredth of a point.
All components are bounded; missing CORS inputs cannot be converted to zeros
and passed off as a calculated BEV.

### Synthetic arithmetic checks

These are invented inputs checked by direct arithmetic on 2026-09-29. They
verify selected orderings, not accuracy against historical viewing preferences.

| Matchup | CORS forecast | Market forecast | BEV |
| --- | --- | --- | ---: |
| CORS #1 vs. #2 | #1 by 2 | Unavailable | 98.6 |
| CORS #3 vs. #5 | #3 by 3 | Unavailable | 94.7 |
| CORS #1 vs. #2 | #1 by 7 | Unavailable | 90.6 |
| CORS #99 vs. #100 | #99 by 2 | Unavailable | 47.6 |
| Kansas State #18 vs. Iowa State #22 | Kansas State by 2 | Unavailable | 80.2 |
| Ohio State #1 vs. Akron #80 | Ohio State by 28 | Unavailable | 28.0 |
| Alabama #5 vs. Auburn #25 | Alabama by 2 | Alabama by 14 | 86.4 |
| Alabama #5 vs. Auburn #25 | Auburn by 2 | Alabama by 14 | 87.9 |
| Alabama #5 vs. Auburn #25 | Auburn by 28 | Alabama by 14 | 46.1 |
| Alabama #5 vs. Auburn #25 | Alabama by 2 | Unavailable | 84.5 |

The chosen weights preserve the accepted examples. Broader checks must test
whether rank quality dominates too strongly, whether seven-to-fourteen-point
forecasts fall sensibly between close games and mismatches, and whether the
market boost introduces unwanted ordering changes.

The accepted revision passed a 3,168-case bound and side-swap check on
2026-09-29, with no 0-100 violations or changes when the team labels and margin
signs were reversed. The accepted competitiveness and upset orderings also
passed. These are formula checks, not calibration.

## Full-data AEV

Definition accepted for v1 on 2026-09-29:

```text
AEV = 80 * T + 8 * L + 4 * R + 2 * O + 3 * Q + 3 * U
```

All components range from 0 to 1:

- **T, sustained tension:** let `d(t)` be the home-minus-away score margin
  during regulation at elapsed minute `t`. Average `exp(-abs(d(t))/14)` over
  game-clock time with weight `1 + 3*t/60`. The denominator is 150 weighted
  minutes for a normal 60-minute game. Later intervals count more heavily;
  additional plays at the same score and clock cannot inflate this component.
  Integrate the linear weight exactly over each constant-margin interval,
  equivalently evaluating it at that interval's midpoint.
- **L, lead changes:** `clip(lead_changes / 3)`. Track the last nonzero lead
  across ties: home lead, tie, away lead counts once; home lead, tie, home lead
  counts zero. Do not count taking the first lead as a lead change.
- **R, comeback:** `clip(eventual_winner_max_deficit / 21)`. A winner that
  never trailed receives zero. This is the largest observed deficit, not the
  expected margin or a deficit inferred from the final score.
- **O, overtime:** 1 if verified overtime occurred, otherwise 0. Additional
  overtime periods do not stack this bonus. Overtime events contribute to L
  and R; do not fabricate elapsed regulation time for untimed overtime.
- **Q, quality:** the same pregame CORS-rank component used in BEV, contributing
  at most three points. Postgame rankings do not replace its pregame basis.
- **U, actual upset:** if the eventual winner was the underdog on the accepted
  market reference, `clip(underdog_margin / 14)`; otherwise zero. Apply the
  owner's market-first, pregame-CORS-second fallback. If neither reference
  exists, award no surprise bonus and record the missing basis.

Normalize and validate the score timeline before calculation: ordering,
home/away orientation, score-change timing, clock/period interpretation,
duplicate events and final-score agreement matter. A verified 60-minute
timeline is needed for the full T calculation. Partial or shortened timelines
need the estimated-data path rather than invented regulation intervals.

### Synthetic timeline checks

Each pair below is `[elapsed regulation minute, home score margin]`; the
margin holds until the next pair. All four examples have no overtime. These
are hypothetical score paths, not inspected historical games.

| Example | CORS ranks | Market | AEV |
| --- | --- | --- | ---: |
| Repeated close lead changes, home wins | 99 / 100 | Unavailable | 78.7 |
| Routine home win | 1 / 2 | Unavailable | 21.3 |
| Comfortable away upset | 5 / 25 | Home by 14 | 23.6 |
| Sustained close game without lead changes, home wins | 30 / 35 | Unavailable | 73.1 |

```text
Repeated close lead changes:
[[0,0],[5,3],[15,-3],[25,0],[35,3],[45,-3],[55,0],[60,3]]

Routine home win:
[[0,0],[5,7],[10,14],[20,21],[30,28],[60,28]]

Comfortable away upset:
[[0,0],[5,-7],[10,-14],[20,-21],[30,-28],[60,-28]]

Sustained close game, no lead changes:
[[0,0],[10,3],[20,0],[30,3],[40,0],[50,3],[60,3]]
```

The comfortable-upset example has the same tension contribution as the
routine-win example. Its surprise bonus is three points; the remaining
difference comes from pregame quality. The no-lead-change example earns a high
score from sustained closeness alone, satisfying the owner's explicit intent.

This measures score-and-clock drama. It does not yet account for possession,
field position or down-and-distance. Games with the same score path can differ
on those dimensions. The owner accepted this first-pass method; empirical
validation and the estimated-data method remain separate work.

## Estimated AEV: comparable-game method

The owner accepted this approach on 2026-09-29. The initial reference artifact
has not been fitted or calibrated.
Estimate typical drama from complete reference games with similar results.
Use quarter scores when available and final scores otherwise. Two games with
the same limited evidence can receive the same estimate even if their actual
drama differed. The estimate does not assert that any particular comeback or
lead change occurred.

Keep the observed quality and surprise bonuses separate from the estimated
drama. For every validated full-data reference game, retain both targets:

```text
D = 80*T + 8*L + 4*R
M = D + 2*O

Known overtime status: AEV* = mean(neighbor D) + 2*O + 3*Q + 3*U
Unknown overtime status: AEV* = mean(neighbor M) + 3*Q + 3*U
```

Unknown overtime is not treated as zero. The second branch estimates its
contribution along with the drama and adds no separate overtime bonus. Q and U
use the same pregame evidence rules as full AEV; neither is a matching feature.
Thus missing flow cannot enlarge the permitted quality or surprise bonuses.
An average of bounded reference targets preserves the 0-100 range.

### Initial deterministic recipe

- Minimum evidence is a verified completed game's final scores. Unverified
  results, forfeits and abandoned games are not converted into ordinary games.
- The final-score tier matches on absolute final margin and combined final
  points. The quarter-score tier also uses the cumulative margin after each of
  the first three quarters, oriented to the eventual winner. Use the quarter
  tier only when all three checkpoints are valid; otherwise use the final-score
  tier. Historical tied games use the final-score tier.
- When overtime status is known, first select reference games with the same
  status. If none exist, use the all-status pool to estimate D and record the
  broader reference basis. Unknown overtime uses the all-status pool and M.
- Scale each numeric feature by its reference-set interquartile range, with a
  one-point minimum divisor. Select the 50 nearest games by Euclidean distance,
  or all eligible games when fewer exist. Include every game tied at the
  cutoff distance and take an unweighted mean. Record actual support and
  distance, including when a tie produces more than 50 neighbors.
- Freeze the source snapshots, eligible reference games, features, targets,
  scales and algorithm version in a reference artifact. Ordinary builds do
  not refit it or depend on a live service. Always exclude the target game
  from its own reference set.

The initial reference set must contain validated normal completed games.
Shortened games or other unsupported formats need separate evidence before
using this estimate; they must not masquerade as a normal 60-minute timeline.
No usable reference artifact is a data-preparation blocker, not permission to
invent a score of 50 or silently suppress the requested historical estimates.

### Validation before accepting an artifact

On complete games, hide the timeline and calculate both reduced-input tiers.
Hold out whole seasons, deriving references and feature scales only from the
other seasons. Also test the unknown-overtime branch by hiding that field.
Report signed bias, absolute error, score/rank correlation and movement into
or out of the highest-scoring games, broken down by season, tier, overtime and
reference support. Compare with a simple final-margin-only baseline.

Check the accepted game-ordering examples and inspect the largest errors.
Do not promise that final scores can distinguish every tense game from a late
comeback. The combined ranking requires evidence that the chosen estimate is
useful on its shared scale; the asterisk alone does not establish that.
Reference sufficiency and numerical release criteria must be set from the
coverage and held-out results before accepting a production artifact. No
historical error rates or fitted reference artifact are claimed here.

Every published estimate retains `*` and the agreed
footnote, regardless of which input tier supplied it.

## Market reference: inspect provider coverage before selection

On 2026-09-29 the owner directed checking which providers the API actually
supplies before finalizing the reference. Averaging available lines is
acceptable; standardizing on a suitable available provider is also acceptable.
The earlier median recommendation is not a selected requirement. A single
available provider naturally supplies the reference directly.

Use eligible `spread` values captured for the weekly forecast, normalized to
one team orientation. Keep constituent provider lines, retrieval time and
source snapshot. Do not mix opening and current values or label an unspecified
`spread` as a closing line. No eligible lines means no market comparison.
Record a deterministic averaging or provider-selection rule after the coverage
inspection, before implementation.

The [CFBD line schema](https://github.com/CFBD/cfbd-python/blob/main/docs/GameLine.md)
has provider and spread fields but no quote timestamp, as inspected on
2026-09-29. Retrieval time is not quote time. Late-retrieved historical lines
are ineligible for the market component unless evidence establishes their
pregame eligibility. Apply the accepted missing-market rules when it cannot.
Do not imply that such a reconstruction
was published before kickoff. This policy is for excitement and does not
silently select ADR-0019's forecast-evaluation benchmark.
