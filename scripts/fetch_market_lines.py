"""Explicit, metered acquisition of private seasonal provider-line snapshots."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from cfb.request_meter import RequestBudgets, RequestMeter
from cfb.season_snapshot import SeasonSnapshotService
from cfb.season_source import ProductionSeasonSource
from cfb.snapshot_cache import SnapshotCache


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", nargs="+", type=int, required=True)
    parser.add_argument("--meter", type=Path, required=True, help="existing shared or conservatively seeded request audit")
    parser.add_argument("--output", type=Path, required=True, help="new private directory outside website")
    parser.add_argument("--historical-reserve", type=int, default=0,
                        help="known current-month historical attempts absent from the supplied audit")
    args = parser.parse_args(argv)
    if not args.meter.is_file():
        parser.error("an existing accounted request audit is required")
    if not 0 <= args.historical_reserve < 500:
        parser.error("historical reserve must be between 0 and 499")
    years = sorted(set(args.seasons))
    if len(years) != len(args.seasons) or not all(2013 <= year <= datetime.now(timezone.utc).year for year in years):
        parser.error("seasons must be distinct selected-provider seasons through the current year")
    output = args.output.resolve()
    if output.is_relative_to((Path.cwd() / "website").resolve()):
        parser.error("raw provider snapshots must remain outside website")
    try:
        # A failed/uncertain batch is never replayed merely by rerunning a command.
        output.mkdir(parents=True, exist_ok=False)
        meter = RequestMeter(args.meter, RequestBudgets(
            historical=500 - args.historical_reserve, absolute=2500 - args.historical_reserve))
        manifest = {"schema": "market-inputs/v1", "responses": []}
        for year in years:
            source = ProductionSeasonSource(meter, category="scheduled" if year == datetime.now(timezone.utc).year else "historical")
            service = SeasonSnapshotService(source, SnapshotCache(output / "unused-games-cache"))
            entry = service.fetch_market_snapshot(year, output / str(year))
            entry["path"] = f"{year}/response.json"
            manifest["responses"].append(entry)
            (output / "inputs.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
            print(f"Retained {year} provider response ({len(manifest['responses'])}/{len(years)}).", flush=True)
        print(f"Offline input manifest: {output / 'inputs.json'}")
        return 0
    except Exception as error:
        # Exceptions from third-party transports may carry request headers.
        # RequestMeter retains safe failure types; never print response bodies.
        print(f"Market acquisition stopped: {type(error).__name__}. No automatic retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
