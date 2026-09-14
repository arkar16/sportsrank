---
status: accepted
---

# Preserve Firebase-managed resource behavior

Treat exactly `/__/firebase/init.js` and `/__/firebase/init.json` as
Firebase-managed resources: retain their original bytes and hashes in the
complete baseline evidence, preserve their public availability and Firebase
project/app association, and verify their generated configuration after
publication. Firebase may regenerate their bytes; copying the captured files
into the application tree cannot control the reserved Hosting responses.

This is a narrow exception to byte preservation, not to URL preservation.
ADR-0011's independent ownership and unchanged-content checks continue to apply
to every SportsRank application file. No namespace-wide exclusion is allowed;
any other unexplained live-only resource blocks acceptance.

Accepted by the owner in **Define the reviewable PR completion scope and release
evidence** (SR-5), following complete capture in SR-6. Firebase documents the
[reserved initialization resources](https://firebase.google.com/docs/hosting/reserved-urls)
and their [serving priority](https://firebase.google.com/docs/hosting/full-config#hosting_priority_order).
