---
status: accepted
---

# Reconstruct historical postseason with archived membership

On October 9 the owner authorized historical postseason restoration, corrected
week handling, reconstruction of affected rankings and predictions through
subsequent Season Carryover, and regenerated recreational performance. The
owner explicitly approved each existing archived season's team list, corrected
prior FINAL values for returning teams, and the legacy `-10` baseline only for
teams newly listed in that historical season. This bounded pre-2024 exception
uses archived membership rather than requiring a new historical Classification
Entrant registry. Missing returning-team carryover remains an error. From 2024
onward ADR-0013's registered entrants and the production validator still apply.

Read the retained regular results and an explicitly supplied local historical
games CSV through one Season Snapshot per season. Preserve regular provider
week labels except dated Week 1 games preceding the season boundary, which
belong to scored Week 0. Derive a pre-2024 Week 1 Monday from the unique modal
origin of matching dated regular games and retained week labels; record that
origin and its evidence count. Date-only inputs retain their calendar dates;
provider timestamps use America/New_York. Place positively identified postseason
games on ADR-0014's continuous date lattice, without a fixed last-week cutoff
or a modern postseason date window imposed on older schedules. Preserve
unknown/unrated opponents without inventing ranked membership. A historical
season without actual Week 0 games retains its old W0 URL as a PRESEASON alias,
so an artificial empty scored checkpoint cannot attenuate carryover.

Rebuild from the earliest affected season through the latest completed archived
checkpoint. Reconstructed predictions use preceding-checkpoint ratings and are
explicitly retrospective; original saved spreads and authenticated issued
forecasts remain unchanged. Prepare a complete separate local review tree,
including historical summaries and progression, without changing the source
site or creating a production receipt. Inherited publication metadata is not
valid for that review tree. Acceptance and a historical correction's authenticated
release integration remain with the delivery task and owner; this decision
does not authorize a provider request, merge, publication or weakened release
validation. It supersedes the recovery plan's pre-2024 freeze only for this
authorized isolated reconstruction, not for ordinary publication.
