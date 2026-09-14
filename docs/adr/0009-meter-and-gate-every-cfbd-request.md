---
status: accepted
---

# Meter and gate every CFBD request

SportsRank will use one CFBD key dedicated to CFB and route every request through the Season Snapshot module's metered adapter. The Request Meter will attribute and gate calls before HTTP begins, initially reserving 100 monthly calls for scheduled production, 500 for historical maintenance, and stopping all application traffic at 2,500 of the 3,000-call allowance; these internal limits may be tuned from observed weeks without raising the external ceiling. Teams are fetched at Season initialization, downstream calculations and post-fetch retries reuse snapshots, ordinary tests make zero live calls, and failures expose their stage and cause without logging credentials. SportsRank will not spend a request on automatic weekly usage reconciliation while the local meter is being established; external usage may be checked manually during calibration.
