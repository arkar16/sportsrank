"""Acquire the program catalog once, then install its shared static marks."""

from __future__ import annotations

import argparse
from pathlib import Path

from cfb.program_marks import ProgramMarksError, install_program_marks
from cfb.request_meter import MeteredRequestFailed, RequestBudgetExhausted, RequestMeter
from cfb.season_snapshot import SeasonSnapshotService
from cfb.season_source import ProductionSeasonSource
from cfb.snapshot_cache import SnapshotCache


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True, help="static site root for public assets")
    parser.add_argument("--cache", type=Path, required=True, help="private catalog directory outside the site")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--acquire-catalog", action="store_true", help="authorize one request if the cache is new")
    mode.add_argument("--offline", action="store_true", help="use retained metadata and existing site images")
    parser.add_argument("--meter", type=Path, help="existing accounted CFBD request audit for acquisition")
    args = parser.parse_args(argv)
    cache = args.cache.resolve()
    website = (Path(__file__).resolve().parents[1] / "website").resolve()
    if cache.is_relative_to(website) or website.is_relative_to(cache):
        parser.error("the raw catalog cache must be separate from website")
    if args.acquire_catalog and not cache.exists() and (args.meter is None or not args.meter.is_file()):
        parser.error("initial acquisition requires --meter with the existing accounted CFBD request audit")

    def acquire() -> bytes:
        meter = RequestMeter(args.meter)
        if not meter.audit_records():
            raise ProgramMarksError("Initial acquisition requires a populated CFBD request audit.")
        service = SeasonSnapshotService(
            ProductionSeasonSource(meter, category="historical"), SnapshotCache(cache / "snapshots"),
        )
        return service.fetch_program_catalog_response()

    try:
        result = install_program_marks(
            args.site, cache, acquire=acquire if args.acquire_catalog else None, offline=args.offline,
        )
    except (ProgramMarksError, RequestBudgetExhausted, MeteredRequestFailed) as error:
        print(f"Program marks stopped. {error}")
        return 1
    except Exception as error:
        print(f"Program marks stopped ({type(error).__name__}). Inspect local cache and request audit.")
        return 1

    print(f"Catalog schools {result.schools}, local images {result.images}, pending images {result.pending}.")
    if result.pending and not args.offline:
        print("Rerun with the same --cache and --site to resume pending images.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
