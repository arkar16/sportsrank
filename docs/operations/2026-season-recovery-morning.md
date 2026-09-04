# 2026 season recovery — morning handoff

This is a local, operator-reviewed procedure. It does not run a live fetch or
publish anything by itself. Stop at the first failed command or unexpected
call count and coordinate with the release owner; do not invent replacement
flags or bypass validation.

## 1. Enter the key locally, without echoing or recording it

From the repository root, use a terminal that is not recording a transcript.
The following zsh prompt disables input echo; type the project-only key when
prompted. Do not paste the key into chat, a shell command, a tracked file, or
an issue, and do not enable shell tracing.

```zsh
read -r -s "CFBD_API_KEY?CFBD project key (hidden; local terminal only): "
printf '\n'
export CFBD_API_KEY
export SPORTSRANK_DATA_DIR="$PWD/.sportsrank"
```

The repository's `.env.example` is documentation only and must retain a
placeholder. The real key is read only from `CFBD_API_KEY`; clear it with
`unset CFBD_API_KEY` when the local session is finished.

## 2. Run exactly one authenticated smoke test

Run this once after entering the key:

```sh
uv sync --locked
uv run python -m cfb.recovery smoke 2026 --classification FBS --category scheduled
```

Expected result: JSON reports `"stage": "smoke"` and `"calls": 1`. This is
one metered teams request. A budget block, transport error, or any count other
than one is a stop condition; do not retry the smoke test until the audit has
been inspected. Normal `fetch` may use one teams call plus one games call on a
new season, subject to the configured meter budget.

## 3. Inspect the meter and check for redaction

The audit database contains request metadata and outcomes, never the key or an
authorization header. Inspect only these credential-free columns:

```sh
sqlite3 "$SPORTSRANK_DATA_DIR/cfbd_requests.sqlite3" \
  'SELECT id, requested_at, category, purpose, endpoint, season, cache_decision, budget_impact, outcome, error_type FROM request_audit ORDER BY id;'
```

Confirm that the smoke row is a single `teams` request with
`cache_decision = 'miss'`, `budget_impact = 1`, and `outcome = 'succeeded'`.
Then scan the local meter/cache directory for accidental credential material;
no output is expected:

```sh
if rg -n -i --hidden 'CFBD_API_KEY|Authorization:|Bearer[[:space:]]|access[_-]?token|api[_-]?key' "$SPORTSRANK_DATA_DIR"; then
  echo "Potential credential leakage; stop and rotate the key." >&2
  exit 1
fi
```

Do not paste the audit database or scan output containing sensitive data into
chat. If the key appears anywhere, stop, remove the local artifact safely,
rotate the key, and coordinate before continuing.

## 4. Dry-run and validate in recovery order

These commands use the supported `cfb.recovery` interface. Verify the checked
out command surface with `uv run python -m cfb.recovery --help` before the live
run. If it differs from this handoff, stop and coordinate; do not guess at
flags. Every `build` below writes a candidate under `.sportsrank/releases`;
none publishes the public site.

```sh
RELEASE_ROOT="$PWD/.sportsrank/releases"
PUBLISHED_SITE="$PWD/website"
mkdir -p "$RELEASE_ROOT"
```

Complete and validate 2024 first:

```sh
uv run python -m cfb.recovery fetch 2024 --classification FBS
uv run python -m cfb.recovery build 2024 --classification FBS \
  --release-id 2024-recovery --output-root "$RELEASE_ROOT" \
  --published-site "$PUBLISHED_SITE" --clone-published
uv run python -m cfb.recovery validate "$RELEASE_ROOT/2024-recovery" --json
```

Then carry the corrected 2024 final into 2025:

```sh
uv run python -m cfb.recovery fetch 2025 --classification FBS
uv run python -m cfb.recovery build 2025 --classification FBS \
  --release-id 2025-recovery --output-root "$RELEASE_ROOT" \
  --published-site "$RELEASE_ROOT/2024-recovery/site" --clone-published
uv run python -m cfb.recovery validate "$RELEASE_ROOT/2025-recovery" --json
```

Finally generate 2026 Week 0 from the corrected 2025 final:

```sh
uv run python -m cfb.recovery fetch 2026 --classification FBS
uv run python -m cfb.recovery build 2026 --classification FBS --week 0 \
  --release-id 2026-recovery --output-root "$RELEASE_ROOT" \
  --published-site "$RELEASE_ROOT/2025-recovery/site" --clone-published
uv run python -m cfb.recovery validate "$RELEASE_ROOT/2026-recovery" --json
```

For each candidate, inspect `manifest.json`, `metadata.json`, and the JSON
validation report. Confirm deterministic output, complete FBS membership,
contiguous ranks, finite CORS/record/spread values, reconciled completed
games, resolved internal links, timestamps, and preserved historical pages.
Cached `build` and `validate` operations must make zero CFBD calls.

## 5. Approval and publication gate

Do not promote or deploy until the 2024, 2025, and 2026 validation reports are
green and a production reviewer explicitly approves the candidate. Record the
candidate IDs and validation results in the operator's approved change record,
without including credentials.

After approval, the local promotion is explicit and atomic:

```sh
uv run python -m cfb.recovery promote \
  "$RELEASE_ROOT/2026-recovery" "$PUBLISHED_SITE"
npm ci
npm run build
```

The final public step is the manually dispatched **Publish validated static
site** GitHub workflow. First commit the reviewed, promoted `website/` tree
and its Release metadata on the ref that will be published. In the
workflow-dispatch form, select that reviewed ref; the workflow deliberately
validates the checked-out `website/` path and deploys that same path, so a
Pi-local `.sportsrank/releases/...` path is never passed to a runner. Its
validation job must pass before the protected `production` environment can
approve the Firebase Hosting live publication. There are no push,
pull-request, scheduled, or automatic preview triggers. Do not use a Firebase
command or action to publish before that approval.

## 6. Raspberry Pi and Samsung T7 prerequisites

Before production scheduling, the operator still needs to confirm all of the
following outside this repository:

- Raspberry Pi OS, a supported Python 3.12 runtime/`uv`, Node.js 22/npm, and a
  clean checkout with `uv.lock` and `package-lock.json` available;
- outbound HTTPS access to CFBD and Firebase, with clock/time-zone configured
  for `America/Indiana/Indianapolis` (or an explicitly selected Eastern zone);
- the Samsung T7 mounted at a stable path with ownership and free-space
  checks, then separate directories for snapshots, request audit, staged
  Releases, published-site backups, and logs;
- the project-only CFBD key provisioned through a host secret mechanism or a
  mode-0600 environment file outside the checkout; and
- a protected Firebase service account available to the GitHub production
  environment, not to source files or generated pages.

Only after those prerequisites and a tested manual run should the operator
provision a later `systemd` service and timer: Monday 06:00 Eastern with
explicit 07:00 and 09:00 retries, nonzero failure handling, last-known-good
preservation, and logs that exclude credentials. The timer is intentionally
not installed by this handoff.

## 7. Decisions and blockers still open

- Off-device backup policy, retention, restore testing, and whether Google
  Drive through the Pi/OpenClaw environment is acceptable are undecided.
  Do not upload data or credentials until an owner approves the destination
  and access model.
- A CFBD key is required for the one smoke request and all uncached fetches.
- GitHub production-environment reviewers and the Firebase service-account
  secret are required for public publication.
- Pi access, T7 mount/permissions, network egress, and later `systemd`
  provisioning require external operator work.
- The checked-out recovery CLI must match the commands above before executing
  the ordered catch-up. Do not bypass the staged Release, validation, or CORS
  and historical-URL contracts if the interface changes.
