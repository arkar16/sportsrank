# SportsRank

SportsRank publishes the College Football (CFB) CORS rankings as static HTML
under `website/`. Firebase Hosting serves that directory at the existing
public URLs; the public site has no runtime Python or Node dependency.

## Install and verify

Use Python 3.12 and install the locked environments:

```sh
uv sync --locked
npm ci
npm run build
uv run python -m unittest discover -s tests -v
```

`npm run build` checks the static hosting tree. The hosting scripts are
explicitly hosting-only:

```sh
npm run serve    # local Firebase Hosting emulator
npm run deploy   # explicit Firebase Hosting publication
```

Do not run `npm run deploy` until a candidate Release has passed offline
validation and a production reviewer has approved publication. The GitHub
workflow is manual (`workflow_dispatch`): select a reviewed ref whose tracked
`website/` tree contains the promoted candidate, and its protected
`production` environment gates the live publication.

## Recovery CLI

The staged CFB recovery interface keeps fetching separate from rendering,
validation, and promotion. `fetch` stores a metered Season Snapshot; `build`
creates a candidate under a staging directory; `validate` is offline; and
`promote` is the explicit local publication step.

```sh
export SPORTSRANK_DATA_DIR="$PWD/.sportsrank"
uv run python -m cfb.recovery --help
uv run python -m cfb.recovery smoke 2026
uv run python -m cfb.recovery fetch 2024 --classification FBS
uv run python -m cfb.recovery build 2024 --classification FBS \
  --release-id 2024-recovery --output-root "$PWD/.sportsrank/releases"
uv run python -m cfb.recovery validate \
  "$PWD/.sportsrank/releases/2024-recovery"
```

The smoke command makes one authenticated teams request. All other recovery
commands use the persistent cache where possible. For the credential-safe
morning procedure and the required 2024 → 2025 → 2026 order, see
[`docs/operations/2026-season-recovery-morning.md`](docs/operations/2026-season-recovery-morning.md).

## Credentials

The production CFBD adapter reads the bearer token only from the local
`CFBD_API_KEY` environment variable. Enter it in a local terminal when needed;
never put the real value in source, arguments, generated pages, fixtures,
tracked environment files, chat, or logs.

The Firebase service account is supplied only through the protected GitHub
production environment. It is not required for local build or validation.
