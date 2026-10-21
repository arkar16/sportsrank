---
status: accepted
---

# Deploy the exact validated artifact

October 20 publication update: [ADR-0026](0026-restore-direct-static-publication.md) supersedes the routine
release procedure below. Historical evidence and unrelated domain/private-input
constraints remain; use the direct-publication runbook for new deployments.


Production publication will deploy the same content-addressed static-site artifact that passed validation for an immutable commit SHA, with separate approval gates before remote review and before Firebase production. Mutable-ref re-checkouts and direct hosting commands are not publication paths because they can deploy content other than what reviewers and validators approved.
