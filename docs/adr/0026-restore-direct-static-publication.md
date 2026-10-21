---
status: accepted
---

# Restore direct static publication

On October 20, 2026 the owner explicitly selected changed-output validation and
direct publication, and authorized implementation and publication of the current
website. The recovery release ceremony had made routine updates take over an
hour while the public site remained behind. Restore the earlier main-push
Firebase Hosting workflow: review the changed output, deploy the committed
`website/`, then check a few updated public pages against those bytes.

This supersedes ADR-0012's separate artifact/approval gates and ADR-0016/0018's
sealed predecessor, two-dispatch, immutable claim/archive and full live-inventory
requirements for routine publishing and this authorized recovery. Broad offline
CI remains independent of deployment; receipt regeneration is not a deployment
or CI prerequisite. The owner subsequently explicitly approved permanently removing GitHub's
required production reviewer so future main deployments run without an approval
prompt. The production environment retains its main-only branch restriction,
private deployment secret and disabled administrator bypass. Code changes,
unrelated merges and future exceptional recovery still require owner authority.

Preserve the CORS model, public URLs, original forecasts and private source data.
Source acquisition remains metered and separately authorized. Git and Firebase
release history provide ordinary recovery context; old sealed evidence is
historical and does not attest these direct releases. Do not reuse consumed or
superseded sealed attempts. Add useful deterministic checks incrementally rather
than making exhaustive historical verification a weekly publication prerequisite.
This decision does not activate a weekly generator or select hosted storage.

[Issue #68](https://github.com/arkar16/sportsrank/issues/68) owns delivery and live
acceptance. The [publication runbook](../operations/direct-publication.md) owns
current commands.
