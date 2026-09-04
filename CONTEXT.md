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

**Season**:
The named competition year whose games contribute to a sequence of weekly and final rankings.

**Week**:
A season checkpoint through which completed games are included in a ranking. Week 0 is the preseason state before scored games contribute.

**Season Carryover**:
The rule that a Season's final CORS Ranking initializes the following Season's Week 0 Ranking before roster changes and regression are applied.
_Avoid_: Seed, previous-year copy

## Rankings and publication

**CORS Ranking**:
SportsRank's ordered assessment of teams for one sport, classification, season, and week using the CORS model.
_Avoid_: Poll, power ranking

**Ranking Run**:
One attempt to calculate a CORS Ranking through a specified season week and prepare it for validation.
_Avoid_: Cron, build

**Season Snapshot**:
The normalized teams and games retrieved for one Sport, Classification, and Season and shared by every calculation in a Ranking Run.
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
The last validated Release currently visible to the public. A failed Ranking Run never changes it.
_Avoid_: Build, website folder
