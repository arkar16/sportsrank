---
status: accepted
---

# Weight basketball outcomes by the pregame spread in CORS v1.1.0

On November 27, 2026 the owner approved basketball CORS v1.1.0
(`cbb-kalman-2scale/v1.1`). The goal is for rating changes to reflect what
winning or losing tells us about team strength, especially when one team
entered as a heavy favorite. Under v1.0.0 Arizona gained 2.78 points for
beating Morgan State by 63 and 0.94 for winning at Duke by five in the same
week. Norfolk State lost more points for a 48-point road loss at Houston
than for a seven-point home loss to Coppin State.
[Delivery #195](https://github.com/arkar16/sportsrank/issues/195)

## Decision

For each Division I matchup, retain its unrounded, venue-adjusted expected
home margin `d` from the Monday checkpoint preceding tipoff. Give the exact
final margin weight `L = 400 / (400 + d²)` and the winner weight `1 − L`.
A pick’em uses the margin alone. A 20-point spread gives the two observations
equal weight. A 40-point spread gives the exact margin one fifth of the weight.

At the first weekly checkpoint that can use the final, evaluate both
observations at the common pre-update state. Let `m` be the expected home
margin including league and team court effects, `y` the final home score
minus the away score, and `r` the v1.0.0 robust margin precision. For the
winner use `s = 11√3/π` and `p = 1 / (1 + exp(−m/s))`.

- The score along the game's design vector is
  `L r (y − m) + (1 − L) (I[y > 0] − p) / s`.
- The precision along that vector is `L r + (1 − L) p (1 − p) / s²`.

Season carryover, court effects, and the non-Division I margin update follow
[ADR-0030](0030-adopt-kalman-two-scale-basketball-cors-v1.md). The public
replay uses the current official CollegeBasketballData rosters and starts
from the first available season. The six-hour buffer begins at the end of
the possible tipoff window for date-only games.

A game's signed impact is the team's centered rating with that game in its
first eligible weekly fit minus its rating with the whole game omitted from
that fit. Best win selects the largest impact among wins and worst loss
selects the smallest among losses. The reconstructed rankings and spreads use
v1.1.0 throughout the public archive. Dated editions keep the predictions originally printed.

The published excitement archive used v1.0.0 to supply pregame ranks,
spreads and baseline quality. Rebuild those inputs under v1.0.0 for checkpoints
before November 27, 2026 UTC so their frozen BEV and AEV reproduce in a clean
build. Later excitement checkpoints use v1.1.0.
[Game excitement decision](0021-define-versioned-game-excitement-scores.md)

## Evidence and trade-off

Arizona's 2023–24 best win becomes the win at Duke (+1.22). The Morgan State
win still adds 1.13 points. Norfolk State's 2022–23 worst loss becomes the
Coppin State game (−0.93), followed in this comparison by the Houston game
(−0.69). In seven complete modern seasons, 796 team-seasons had wins over
both a pregame top-50 opponent and an opponent ranked 201st or lower using
the same v1.0.0 opponent ranks. The latter group supplied the overall best
win for 129 teams under v1.1.0, down from 304 under v1.0.0.

On 54,197 comparable 2017–26 games with each model warmed up from 2002, spread
RMSE rose from 11.3971 to 11.4121 points. Winner accuracy went from 72.47%
to 72.52%, excluding pick’ems. The owner accepted this forecast trade-off for
the change in game credit.

On the public CollegeBasketballData archive, there are 54,322 scored Division I
games in those ten seasons. Spread RMSE goes from 11.3906 to 11.4054 points.
The Duke and Coppin State selections reproduce to the same hundredth and
Kansas leads the final 2019–20 standings under both versions. The earlier
research replay with the end-of-2019 modern seed put Gonzaga first under v1.1.0.
