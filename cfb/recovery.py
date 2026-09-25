"""Safe staged CFB recovery and backfill commands.

Fetching, rendering, validation, and promotion are deliberately separate
operations.  The P0 backfill command uses the normal Season Snapshot cache and
Request Meter, verifies the expected six-request ledger after every stage, and
builds a chained candidate below a staging directory without touching the
tracked ``website/`` tree.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping

try:
    from .cfbd_client import get_snapshot_service, redact_api_key
    from .release import (
        Release,
        build_release,
        promote_release,
        validate_release,
        validate_release_chain,
    )
    from .request_meter import RequestMeter
    from .recovery_inputs import RecoveryInputBundle
    from .season_snapshot import (
        SeasonSnapshot,
        SeasonSnapshotService,
        migrate_postseason_cache,
    )
    from .season_source import FixtureSeasonSource, ProductionSeasonSource
    from .snapshot_cache import SnapshotCache
except ImportError:  # pragma: no cover - direct execution compatibility
    from cfbd_client import get_snapshot_service, redact_api_key
    from release import (
        Release,
        build_release,
        promote_release,
        validate_release,
        validate_release_chain,
    )
    from request_meter import RequestMeter
    from recovery_inputs import RecoveryInputBundle
    from season_snapshot import SeasonSnapshot, SeasonSnapshotService, migrate_postseason_cache
    from season_source import FixtureSeasonSource, ProductionSeasonSource
    from snapshot_cache import SnapshotCache


PHASES = ("preseason", "week", "final")
COMMANDS = {
    "smoke",
    "prime",
    "fetch",
    "refresh",
    "build",
    "backfill",
    "p0",
    "recover",
    "validate",
    "promote",
    "migrate-postseason",
}


@dataclass(frozen=True)
class ExpectedRequest:
    """Credential-free identity of one request in the approved P0 sequence."""

    category: str
    season: int
    endpoint: str
    cache_decision: str


P0_EXPECTED_REQUESTS: tuple[ExpectedRequest, ...] = (
    ExpectedRequest("scheduled", 2026, "teams", "miss"),
    ExpectedRequest("historical", 2024, "teams", "miss"),
    ExpectedRequest("historical", 2024, "games", "miss"),
    ExpectedRequest("historical", 2025, "teams", "miss"),
    ExpectedRequest("historical", 2025, "games", "miss"),
    ExpectedRequest("scheduled", 2026, "games", "miss"),
)
P0_EXPECTED_CALLS = P0_EXPECTED_REQUESTS
LAST_UPDATED_RE = re.compile(r"Last updated:\s*([^<\n]+)")


class RecoveryContractError(RuntimeError):
    """Raised when the staged recovery cannot prove its safety contract."""


@dataclass(frozen=True)
class P0BackfillResult:
    """The staged result returned before any local promotion or publication."""

    original_site: Path
    releases: tuple[Release, ...]
    snapshots: tuple[SeasonSnapshot, ...]
    audit_records: tuple[Mapping[str, object], ...]

    @property
    def candidate_site(self) -> Path:
        return self.releases[-1].site


def _timestamp(value: str | None) -> str:
    return value or datetime.now(timezone.utc).isoformat()


def _safe_message(error: BaseException) -> str:
    """Render an error without ever echoing the environment secret."""

    return redact_api_key(str(error))


def _service(
    args: argparse.Namespace, *, category: str | None = None
) -> SeasonSnapshotService:
    """Construct one service using the shared production cache/audit root."""

    category = category or getattr(args, "category", "scheduled")
    fixture_root = getattr(args, "fixture_root", None)
    cache_dir = getattr(args, "cache_dir", None)
    if fixture_root is not None:
        if cache_dir is None:
            raise ValueError("--cache-dir is required with --fixture-root")
        return SeasonSnapshotService(
            FixtureSeasonSource(fixture_root),
            SnapshotCache(Path(cache_dir)),
            clock=lambda: datetime.now(timezone.utc),
        )
    if cache_dir is not None:
        root = Path(cache_dir)
        return SeasonSnapshotService(
            ProductionSeasonSource(
                RequestMeter(root / "cfbd_requests.sqlite3"),
                category=category,
            ),
            SnapshotCache(root / "snapshots"),
            clock=lambda: datetime.now(timezone.utc),
        )
    return get_snapshot_service(category)


def _identity_args(parser: argparse.ArgumentParser, *, cache: bool = True) -> None:
    parser.add_argument("year", type=int)
    parser.add_argument("--classification", default="FBS")
    if cache:
        parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--fixture-root", type=Path)


def _source_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-input-root",
        type=Path,
        help="external bundle root containing inputs-manifest.json and snapshots/",
    )
    parser.add_argument(
        "--source-input-pin",
        action="append",
        default=[],
        metavar="PATH=SHA256",
        help="trusted raw source file pin; repeat once per manifest file",
    )
    parser.add_argument("--source-input-archive", type=Path)
    parser.add_argument("--source-input-archive-sha256")


def _source_inputs(args: argparse.Namespace) -> RecoveryInputBundle | None:
    root = getattr(args, "source_input_root", None)
    pins = getattr(args, "source_input_pin", None) or []
    archive = getattr(args, "source_input_archive", None)
    archive_sha256 = getattr(args, "source_input_archive_sha256", None)
    if root is None:
        if pins or archive is not None or archive_sha256 is not None:
            raise ValueError("source input pins require --source-input-root")
        return None
    parsed_pins: dict[str, str] = {}
    for value in pins:
        if not isinstance(value, str) or "=" not in value:
            raise ValueError("--source-input-pin must be PATH=SHA256")
        relative, digest = value.rsplit("=", 1)
        if not relative or not digest:
            raise ValueError("--source-input-pin must be PATH=SHA256")
        if relative in parsed_pins:
            raise ValueError(f"duplicate --source-input-pin: {relative}")
        parsed_pins[relative] = digest
    if not parsed_pins:
        raise ValueError("--source-input-root requires at least one --source-input-pin")
    return RecoveryInputBundle.from_directory(
        root,
        trusted_file_sha256=parsed_pins,
        archive=archive,
        expected_archive_sha256=archive_sha256,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cfb.recovery",
        description="Credential-safe staged CFB Season Snapshot and Release pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command_name in ("smoke", "prime"):
        prime = subparsers.add_parser(
            command_name,
            help="fetch and persist teams only; never fetch games or retry",
        )
        prime.add_argument("year", type=int)
        prime.add_argument("--classification", default="FBS")
        prime.add_argument("--category", default="scheduled")
        prime.add_argument("--cache-dir", type=Path)
        prime.add_argument("--fixture-root", type=Path)

    for name, help_text, refresh in (
        ("fetch", "fetch and cache one complete Season Snapshot", False),
        ("refresh", "refresh cached games and reuse cached teams", True),
    ):
        command = subparsers.add_parser(name, help=help_text)
        _identity_args(command)
        command.add_argument("--category", default="scheduled")
        command.add_argument("--required-week", type=int)

    build = subparsers.add_parser(
        "build", help="build one explicit phase from a complete cached snapshot"
    )
    _identity_args(build)
    build.add_argument("--phase", choices=PHASES, required=True)
    build.add_argument("--through-week", type=int)
    build.add_argument("--release-id", required=True)
    build.add_argument("--output-root", type=Path, default=Path("build/releases"))
    build.add_argument("--previous-final", type=Path)
    build.add_argument("--published-site", type=Path, required=True)
    build.add_argument("--timestamp")
    build.add_argument("--code-revision", default="working-tree")
    _source_input_args(build)

    backfill = subparsers.add_parser(
        "backfill",
        aliases=("p0", "recover"),
        help="run the six-call 2024/2025/2026 staged recovery",
    )
    backfill.add_argument("--classification", default="FBS")
    backfill.add_argument("--cache-dir", type=Path)
    backfill.add_argument("--fixture-root", type=Path)
    backfill.add_argument("--published-site", type=Path, default=Path("website"))
    backfill.add_argument(
        "--output-root", type=Path, default=Path(".sportsrank/releases")
    )
    backfill.add_argument("--timestamp")
    backfill.add_argument("--code-revision", default="working-tree")

    validate = subparsers.add_parser(
        "validate", help="strictly validate one candidate Release offline"
    )
    validate.add_argument("candidate", type=Path)
    validate.add_argument("--published-site", type=Path)
    validate.add_argument("--json", action="store_true", dest="as_json")
    _source_input_args(validate)

    promote = subparsers.add_parser(
        "promote", help="explicitly validate then atomically promote a local Release"
    )
    promote.add_argument("candidate", type=Path)
    promote.add_argument("published_site", type=Path)
    promote.add_argument(
        "--validation-base",
        type=Path,
        help="independent Published Site used for validation; defaults to destination",
    )
    _source_input_args(promote)

    migration = subparsers.add_parser(
        "migrate-postseason",
        help="copy immutable schema-3 snapshots into a pinned phase-aware root",
    )
    migration.add_argument("--source-root", type=Path, required=True)
    migration.add_argument("--destination-root", type=Path, required=True)
    migration.add_argument("--classification", default="FBS")
    migration.add_argument(
        "--season",
        dest="seasons",
        action="append",
        type=int,
        help="season to migrate; repeat for multiple seasons (defaults to 2024,2025,2026)",
    )
    _source_input_args(migration)

    return parser


def _meter_for(service: SeasonSnapshotService) -> RequestMeter | None:
    meter = getattr(getattr(service, "source", None), "meter", None)
    return meter if isinstance(meter, RequestMeter) else None


def _assert_shared_boundaries(
    scheduled: SeasonSnapshotService, historical: SeasonSnapshotService
) -> None:
    scheduled_cache = Path(scheduled.cache.root).resolve()
    historical_cache = Path(historical.cache.root).resolve()
    if scheduled_cache != historical_cache:
        raise RecoveryContractError(
            "scheduled and historical services do not share the snapshot cache root"
        )
    scheduled_meter = _meter_for(scheduled)
    historical_meter = _meter_for(historical)
    if (scheduled_meter is None) != (historical_meter is None):
        raise RecoveryContractError(
            "scheduled and historical services do not share the same audit boundary"
        )
    if scheduled_meter is not None and historical_meter is not None:
        if Path(scheduled_meter.path).resolve() != Path(historical_meter.path).resolve():
            raise RecoveryContractError(
                "scheduled and historical services do not share the audit root"
            )


def _record_identity(record: Mapping[str, object]) -> tuple[object, ...]:
    return (
        record.get("category"),
        record.get("purpose"),
        int(record.get("season", -1)),
        record.get("endpoint"),
        record.get("cache_decision"),
        record.get("budget_impact"),
        record.get("outcome"),
        record.get("error_type"),
    )


def _expected_identity(expected: ExpectedRequest) -> tuple[object, ...]:
    return (
        expected.category,
        expected.category,
        expected.season,
        expected.endpoint,
        expected.cache_decision,
        1,
        "succeeded",
        None,
    )


def _assert_audit_prefix(
    meter: RequestMeter, expected: Iterable[ExpectedRequest]
) -> list[dict[str, object]]:
    expected_rows = tuple(expected)
    records = meter.audit_records()
    if len(records) != len(expected_rows):
        raise RecoveryContractError(
            "request audit count mismatch: "
            f"expected {len(expected_rows)}, observed {len(records)}"
        )
    for index, (record, wanted) in enumerate(zip(records, expected_rows, strict=True), 1):
        if _record_identity(record) != _expected_identity(wanted):
            raise RecoveryContractError(
                f"request audit mismatch at position {index}: "
                f"expected {_expected_identity(wanted)!r}, "
                f"observed {_record_identity(record)!r}"
            )
    return records


def _fixture_records(
    scheduled: SeasonSnapshotService,
    historical: SeasonSnapshotService,
    expected: Iterable[ExpectedRequest],
) -> list[dict[str, object]]:
    """Apply the same sequence assertion to recorded fixture sources."""

    scheduled_calls = list(getattr(getattr(scheduled, "source", None), "calls", ()))
    historical_calls = list(getattr(getattr(historical, "source", None), "calls", ()))
    combined: list[tuple[str, tuple[str, int, str, str]]] = []
    if scheduled_calls:
        combined.append(("scheduled", scheduled_calls[0]))
    combined.extend(("historical", call) for call in historical_calls)
    combined.extend(("scheduled", call) for call in scheduled_calls[1:])
    observed = tuple(
        (category, year, kind, decision)
        for category, (kind, year, _classification, decision) in combined
    )
    wanted = tuple(
        (row.category, row.season, row.endpoint, row.cache_decision)
        for row in expected
    )
    if observed != wanted:
        raise RecoveryContractError(
            f"fixture request sequence mismatch: expected {wanted!r}, observed {observed!r}"
        )
    return [
        {
            "category": category,
            "purpose": category,
            "season": season,
            "endpoint": endpoint,
            "cache_decision": cache_decision,
            "budget_impact": 1,
            "outcome": "succeeded",
            "error_type": None,
        }
        for category, season, endpoint, cache_decision in observed
    ]


def _assert_stage_ledger(
    scheduled: SeasonSnapshotService,
    historical: SeasonSnapshotService,
    expected: Iterable[ExpectedRequest],
    meter: RequestMeter | None,
) -> list[dict[str, object]]:
    expected_rows = tuple(expected)
    if meter is not None:
        return _assert_audit_prefix(meter, expected_rows)
    return _fixture_records(scheduled, historical, expected_rows)


def _public_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


_PROVENANCE_JSON_KEYS = frozenset(
    {
        "last_updated",
        "created_at",
        "captured_at",
        "started_at",
        "completed_at",
        "timestamp",
        "code_revision",
        "manifest_checksum",
        "base_tree_sha256",
        "immediate_base_tree_sha256",
        "baseline_provenance",
        "source_input_provenance",
        "source_snapshot",
        "snapshot_checksum",
        "snapshot_archive_checksum",
        "migration_provenance",
        "calendar_provenance",
        "correction_registry_provenance",
    }
)


def _comparison_site(value: str | Path | Release) -> Path:
    if isinstance(value, Release):
        return value.site
    path = Path(value)
    return path / "site" if (path / "site").is_dir() else path


def _public_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in sorted(_public_hashes(root)):
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _normalized_declared_provenance(path: Path) -> bytes | None:
    """Return comparable bytes with declared timestamp/provenance removed."""

    raw = path.read_bytes()
    if path.suffix.lower() in {".html", ".htm"}:
        text = raw.decode("utf-8", errors="replace")
        if LAST_UPDATED_RE.search(text) is None:
            return None
        return LAST_UPDATED_RE.sub(lambda match: f"{match.group(0).split(':', 1)[0]}:<timestamp>", text).encode(
            "utf-8"
        )
    if path.suffix.lower() != ".json":
        return None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    found = False

    def scrub(item: Any) -> Any:
        nonlocal found
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if str(key).lower() in _PROVENANCE_JSON_KEYS:
                    found = True
                    continue
                result[str(key)] = scrub(child)
            return result
        if isinstance(item, list):
            return [scrub(child) for child in item]
        return item

    normalized = scrub(value)
    if not found:
        return None
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _is_timestamp_or_provenance_difference(
    candidate_root: Path,
    reference_root: Path,
    relative: str,
) -> bool:
    candidate_path = candidate_root / relative
    reference_path = reference_root / relative
    try:
        candidate_normalized = _normalized_declared_provenance(candidate_path)
        reference_normalized = _normalized_declared_provenance(reference_path)
    except OSError:
        return False
    return (
        candidate_normalized is not None
        and reference_normalized is not None
        and candidate_normalized == reference_normalized
    )


def compare_site_trees(
    candidate: str | Path | Release,
    references: Mapping[str, str | Path | Release],
    *,
    explanation_ledger: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Produce complete, classified public-tree diffs for retained references.

    Every added/deleted path and every changed file is classified.  A changed
    file is ``timestamp_or_provenance`` only when removing explicitly declared
    metadata leaves the remaining bytes equivalent; all other differences are
    ``numerical_or_content``.  Classification does not explain a difference:
    callers must provide an explicit per-reference, per-path explanation
    ledger to close the ``unexplained`` set.  The function never consults
    Release ownership, so comparison evidence remains independent of the
    candidate manifest.
    """

    candidate_root = _comparison_site(candidate)
    candidate_files = _public_hashes(candidate_root)
    candidate_paths = set(candidate_files)
    result: dict[str, dict[str, Any]] = {}
    for name, reference in references.items():
        reference_root = _comparison_site(reference)
        reference_files = _public_hashes(reference_root)
        reference_paths = set(reference_files)
        added = sorted(candidate_paths - reference_paths)
        deleted = sorted(reference_paths - candidate_paths)
        changed = sorted(
            relative
            for relative in candidate_paths & reference_paths
            if candidate_files[relative] != reference_files[relative]
        )
        unchanged = sorted(
            relative
            for relative in candidate_paths & reference_paths
            if candidate_files[relative] == reference_files[relative]
        )
        timestamp_or_provenance = sorted(
            relative
            for relative in changed
            if _is_timestamp_or_provenance_difference(
                candidate_root, reference_root, relative
            )
        )
        numerical_or_content = sorted(
            set(added)
            | set(deleted)
            | (set(changed) - set(timestamp_or_provenance))
        )
        all_differences = set(added) | set(deleted) | set(changed)
        supplied_ledger = dict((explanation_ledger or {}).get(str(name), {}))
        explained = all_differences & set(supplied_ledger)
        invalid_explanations = sorted(set(supplied_ledger) - all_differences)
        result[str(name)] = {
            "candidate_tree_sha256": _public_tree_digest(candidate_root),
            "reference_tree_sha256": _public_tree_digest(reference_root),
            "added": added,
            "changed": changed,
            "deleted": deleted,
            "unchanged": unchanged,
            "timestamp_or_provenance": timestamp_or_provenance,
            "numerical_or_content": numerical_or_content,
            "explanation_ledger": {
                relative: supplied_ledger[relative] for relative in sorted(explained)
            },
            "unexplained": sorted(all_differences - explained),
            "invalid_explanations": invalid_explanations,
        }
    return result


