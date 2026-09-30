# Excitement source qualification

The excitement source boundary converts immutable retained game metadata and
play captures into evidence for the accepted [scoring-v1 contract](../plans/2026-game-excitement-scoring-v1.md).
It does not fetch provider data, change Season Snapshots, select market policy,
fit reference artifacts or publish a Release. The existing scorer owns the
calculation of BEV, full AEV and comparable-game estimates.

## Input trust and correspondence

The caller supplies retained response bytes and the corresponding source
receipts. Qualification verifies byte identities and the declared request,
source and game correspondence before interpreting a capture. A content hash
establishes which bytes were supplied; it does not independently prove who
supplied them or whether a caller's source assertion is true. Production callers
must obtain receipts through the reviewed immutable supplement boundary and
use the qualified target roster.

Original snapshot bytes remain unchanged. Supplemental provider phase and week
metadata retain their own capture identity. A normalized historical week is
not substituted for a provider request week. Conflicting game identities,
orientations, source bindings or verified results must be reconciled before
they can become scoring inputs.

## Timeline qualification

Full AEV needs a supported score path across all 60 minutes of regulation,
ordered score transitions and verified overtime occurrence. Raw array order is
not sufficient. Provider event identity and ordering evidence must support
clock and score interpretation, including whether a score applies after the
event. An arbitrary identifier tie-break cannot resolve an ambiguous scoring
sequence.

Same-clock score transitions have no regulation duration, but their supported
order remains relevant to lead changes and comebacks. A missing standalone
15:00 row in a later quarter is not itself a missing interval: a verified
preceding quarter-end score can carry to the next observed change when source
semantics support that interpretation. Qualification must still establish
continuous regulation support; it cannot manufacture missing events, boundary
observations or elapsed time.

Overtime contributes ordered score changes and a binary occurrence flag. A
provider may collapse multiple overtime periods into one untimed period label.
That discrepancy remains part of qualification evidence; individual overtime
period boundaries are not required by scoring-v1, and no overtime elapsed
regulation time is invented.

## Reduced evidence and unavailable states

A failed full-timeline check does not create an invented full score. Valid
quarter checkpoints or a verified normal completed final may support the
existing comparable-game path. Unknown overtime remains unknown. Missing,
contradictory, shortened, abandoned or unsupported game-format evidence must
remain explicit rather than becoming default zero scores or an assumed normal
60-minute game.

Qualified reduced inputs do not establish that a useful reference artifact
exists. Reference eligibility, frozen scales, sufficient support and numerical
whole-season acceptance remain separate gates owned by the scoring/reference
delivery contract.

## Private evidence and safe summaries

Raw provider payloads, play text and private storage paths stay private.
Qualification summaries expose only intended derived status, source identity
and reasons. Serialized derived artifacts retain semantic correspondence checks;
regenerating an outer digest cannot legitimize contradictory qualification
fields. Public summaries must pass the unchanged public-output safety boundary.

Synthetic fixtures verify these software rules. Real retained captures require
separate qualification, and neither establishes empirical viewing-quality or
estimated-score acceptance. Current coverage, partition decisions, verification
and outstanding integration work belong to the linked delivery tasks.

## Observed-data policy boundary

The initial `cfbd-score-trajectory-v1` policy, version `1`, is recorded with SR35
and pinned by SHA-256
`5166109215b2a919160e2a2a8d79e4a595e01a854d7f15f8a6f42e29944963db`.
Its accepted inference boundary combines exact request coverage and retained
bytes, supported event ordering and after-play score interpretation, independent
quarter/final agreement, and supported normal-game clock/format facts. A caller
must bind the actual policy digest, evidence reference and per-game decisions
to the captures; a boolean or self-sealed digest alone does not qualify a source.
The v1 qualifier accepts only that exact policy tuple. Its source identity also
binds the caller's literal score-semantics, completeness, regulation-duration
and overtime assertions, so changing an assertion produces a different source
identity even when the retained response bytes are unchanged. The target's
completion and game-format assertions are bound the same way.

Completeness means sufficient score-trajectory coverage, not every non-scoring
play. Supported constant-score carry may establish a quarter endpoint without
a literal 0:00 row; this is recorded as an inference, not a provider observation.
A scoring flag cannot explain a single event increasing both teams' cumulative
scores. Such an aggregate transition is rejected under the initial policy.

Agreement with independent totals cannot logically exclude every possible
unobserved intermediate transition. This residual provider omission risk is a
limit of the observed-data method, not permission to waive a detected gap or
contradiction. Missing SDK warranty language alone is not a rejection rule.
Coverage, rejections and inference use must be reported before accepting any
reference roster. Approval of this policy does not qualify a particular game
or accept empirical thresholds.

A capture explicitly reviewed as partial or of unknown completeness can still
carry an independently verified completed final into the reduced tier. Missing
terminal or quarter-boundary rows in such a capture are unavailable evidence,
not contradictions. An observed boundary that conflicts with the independent
line score remains invalid. Constant-score boundary carry is available only
for a capture reviewed as complete.

Unknown score timing semantics also remain reduced. The qualifier still checks
observable response, game and team correspondence, but it does not apply
after-play-specific transition rules until that interpretation has been
reviewed. This prevents an unqualified timing assumption from erasing an
independently verified normal completed final.
