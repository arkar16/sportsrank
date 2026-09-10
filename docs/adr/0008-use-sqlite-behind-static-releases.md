---
status: accepted
---

# Use SQLite behind static Releases

SportsRank will move structured source data, calculated rankings, spreads, and publication metadata into SQLite on the Raspberry Pi while continuing to publish static files to Firebase. The database is generation-time state rather than a website runtime dependency; generated HTML will leave Git only after database backups, immutable Release archives, Firebase publication, and URL compatibility have been proven end to end.
