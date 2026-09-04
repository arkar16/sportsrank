"""Command-line entry points for offline CFB recovery Releases.

Commands intentionally separate fetching from rendering and promotion.  The
``build``, ``validate``, and ``promote`` commands use ``load_cached`` or the
candidate tree and therefore make zero CFBD calls.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

try:
    from .cfbd_client import get_snapshot_service
    from .request_meter import RequestMeter
    from .release import (
        MODEL_VERSION,
        ReleaseValidationError,
        build_release,
        promote_release,
        validate_release,
    )
    from .season_snapshot import SeasonSnapshotService
    from .season_source import FixtureSeasonSource, ProductionSeasonSource
    from .snapshot_cache import SnapshotCache
except ImportError:  # pragma: no cover - direct execution compatibility
    from cfbd_client import get_snapshot_service
    from request_meter import RequestMeter
    from release import MODEL_VERSION, ReleaseValidationError, build_release, promote_release, validate_release
    from season_snapshot import SeasonSnapshotService
    from season_source import FixtureSeasonSource, ProductionSeasonSource
    from snapshot_cache import SnapshotCache


COMMANDS = {"smoke", "fetch", "refresh", "build", "validate", "promote"}


def _timestamp(value: str | None) -> str:
    return value or datetime.now(timezone.utc).isoformat()


def _service(args: argparse.Namespace, *, category: str | None = None) -> SeasonSnapshotService:
    fixture_root = getattr(args, "fixture_root", None)
    cache_dir = getattr(args, "cache_dir", None)
    if fixture_root:
        if cache_dir is None:
            raise ValueError("--cache-dir is required with --fixture-root")
        return SeasonSnapshotService(
            FixtureSeasonSource(fixture_root),
            SnapshotCache(cache_dir),
            clock=lambda: datetime.now(timezone.utc),
        )
    return get_snapshot_service(category or getattr(args, "category", "scheduled"))


def _identity_args(parser: argparse.ArgumentParser, *, cache: bool = True) -> None:
    parser.add_argument("year", type=int)
    parser.add_argument("--classification", default="FBS")
    if cache:
        parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--fixture-root", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cfb.recovery",
        description="Offline-capable staged CFB Season Snapshot Release pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    smoke = subparsers.add_parser("smoke", help="make one metered authenticated teams call")
    smoke.add_argument("year", type=int)
    smoke.add_argument("--classification", default="FBS")
    smoke.add_argument("--category", default="scheduled")
    smoke.add_argument("--cache-dir", type=Path)
    smoke.add_argument("--fixture-root", type=Path)

    for name, help_text, refresh in (
        ("fetch", "fetch and cache one Season Snapshot", False),
        ("refresh", "refresh cached games and reuse cached teams", True),
    ):
        command = subparsers.add_parser(name, help=help_text)
        _identity_args(command)
        command.add_argument("--category", default="scheduled")
        command.add_argument("--required-week", type=int)

    build = subparsers.add_parser("build", help="build a candidate Release from a cached snapshot")
    _identity_args(build)
    build.add_argument("--release-id", required=True)
    build.add_argument("--output-root", type=Path, default=Path("build/releases"))
    build.add_argument("--week", type=int, dest="target_week")
    build.add_argument("--previous-final", type=Path)
    build.add_argument("--published-site", type=Path)
    build.add_argument("--clone-published", action="store_true")
    build.add_argument("--timestamp")
    build.add_argument("--code-revision", default="working-tree")

    validate = subparsers.add_parser("validate", help="strictly validate a candidate Release offline")
    validate.add_argument("candidate", type=Path)
    validate.add_argument("--json", action="store_true", dest="as_json")

    promote = subparsers.add_parser("promote", help="validate then atomically promote a local Release")
    promote.add_argument("candidate", type=Path)
    promote.add_argument("published_site", type=Path)

    return parser


def _snapshot_command(args: argparse.Namespace, *, refresh: bool) -> int:
    try:
        service = _service(args)
        snapshot = service.get(
            args.year,
            args.classification,
            refresh_games=refresh,
            required_week=getattr(args, "required_week", None),
        )
        print(json.dumps({
            "stage": "refresh" if refresh else "fetch",
            "year": snapshot.year,
            "classification": snapshot.classification,
            "teams": len(snapshot.teams),
            "games": len(snapshot.games),
            "complete_through_week": snapshot.complete_through_week,
            "snapshot_checksum": snapshot.checksum,
        }, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"stage={'refresh' if refresh else 'fetch'} cause={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _smoke(args: argparse.Namespace) -> int:
    try:
        # Smoke deliberately bypasses SeasonSnapshotService so it spends one
        # metered teams request only; normal fetch uses the two-call snapshot.
        if args.fixture_root:
            source = FixtureSeasonSource(args.fixture_root)
            teams = source.fetch_teams(args.year, args.classification, cache_decision="miss")
        else:
            if args.cache_dir:
                meter = RequestMeter(Path(args.cache_dir) / "cfbd_requests.sqlite3")
            else:
                service = get_snapshot_service(args.category)
                source = service.source
                teams = source.fetch_teams(args.year, args.classification, cache_decision="miss")
            if args.cache_dir:
                source = ProductionSeasonSource(meter, category=args.category)
                teams = source.fetch_teams(args.year, args.classification, cache_decision="miss")
        print(json.dumps({"stage": "smoke", "teams": len(teams), "calls": 1}, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"stage=smoke cause={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _build(args: argparse.Namespace) -> int:
    try:
        service = _service(args)
        snapshot = service.load_cached(args.year, args.classification)
        release = build_release(
            snapshot,
            args.output_root,
            release_id=args.release_id,
            target_week=args.target_week,
            previous_final=args.previous_final,
            timestamp=_timestamp(args.timestamp),
            code_revision=args.code_revision,
            published_site=args.published_site,
            clone_published=args.clone_published,
        )
        report = validate_release(release)
        report.raise_for_failure()
        print(json.dumps({"stage": "build", "release": str(release.root), "site": str(release.site), "valid": True}, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"stage=build cause={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _validate(args: argparse.Namespace) -> int:
    report = validate_release(args.candidate)
    if args.as_json:
        print(json.dumps({
            "valid": report.valid,
            "failures": [failure.__dict__ for failure in report.failures],
            "legacy_failures": [failure.__dict__ for failure in report.legacy_failures],
            "checked_artifacts": report.checked_artifacts,
        }, sort_keys=True))
    else:
        print(f"valid={report.valid} failures={len(report.failures)} legacy_failures={len(report.legacy_failures)}")
        for failure in report.failures:
            print(str(failure), file=sys.stderr)
    return 0 if report.valid else 1


def _promote(args: argparse.Namespace) -> int:
    try:
        result = promote_release(args.candidate, args.published_site)
        print(json.dumps({"stage": "promote", "changed": result.changed, "reason": result.reason, "target": str(result.target)}, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"stage=promote cause={type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "smoke":
        return _smoke(args)
    if args.command == "fetch":
        return _snapshot_command(args, refresh=False)
    if args.command == "refresh":
        return _snapshot_command(args, refresh=True)
    if args.command == "build":
        return _build(args)
    if args.command == "validate":
        return _validate(args)
    if args.command == "promote":
        return _promote(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