def _public_delta(original_site: str | Path, candidate_site: str | Path) -> dict[str, list[str]]:
    """Report public tree changes without using Release manifest ownership."""

    def site_path(value: str | Path) -> Path:
        path = Path(value)
        return path / "site" if (path / "site").is_dir() else path

    original = _public_hashes(site_path(original_site))
    candidate = _public_hashes(site_path(candidate_site))
    original_paths = set(original)
    candidate_paths = set(candidate)
    return {
        "added": sorted(candidate_paths - original_paths),
        "changed": sorted(
            relative
            for relative in original_paths & candidate_paths
            if original[relative] != candidate[relative]
        ),
        "deleted": sorted(original_paths - candidate_paths),
    }


def _assert_staging_root(original_site: Path, output_root: Path) -> None:
    original = original_site.resolve()
    staging = output_root.resolve()
    if staging == original or original in staging.parents:
        raise RecoveryContractError(
            "backfill output must be outside the original Published Site"
        )


def run_p0_backfill(
    *,
    scheduled_service: SeasonSnapshotService | None = None,
    historical_service: SeasonSnapshotService | None = None,
    meter: RequestMeter | None = None,
    classification: str = "FBS",
    published_site: str | Path = "website",
    output_root: str | Path = ".sportsrank/releases",
    timestamp: str | None = None,
    code_revision: str = "working-tree",
) -> P0BackfillResult:
    """Run the approved six-call sequence and build one immutable candidate.

    This function intentionally has no promotion or hosting side effect.  A
    failed stage raises immediately; there is no retry loop and no later stage
    is attempted after a failure or audit mismatch.
    """

    classification = classification.upper()
    if classification != "FBS":
        raise NotImplementedError("P0 recovery currently supports FBS only")
    scheduled = scheduled_service or get_snapshot_service("scheduled")
    historical = historical_service or get_snapshot_service("historical")
    _assert_shared_boundaries(scheduled, historical)
    meter = meter or _meter_for(scheduled) or _meter_for(historical)
    expected = P0_EXPECTED_REQUESTS
    if meter is None:
        # Fixture sources do not write a SQLite audit.  Their recorded calls
        # still receive the same exact identity check, while production must
        # always arrive here with the shared Request Meter.
        existing_calls = list(getattr(getattr(scheduled, "source", None), "calls", ()))
        existing_calls += list(getattr(getattr(historical, "source", None), "calls", ()))
        if existing_calls:
            raise RecoveryContractError(
                "P0 production backfill requires a shared Request Meter audit"
            )
    else:
        current = meter.audit_records()
        if len(current) not in (0, 1, len(expected)):
            raise RecoveryContractError(
                "P0 backfill requires an empty audit, its one successful smoke row, "
                "or the exact completed six-call ledger"
            )
        if len(current) == 1:
            _assert_audit_prefix(meter, expected[:1])
        elif len(current) == len(expected):
            _assert_audit_prefix(meter, expected)

    original = Path(published_site)
    if not original.is_dir() or not any(original.rglob("*")):
        raise RecoveryContractError("published_site must be an existing non-empty directory")
    output = Path(output_root)
    _assert_staging_root(original, output)
    stamp = _timestamp(timestamp)

    completed_ledger_resume = meter is not None and len(meter.audit_records()) == len(expected)
    if completed_ledger_resume:
        # This is the sole zero-transport resume state: every approved request
        # already succeeded in exact order, and every input must be present in
        # the canonical cache.  No refresh or fallback fetch is permitted.
        snapshot_2024 = historical.load_cached(2024, classification)
        snapshot_2025 = historical.load_cached(2025, classification)
        snapshot_2026 = scheduled.load_cached(2026, classification)
        records = _assert_audit_prefix(meter, expected)
    else:
        # The prime operation is idempotent only with respect to the cache.  It
        # never retries a failed request and the ledger check rejects an unexpected
        # cache hit or extra request.
        scheduled.prime_teams(2026, classification)
        _assert_stage_ledger(scheduled, historical, expected[:1], meter)

        snapshot_2024 = historical.get(2024, classification)
        _assert_stage_ledger(scheduled, historical, expected[:3], meter)
        snapshot_2025 = historical.get(2025, classification)
        _assert_stage_ledger(scheduled, historical, expected[:5], meter)
        snapshot_2026 = scheduled.get(2026, classification)
        records = _assert_stage_ledger(scheduled, historical, expected, meter)

    first = build_release(
        snapshot_2024,
        output,
        release_id="2024-final",
        phase="final",
        published_site=original,
        timestamp=stamp,
        code_revision=code_revision,
    )
    validate_release(first, published_site=original).raise_for_failure()

    second = build_release(
        snapshot_2025,
        output,
        release_id="2025-final",
        phase="final",
        published_site=first.site,
        timestamp=stamp,
        code_revision=code_revision,
    )
    validate_release(second, published_site=first.site).raise_for_failure()

    third = build_release(
        snapshot_2026,
        output,
        release_id="2026-preseason",
        phase="preseason",
        published_site=second.site,
        timestamp=stamp,
        code_revision=code_revision,
    )
    validate_release(third, published_site=second.site).raise_for_failure()
    # Gate 1 is authoritative only when the complete final candidate is
    # checked against the original Published Site.  The release module derives
    # cumulative runs, snapshots, and the owned graph independently; no
    # manifest-declared owned list is consulted here.
    validate_release_chain(third, original).raise_for_failure()

    if meter is not None:
        records = _assert_audit_prefix(meter, expected)
    return P0BackfillResult(
        original_site=original,
        releases=(first, second, third),
        snapshots=(snapshot_2024, snapshot_2025, snapshot_2026),
        audit_records=tuple(records),
    )


