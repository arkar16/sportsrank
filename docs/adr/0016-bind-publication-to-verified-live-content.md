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
