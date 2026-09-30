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

The history loader accepts at most 256 independently authenticated publication
attempts. It retains each candidate’s actual publication receipt and requires
the bound BEV bytes in that same package. A later package cannot supply missing
BEV bytes to an earlier receipt. Copies of predecessors in a correction package
do not establish earlier publication.

Selection follows the existing pregame correction chain across these distinct
attempts. A late correction cannot displace an earlier qualifying issued value.
Ordinary rebuilds retain the earliest qualifying receipt and exact artifact;
republishing an ancestor cannot roll back a child. Conflicting or unverified
history fails closed. Historical
reconstruction stays explicitly reconstructed and outside recorded forecast
evaluation. Its input cutoff must be supported as pregame; the act of
reconstructing history need not occur before the historical game.

Reconstruction consumes an explicitly caller-qualified input value. It binds
the exact rating checkpoint, cutoff label, rating artifact and source snapshot
digests, model and forecast anchor, semantic input cutoff time, and source
evidence reference. Every field must correspond to the candidate before the
separate game-timing comparison can qualify that cutoff. An older qualification
for different inputs cannot be reused merely because its timestamp is pregame.
The same correspondence check applies to artifact construction and wire readers,
so regenerating an outer digest cannot legitimize altered qualification fields.

The external caller must qualify the retained source/checkpoint evidence.
Neither the typed value nor its content hash independently proves source truth
or when those inputs existed. This pure module does not fetch or interpret the
referenced evidence bytes. Public reconstruction integration must use the
reviewed source-byte/checkpoint loader and demonstrate that qualification
boundary; a self-declared timestamp or hash is insufficient. This requirement
is separate from the immutable-package authentication used for issued BEV.

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
