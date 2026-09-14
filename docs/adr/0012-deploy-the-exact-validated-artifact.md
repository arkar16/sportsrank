---
status: accepted
---

# Deploy the exact validated artifact

Production publication will deploy the same content-addressed static-site artifact that passed validation for an immutable commit SHA, with separate approval gates before remote review and before Firebase production. Mutable-ref re-checkouts and direct hosting commands are not publication paths because they can deploy content other than what reviewers and validators approved.
