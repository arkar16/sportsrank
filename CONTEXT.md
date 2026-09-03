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

**FBS**:
The first supported college-football classification and the initial production scope.

**Season**:
The named competition year whose games contribute to a sequence of weekly and final rankings.

**Week**:
A season checkpoint through which completed games are included in a ranking. Week 0 is the preseason state before scored games contribute.

## Rankings and publication

**CORS Ranking**:
SportsRank's ordered assessment of teams for one sport, classification, season, and week using the CORS model.
_Avoid_: Poll, power ranking

**Ranking Run**:
One attempt to calculate a CORS Ranking through a specified season week and prepare it for validation.
_Avoid_: Cron, build

**Release**:
A complete, immutable collection of site pages and supporting assets produced by a Ranking Run.
_Avoid_: Output folder, generated files

**Publication**:
The act of making one validated Release the publicly visible SportsRank site.
_Avoid_: Upload, push

**Published Site**:
The last validated Release currently visible to the public. A failed Ranking Run never changes it.
_Avoid_: Build, website folder
