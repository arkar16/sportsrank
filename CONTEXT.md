# SportsRank

SportsRank publishes independently calculated sports rankings. College football
is the initial domain, while future sports and classifications must be able to
join without redefining the existing vocabulary.

## Competition

**Sport**:
A family of competitions governed by a shared game structure and ranking domain. College football is SportsRank's initial sport.
_Avoid_: League

**Classification**:
A competitive subdivision within a sport, such as FBS, FCS, Division II, or Division III. A classification may have its own ranking release.
_Avoid_: Division, level

**Comparable Scale**:
A shared interpretation of CORS values across separate Classification rankings. The required comparability is settled; its calibration method is not.

**FBS**:
The first supported college-football classification and the initial production scope.

**Classification Entrant**:
A team with authoritative, source-cited evidence that it newly joins a Classification for a Season and therefore has no preceding FINAL in that Classification. Its declared entrant baseline is explicit Season Carryover evidence, not a fallback for a missing or mismatched returning team.

**Season**:
The named competition year whose games contribute to a sequence of weekly and final rankings.

**Conference Standings**:
Teams' positions within a conference according to that Season's competition results and governing rules. CORS strength is additional context and does not determine championship qualification.

**Projected Championship Matchup**:
The conference championship pairing indicated by Conference Standings at a specified checkpoint, subject to eligibility and unresolved ties. It can change as the Season progresses and is distinct from confirmed championship participants.

**Preseason Ranking**:
The CORS Ranking published before a Season's scored games contribute. It is initialized through Season Carryover and is used to calculate the Week 0 slate and spreads.
_Avoid_: Week 0 Ranking

**Week**:
A numbered competition checkpoint through which completed games are included in a ranking. Week 0 is a normal week in which teams may play scored games.

**Game Phase**:
A phase attached to an individual Game, such as regular season or postseason. A numbered Week may contain games from more than one phase; phase does not rename the Week.
_Avoid_: Postseason Week

**Canceled Game**:
A scheduled matchup that authoritative source evidence explicitly identifies as not played. It contributes no score or ranking result and does not prevent a historical Season from reaching FINAL. Missing scores alone never establish cancellation.

**Season Carryover**:
The rule that a Season's final CORS Ranking initializes the following Season's Preseason Ranking before roster changes and regression are applied. A source-cited Classification Entrant uses its explicitly declared entrant baseline; every unregistered omission or identity mismatch is invalid.
_Avoid_: Seed, previous-year copy

## Rankings and publication

**CORS Rating**:
A team's numerical strength assessment from the CORS model at one Season checkpoint. Its position in the ordered CORS Ranking is distinct from the rating itself.

**CORS Ranking**:
SportsRank's ordered assessment of team strength for one sport, classification, Season, and completed checkpoint using the CORS model, intended to forecast game outcomes and scoring margins. A checkpoint may be PRESEASON, a numbered Week, or FINAL.
_Avoid_: Poll, power ranking

**Model Spread**:
The scoring margin forecast by CORS for one Game.

**Forecast Cutoff**:
The time boundary before a matchup's kickoff that limits the information eligible for its Model Spread. A later recalculation does not move that boundary.

**Graded Forecast**:
The last CORS forecast published before a Game's kickoff, preserved as the basis for evaluating that Game.

**Market Line**:
A bookmaker's published point spread for one Game, used as a reference for evaluating CORS forecasts.

**Predicted Winner**:
The team CORS selects to win one Game.

**Pick'em**:
A CORS forecast with an exactly zero Model Spread and no Predicted Winner.

**Straight-Up Result**:
The outcome of CORS's Predicted Winner choice for one Game, independent of the size of the winning margin.

**CORS Line Coverage**:
Whether CORS's selected favorite covered its own Model Spread, with Cover, No cover and Push outcomes. A Pick'em has no selected favorite to grade.

**Market ATS**:
The performance of a CORS selection against a bookmaker's Market Line.

**Before Excitement Value (BEV)**:
A Game's anticipated appeal in a pregame watch guide, emphasizing team quality assessed by CORS alongside expected competitiveness and potential upsets suggested by CORS's comparison with Market Lines.

**After Excitement Value (AEV)**:
A Game's experienced drama, including sustained suspense and dramatic swings, assessed after it finishes, with actual drama taking priority over team quality and surprise. When game-flow evidence is incomplete, AEV is an estimate from the available historical evidence.

**Ranking Run**:
One attempt to calculate a CORS Ranking through a specified season week and prepare it for validation.
_Avoid_: Cron, build

**Season Snapshot**:
The normalized teams and games, including explicit played or Canceled Game disposition, retrieved for one Sport, Classification, and Season and shared by every calculation in a Ranking Run.
_Avoid_: API response, scrape

**Call Budget**:
The monthly allowance of CFBD requests available to SportsRank. The current hard ceiling is 3,000 requests per month.
_Avoid_: Rate limit

**Request Meter**:
The record and gate through which every CFBD request passes, allowing SportsRank to attribute calls, enforce budgets, and optimize observed usage.
_Avoid_: Validator, sensor

**Release**:
A complete, immutable collection of site pages and supporting assets produced by a Ranking Run.
_Avoid_: Output folder, generated files

**Publication**:
The act of making one validated Release the publicly visible SportsRank site.

**Published Artifact**:
A weekly or final ranking, spread, result, or supporting page whose canonical URL remains publicly addressable indefinitely. A correction updates that page and its last-update timestamp while prior Releases preserve earlier revisions.
_Avoid_: Temporary output
_Avoid_: Upload, push

**Published Site**:
The complete site currently visible to the public, whose deployment identity is distinct from its verification status. A failed Ranking Run never changes it; failed verification after Publication does not restore the preceding site.
_Avoid_: Build, website folder
