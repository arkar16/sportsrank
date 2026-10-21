# Publish the static website

Review the changed rankings/results/forecasts against the intended data and
inspect the diff for unexpected edits or deletions. Commit the intended generated
`website/` files through a reviewed PR. Preserve historical URLs and issued
forecasts. Source acquisition is separate; publishing makes zero CFBD calls.

A push to `main` changing the website, hosting configuration/dependencies or the
publication workflow runs `Publish static site to Firebase Hosting`. To publish
the existing main website without a new data commit:

```sh
gh workflow run firebase-hosting-publish.yml --ref main
```

The single job checks out the triggering SHA, installs the locked Firebase CLI,
checks the hosting entry files and deploys only Hosting to `sportsrank-837af`.
The production environment retains its main-only policy and private deployment
secret. The owner explicitly removed the required reviewer: main deployments
proceed automatically without a pending-deployment approval call. Administrator
bypass remains disabled.
Broad portable CI is independent and does not gate deployment.

After publication the job compares five current pages against the committed
bytes: season navigation, latest weekly rankings, latest weekly results, latest
forecast and current-season performance. It records the commit in the run summary.
These are brief product checks, not a full inventory audit. Record the run and
public links in the delivery issue. A failed page check may follow a successful
deploy: inspect the actual updated pages and Firebase release before another
write; never automatically roll back. An explicit authorized republish is a new
ordinary deployment, not a replay of an old sealed attempt.

Git retains reviewed website versions and Firebase retains deployment history.
If rollback is needed, obtain owner direction and deploy the selected reviewed
website version through a new commit. Old preparation/seal/execute workflows and
receipts are historical evidence, not current publication prerequisites. See
[ADR-0026](../adr/0026-restore-direct-static-publication.md).

This workflow publishes committed files; it does not fetch results, generate
rankings, submit contest picks or schedule weekly generation. The T3 timer was
deleted October 18; #54's hosted generation replacement remains separate.
