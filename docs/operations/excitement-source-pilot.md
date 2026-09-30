# ADR-0021 source qualification pilot

`cfb.excitement_source` is the private, nine-attempt Season Snapshot
supplement adapter for the ADR-0021 data qualification pilot. It is separate
from the ranking snapshot and does not normalize play, quarter, or market
semantics. Each response is retained as exact bytes in `LocalInputStore` and
receipted by digest and request provenance.

The reviewed manifest is `config/excitement-pilot-v1.json` with SHA-256
`b612f6c155eb90ac7b7473efcbb6f8a984f7c65d754ed3b5809cd49e410c344d`. It is
the authority for the nine requests:

* `/plays`, filtered to one reviewed team and week for each of 2024 Georgia,
  2025 Ohio State, and 2026 Ohio State, with `classification=fbs` and
  `seasonType=regular`;
* `/games`, filtered by the corresponding reviewed game ID, year,
  `classification=fbs`, and `seasonType=regular`; and
* `/lines`, filtered by that same game ID, year, and `seasonType=regular`.

There is one attempt per exact endpoint/filter key, nine attempts total, and
zero retries or redirects. The request claim is committed in SQLite before
`RequestMeter.execute` enters transport. Failed, interrupted, meter-blocked,
and successful attempts all consume their claim. A restart or a different
allowance document cannot clear or reset that ledger; an interrupted claim
remains consumed and a restart stops before retrying that request.

## Offline plan

The dry run reads only the reviewed manifest. It does not construct a private
store, read `CFBD_API`, create a meter database, or open a network connection:

```sh
env -u CFBD_API -u CFBD_API_KEY \
  UV_CACHE_DIR=/tmp/adr21-uv-cache \
  uv run --locked python -m cfb.excitement_source dry-run \
  --pilot-config config/excitement-pilot-v1.json
```

The JSON output includes the frozen manifest digest, the nine safe endpoint and
filter records, and `remaining_attempts: 9`. That value is the frozen pilot's
initial cap, not a read of any existing claim ledger: dry-run never opens or
creates the private store or meter. Only an owner-authorized `acquire` reports
the ledger's actual remaining claim capacity. It contains no response bytes,
credentials, local store paths, or allowance values.

## Live command, owner allowance required

No allowance is present in this repository and this command has not been run.
An owner must provide a fresh JSON allowance document and a private source
binding before this operation is eligible. The adapter accepts this shape:

```json
{
  "allowance_id": "owner-issued-opaque-id",
  "pilot_manifest_sha256": "b612f6c155eb90ac7b7473efcbb6f8a984f7c65d754ed3b5809cd49e410c344d",
  "max_attempts": 9,
  "purpose": "historical"
}
```

The allowance ID and complete-document digest are durably recorded. Reusing an
ID with changed contents is rejected. A new ID can continue only the same
frozen pilot and remaining claim capacity; it cannot reset the total of nine.

The source binding is a safe receipt file, not a source path. It supplies one
`InputReference` for the pinned source archive and one for each parent snapshot:

```json
{
  "source_archive": {"role": "source-archive", "sha256": "86a27f80709549c48bfaca763f4e925b3168dbae232745d534986e6fee43da37", "size": 0},
  "snapshots": {
    "snapshots/cfb-fbs-2024.json": {"role": "snapshot-2024", "sha256": "81202378ba0a862a92a8e168e00f5df0f3c1827b3f5ac2876c7e8efd4d7a5633", "size": 0},
    "snapshots/cfb-fbs-2025.json": {"role": "snapshot-2025", "sha256": "8f9e919d81fbaf71a23c67cd2da73d59d9225513c115f08d5060bb1caf300f47", "size": 0},
    "snapshots/9bf66d0ccab3878c7926f17b44664644774eed8ea95a7ffa73b3a206eb45296a/cfb-fbs-2026.json": {"role": "snapshot-2026-native", "sha256": "8a99677a4c98e3bddfd5ee3d2f80b0eab4813ef8772cc6d9c47f74303f547d8a", "size": 0}
  }
}
```

The `size` values above are placeholders for documentation. The actual
allowance run must use the owner-only `LocalInputStore` receipts with their
verified byte counts. The adapter verifies every receipt against the frozen
manifest before claiming any request, while preserving the original source
archive and snapshot objects unchanged.

With the owner-approved files and a dedicated private store, the command is:

```sh
env -u CFBD_API_KEY \
  UV_CACHE_DIR=/tmp/adr21-uv-cache \
  uv run --locked python -m cfb.excitement_source acquire \
  --pilot-config config/excitement-pilot-v1.json \
  --allowance /private/owner/excitement-allowance.json \
  --source-binding /private/owner/excitement-source-binding.json \
  --store /private/owner/sportsrank-inputs \
  --meter /private/owner/sportsrank-inputs/cfbd_requests.sqlite3
```

The command must be run only after the owner grants the documented allowance
and supplies `CFBD_API` through BB. The adapter never accepts a credential as a
command argument. Every network call uses a one-shot standard-library opener
with the redirect handler replaced by a fail-closed handler; the SDK is not
used for this supplement, so its retry behavior cannot add hidden attempts.

The private claim ledger is derived from the pilot ID under the supplied
owner-only store. Response objects are retained under content-addressed names
with mode `0600`. A safe `SupplementReceipt` contains request filters,
response SHA-256 and size, capture time, allowance ID, source archive digest,
and parent snapshot path/digests. It never contains raw response data, an
authorization header, or a filesystem path.

## Public Python interface

Tests and offline callers can inject a fake transport with this interface:

```python
from collections.abc import Callable
from cfb.excitement_source import (
    PilotAdapter,
    PilotAllowance,
    PilotManifest,
    PilotRequest,
    SourceBinding,
)

Transport = Callable[[PilotRequest], bytes]
manifest = PilotManifest.load("config/excitement-pilot-v1.json")
adapter = PilotAdapter(meter, store, manifest=manifest, transport=fake_transport)
result = adapter.acquire(allowance, source_binding)
```

`PilotAdapter.dry_run()` is side-effect free. `PilotAdapter.acquire()` requires
both a validated `PilotAllowance` and a `SourceBinding`; missing, changed, or
wrongly pinned inputs fail closed before transport. Synthetic fake-transport
tests establish the cap and retention behavior only. They are not evidence of
CFBD timeline/quarter/market coverage or whole-season AEV acceptance.
