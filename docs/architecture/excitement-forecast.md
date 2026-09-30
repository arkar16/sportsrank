# BEV binding to preserved forecasts

The BEV binding boundary connects the accepted [scoring contract](../plans/2026-game-excitement-scoring-v1.md)
to the [forecast record](forecast-record.md). It owns derived BEV artifacts and
their semantic validation. Forecast identity, correction chains, publication
evidence and grading remain owned by the existing forecast modules.

## Acyclic content identity

A BEV artifact needs the original pregame inputs, while the candidate needs a
binding to the artifact. Hashing each final object into the other would be
circular. The anchor therefore removes only the candidate's `version_id` and
its own BEV binding before canonical hashing. Every other immutable candidate
field remains, including game identity, margin and precision, both ratings and
ranks, cutoff and checkpoint, source/model provenance, predecessor/reason and
other version bindings.

The artifact carries this complete anchor and the versioned scoring result.
A new candidate can then carry one `VersionBinding` to the artifact's identity
and exact canonical byte digest. Duplicate or conflicting BEV bindings fail.
The boundary revalidates candidate content and artifact semantics; recomputing
outer hashes cannot legitimize changed inputs, components, score, version or
forecast association.

Already bound candidates require their existing exact artifact. Validation
preserves the candidate's version rather than replacing it with a newly
calculated version. Later ratings, scores, source availability or scoring
versions cannot rewrite an issued BEV. A pregame correction has its own
candidate and artifact and preserves the predecessor.

## Evidence states

A candidate and its artifact establish prepared content. They do not establish
that either was published. Issued BEV requires the existing verified forecast
publication capability, authenticated game timing, and the exact BEV bytes in
the same authenticated immutable publication package. A hash link, a bare
receipt supplied by a caller, or a package containing only the candidate does
not prove that the BEV artifact was public.

Selection follows the existing pregame correction chain. A late or unverified
correction cannot displace an earlier qualifying issued value. Historical
reconstruction stays explicitly reconstructed and outside recorded forecast
evaluation. Its input cutoff must be supported as pregame; the act of
reconstructing history need not occur before the historical game.

## Missing evidence and public bytes

Missing ranks make BEV unavailable rather than creating replacement ranks or a
score. The production binding boundary has no accepted market qualification
contract yet. Missing or historically ineligible market evidence therefore
contributes zero bonus with an explicit reason. Qualified-market formula tests
belong to the pure scorer; this boundary has no fixture-only qualification
bypass. Adding production market qualification requires reviewed source and
policy evidence.

Canonical artifact JSON contains safe derived identities, inputs, results and
provenance references. Existing public-output safety remains unchanged. Raw
provider responses, private paths and credentials do not enter the artifact.

This module prepares the source-independent binding seam. Release/table
wiring, live source qualification and AEV reference/whole-season empirical
acceptance remain separate parent delivery gates. Current readiness and
verification belong to SR-33 and its PR rather than this architecture note.
