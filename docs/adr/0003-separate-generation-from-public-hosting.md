---
status: accepted
---

# Separate scheduled generation from public hosting

The Raspberry Pi will run fully automated Ranking Runs through `systemd` while Firebase Hosting remains the public origin for SportsRank. OpenClaw is another workload on the Pi, not a dependency of the ranking system. This keeps the free managed CDN and avoids exposing a home device to inbound traffic, while still giving SportsRank a controlled machine for scheduled data retrieval, validation, and publication.
