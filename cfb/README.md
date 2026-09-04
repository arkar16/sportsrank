# CFB recovery and rankings

The CFB boundary currently supports FBS data from CFBD and renders static
HTML. The supported recovery entry point is `python -m cfb.recovery`; it keeps
external fetching, cached snapshots, Release generation, validation, and local
promotion as separate stages.

## Local setup

From the repository root:

```sh
uv sync --locked
export SPORTSRANK_DATA_DIR="$PWD/.sportsrank"
```

Set `CFBD_API_KEY` only in the local terminal that performs a fetch. The
adapter reads it as a bearer token and the request meter stores credential-free
audit rows in `$SPORTSRANK_DATA_DIR/cfbd_requests.sqlite3`.

## Supported commands

```sh
uv run python -m cfb.recovery --help
uv run python -m cfb.recovery smoke YEAR
uv run python -m cfb.recovery fetch YEAR --classification FBS
uv run python -m cfb.recovery refresh YEAR --classification FBS
uv run python -m cfb.recovery build YEAR --classification FBS \
  --release-id RELEASE_ID --output-root "$PWD/.sportsrank/releases"
uv run python -m cfb.recovery validate CANDIDATE_PATH --json
uv run python -m cfb.recovery promote CANDIDATE_PATH website
```

`smoke` performs exactly one metered teams request. A first `fetch` normally
uses one teams request and one games request; cached `build`, `validate`, and
`promote` operations do not call CFBD. `refresh` reuses cached teams and
refreshes games only. A failed request is recorded without exposing the API
key.

`build` writes only to its staging output. Review its manifest and run
`validate` before the explicit `promote` step. Promotion is local and should
be followed by committing the reviewed `website/` tree, then using the
protected, manual Firebase Hosting workflow only after production approval.
That workflow validates and deploys the checked-out `website/` path from the
selected ref; it cannot see a Pi-local `.sportsrank/releases` path.

The older `cfb/main.py` script and batch file are retained for historical
compatibility; new recovery work should use the staged interface above.
