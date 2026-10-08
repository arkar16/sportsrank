---
status: accepted
---

# Bind publication to verified live content

The first recovery Publication may use a complete verified capture of the
current Firebase live release/version, files and serving configuration as its
base even when the original source commit is unknown. Record that uncertainty
explicitly; an import commit identifies the capture, not historical authorship.
Incomplete capture or unexplained differences block publication. This preserves
the complete-overlay requirement in ADR-0011 without inventing a deployed SHA.

Firebase's current live release/version identifies what is deployed. Bind it to
the preserved content-addressed artifact and provenance, and record verification
separately from deployment. Reconcile actual provider state after an interrupted
attempt and reject stale or unknown bases. Failed post-deployment verification
pauses ordinary publication for owner-directed recovery; it does not erase the
deployment or trigger automatic rollback. The owner accepts that the suspect
release may remain live until recovery is approved. ADR-0006 archive retention
and ADR-0012 exact-artifact publication continue to apply.

Production requires approval by the owner's existing account, including runs
that account initiated. Disable approval bypass, restrict publication to the
`main` workflow branch, and make deployment credentials available exclusively
behind the production gate. Agents never approve on the owner's behalf. This
uses account-based enforcement plus an operational human-approval boundary;
separating agent and owner identities is deferred.

Accepted in **Choose first-publication and durable deployed-state behavior**
(SportsRank task SR-3). This decision replaces the historical requirement for a
known deployed Git SHA and successful-workflow-derived production state; it does
not establish the baseline or implement the publication path. The recovery
contract and publication runbook own acceptance and operator procedure.

[ADR-0017](0017-preserve-firebase-managed-resource-behavior.md) records the
accepted treatment of Firebase initialization resources;
[ADR-0018](0018-retain-publication-evidence-in-github-releases.md) selects the
permanent archive and receipt mechanism. Neither establishes implementation or
production approval.

## Evidence-backed successors — 2026-10-08

For an ordinary successor, retain the original provider capture as historical
URL/byte preservation authority. Derive a distinct `verified_successor_baseline`
from the authenticated prior sealed publication package and complete immutable
coordinator reconciliation/verification. Bind its exact sealed reference,
candidate commit, artifact inventory, serving configuration, observed live
release/version and immutable audit references. This is package/audit provenance,
not a new Firebase capture; do not change the historical `BaselineRecord` or
invent capture observations.

Local export validates historical preservation independently, then checks that
every prior public path remains and that bytes outside independently validated
run ownership remain unchanged. The reviewed receipt binds both authorities.
Hosted preparation reauthenticates the successor evidence and exact candidate
receipt. Before sealing and execution, freshly reconcile the same prior sealed
attempt; it must still verify the package and equal the receipt's predecessor.
Execution still rejects stale live identity after acquiring its single-use claim.
An archived audit alone does not establish current live state or allow replay.

The owner approved this narrow repair in
[#27](https://github.com/arkar16/sportsrank/issues/27). New capture transports,
credential paths, retention policies, CORS changes, merges and production
dispatches are outside the implementation authority. ADR-0018 and the recovery
contract record the corresponding receipt and operational requirements.
