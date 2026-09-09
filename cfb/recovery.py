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
import os
from pathlib import Path
import sys
from typing import Iterable, Mapping

try:
    from .cfbd_client import get_snapshot_service
    from .release import (
        Release,
        build_release,
        promote_release,
        validate_release,
        validate_release_chain,
    )
    from .request_meter import RequestMeter
    from .season_snapshot import SeasonSnapshot, SeasonSnapshotService
    from .season_source import FixtureSeasonSource, ProductionSeasonSource
    from .snapshot_cache import SnapshotCache
except ImportError:  # pragma: no cover - direct execution compatibility
    from cfbd_client import get_snapshot_service
    from release import (
        Release,
        build_release,
        promote_release,
        validate_release,
        validate_release_chain,
    )
    from request_meter import RequestMeter
    from season_snapshot import SeasonSnapshot, SeasonSnapshotService
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

    message = str(error)
    secret = os.environ.get("CFBD_API_KEY", "")
    if secret:
        message = message.replace(secret, "[redacted]")
    return message


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

    promote = subparsers.add_parser(
        "promote", help="explicitly validate then atomically promote a local Release"
    )
    promote.add_argument("candidate", type=Path)
    promote.add_argument("published_site", type=Path)

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
        )
        report = validate_release(release, published_site=args.published_site)
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
    if args.published_site is None:
        report = validate_release(args.candidate)
        delta = None
    else:
        report = validate_release_chain(args.candidate, args.published_site)
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
        result = promote_release(args.candidate, args.published_site)
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
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
