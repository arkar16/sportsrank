---
status: accepted
---

# Separate scheduled generation from public hosting

The Raspberry Pi will run automated Ranking Runs while Firebase Hosting remains the public origin for SportsRank. This keeps the free managed CDN and avoids exposing a home device to inbound traffic, while still giving SportsRank a controlled machine for scheduled data retrieval, validation, and publication.
