---
status: accepted
---

# Limit football CORS credit for running up the score against an FCS opponent

Football CORS v1.1.0 (`cors-kalman-2scale/v1.1`) caps the margin credited to
an FBS favorite in games against the explicitly modeled FCS tier, which starts
in 2003. Under v1.0.0, NC State gained 3.85 points for beating Richmond 73–0
and 0.92 for beating Louisville 31–28. The owner approved giving the extra
touchdowns in an already decisive FCS win less influence on the FBS rating.
[Football investigation](https://github.com/arkar16/sportsrank/issues/102#issuecomment-7200428384)

## Weekly update

Let `d` be the FBS team's unrounded expected margin at the start of the week,
including home-field advantage with its sign reversed on the road and zero on
a neutral field. For `d > 0`, use the smaller of the final FBS margin and
`max(35, d + 7)` in the team's Student-t update. A team favored by 40 receives
the same credit for winning by 47 or 70. A narrow win, an FCS upset, and a tie
each contribute their actual margin. FBS underdogs, FBS–FBS games, and games
against the historical Other tier use the observation in
[ADR-0029](0029-adopt-kalman-two-scale-football-cors-v1.md).

When the cap binds, fit the same week with the raw scores from the same pregame
state. Take the FCS rating relative to the FBS mean from this raw fit and carry
it into the next checkpoint. The capped fit supplies the FBS ratings and the
full covariance. Using capped scores to estimate FCS strength as well made the
pooled tier about four points stronger by 2025 in the original investigation.

Conference highlights use the difference in the team's centered CORS rating
with and without the game at its first postgame checkpoint. Official scores
and records use the final on the field. Issued forecasts and captured pregame
excitement inputs retain their original edition.

## Evidence

On the current archive, NC State gains 0.92 points against Richmond, 1.22
against Louisville and 2.65 at Florida State. Across 2003–25 the best FCS win
outweighed the best pregame top-50 FBS win in 74 of 1,176 eligible
team-seasons, down from 171 of 1,181. Each version supplies its own pregame ranks.

On the same 16,868 FBS games in 2003–25, spread RMSE moved from 16.009 to
16.010 points. The earlier investigation measured a larger cost of 0.14 points
in the next FBS games after decisive FCS wins in 2017–25. The owner accepted
that trade-off for a ceiling on team credit. [Delivery and replay evidence](https://github.com/arkar16/sportsrank/issues/197)