# Descriptive aliases keep the orchestration seam discoverable to operators and
# tests without introducing a second implementation path.
run_p0_recovery = run_p0_backfill
backfill = run_p0_backfill
run_backfill = run_p0_backfill


def _snapshot_command(args: argparse.Namespace, *, refresh: bool) -> int:
    try:
        service = _service(args)
        snapshot = service.get(
            args.year,
            args.classification,
            refresh_games=refresh,
            required_week=getattr(args, "required_week", None),
        )
        print(
            json.dumps(
                {
                    "stage": "refresh" if refresh else "fetch",
                    "year": snapshot.year,
                    "classification": snapshot.classification,
                    "teams": len(snapshot.teams),
                    "games": len(snapshot.games),
                    "complete_through_week": snapshot.complete_through_week,
                    "snapshot_checksum": snapshot.checksum,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        stage = "refresh" if refresh else "fetch"
        print(f"stage={stage} cause={type(exc).__name__}: {_safe_message(exc)}", file=sys.stderr)
        return 1


def _prime(args: argparse.Namespace) -> int:
    try:
        service = _service(args, category=args.category)
        meter = _meter_for(service)
        before = len(meter.audit_records()) if meter is not None else int(getattr(service.source, "call_count", 0))
        teams = service.prime_teams(args.year, args.classification)
        after = len(meter.audit_records()) if meter is not None else int(getattr(service.source, "call_count", 0))
        calls = after - before
        if calls > 1:
            raise RecoveryContractError("team prime made more than one request")
        if meter is not None and calls == 1:
            records = meter.audit_records()
            record = records[-1]
            expected = ExpectedRequest(args.category, args.year, "teams", "miss")
            if _record_identity(record) != _expected_identity(expected):
                raise RecoveryContractError("team prime audit row did not match the expected teams miss")
        print(
            json.dumps(
                {"stage": "smoke", "teams": len(teams), "calls": calls},
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(f"stage=smoke cause={type(exc).__name__}: {_safe_message(exc)}", file=sys.stderr)
        return 1


# Keep the historical private name available to integrations while routing it
# through the cache-persisting implementation.
_smoke = _prime


def _build(args: argparse.Namespace) -> int:
    try:
        if args.phase == "preseason" and args.through_week is not None:
            raise ValueError("preseason does not accept --through-week")
        if args.phase == "week" and args.through_week is None:
            raise ValueError("week requires --through-week")
        source_inputs = _source_inputs(args)
        service = _service(args)
        snapshot = service.load_cached(args.year, args.classification)
        release = build_release(
            snapshot,
            args.output_root,
            release_id=args.release_id,
            phase=args.phase,
            target_week=args.through_week,
            previous_final=args.previous_final,
            timestamp=_timestamp(args.timestamp),
            code_revision=args.code_revision,
            published_site=args.published_site,
            source_inputs=source_inputs,
        )
        report = validate_release(
            release,
            published_site=args.published_site,
            source_inputs=source_inputs,
        )
        report.raise_for_failure()
        print(
            json.dumps(
                {
                    "stage": "build",
                    "phase": args.phase,
                    "release": str(release.root),
                    "site": str(release.site),
                    "valid": True,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(f"stage=build cause={type(exc).__name__}: {_safe_message(exc)}", file=sys.stderr)
        return 1


def _backfill(args: argparse.Namespace) -> int:
    try:
        scheduled = _service(args, category="scheduled")
        historical = _service(args, category="historical")
        result = run_p0_backfill(
            scheduled_service=scheduled,
            historical_service=historical,
            classification=args.classification,
            published_site=args.published_site,
            output_root=args.output_root,
            timestamp=args.timestamp,
            code_revision=args.code_revision,
        )
        print(
            json.dumps(
                {
                    "stage": "backfill",
                    "calls": len(result.audit_records) or 6,
                    "releases": [str(release.root) for release in result.releases],
                    "candidate": str(result.candidate_site),
                    "promoted": False,
                    "published": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(f"stage=backfill cause={type(exc).__name__}: {_safe_message(exc)}", file=sys.stderr)
        return 1


def _validate(args: argparse.Namespace) -> int:
    source_inputs = _source_inputs(args)
    if args.published_site is None:
        report = validate_release(args.candidate, source_inputs=source_inputs)
        delta = None
    else:
        report = validate_release_chain(
            args.candidate,
            args.published_site,
            source_inputs=source_inputs,
        )
        delta = _public_delta(args.published_site, args.candidate)
    if args.as_json:
        payload = {
            "valid": report.valid,
            "failures": [failure.__dict__ for failure in report.failures],
            "legacy_failures": [
                failure.__dict__ for failure in report.legacy_failures
            ],
            "checked_artifacts": report.checked_artifacts,
        }
        if delta is not None:
            payload.update(delta)
        print(json.dumps(payload, sort_keys=True))
    else:
        print(
            f"valid={report.valid} failures={len(report.failures)} "
            f"legacy_failures={len(report.legacy_failures)}"
        )
        if delta is not None:
            print(
                f"added={len(delta['added'])} changed={len(delta['changed'])} "
                f"deleted={len(delta['deleted'])}"
            )
        for failure in report.failures:
            print(str(failure), file=sys.stderr)
    return 0 if report.valid else 1


def _promote(args: argparse.Namespace) -> int:
    try:
        source_inputs = _source_inputs(args)
        result = promote_release(
            args.candidate,
            args.published_site,
            validation_base=args.validation_base,
            source_inputs=source_inputs,
        )
        print(
            json.dumps(
                {
                    "stage": "promote",
                    "changed": result.changed,
                    "reason": result.reason,
                    "target": str(result.target),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(f"stage=promote cause={type(exc).__name__}: {_safe_message(exc)}", file=sys.stderr)
        return 1


def _migrate_postseason(args: argparse.Namespace) -> int:
    try:
        seasons = tuple(args.seasons or (2024, 2025, 2026))
        source_inputs = _source_inputs(args)
        paths = migrate_postseason_cache(
            args.source_root,
            args.destination_root,
            classification=args.classification,
            seasons=seasons,
            source_inputs=source_inputs,
        )
        print(
            json.dumps(
                {
                    "stage": "migrate-postseason",
                    "source_root": str(args.source_root),
                    "destination_root": str(args.destination_root),
                    "seasons": list(seasons),
                    "snapshots": [str(path) for path in paths],
                    "network_calls": 0,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            f"stage=migrate-postseason cause={type(exc).__name__}: {_safe_message(exc)}",
            file=sys.stderr,
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"smoke", "prime"}:
        return _prime(args)
    if args.command == "fetch":
        return _snapshot_command(args, refresh=False)
    if args.command == "refresh":
        return _snapshot_command(args, refresh=True)
    if args.command == "build":
        return _build(args)
    if args.command in {"backfill", "p0", "recover"}:
        return _backfill(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "promote":
        return _promote(args)
    if args.command == "migrate-postseason":
        return _migrate_postseason(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
