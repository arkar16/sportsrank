"""Build model-performance reports for one season or the entire saved website."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from cfb.performance import backforecast, build_performance_files, report_files


def write_report(report: dict, output: Path, website: Path) -> None:
    if output.resolve().is_relative_to(website.resolve()):
        raise ValueError("use the validated public export to prepare website output")
    output.mkdir(parents=True, exist_ok=True)
    for name, raw in report_files(report).items():
        # Preserve the original single-season command's report.html entry point.
        (output / ("report.html" if name == "index.html" else name)).write_bytes(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season", nargs="?", default="all", help="season year or all (default)")
    parser.add_argument("--website", type=Path, default=Path("website"))
    parser.add_argument("--from-week", type=int)
    parser.add_argument("--through-week", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.season == "all":
            if args.from_week is not None or args.through_week is not None:
                raise ValueError("week filters require a season")
            output = args.output or Path(".sportsrank/backforecasts/all")
            if output.resolve().is_relative_to(args.website.resolve()) or args.website.resolve().is_relative_to(output.resolve()):
                raise ValueError("use the validated public export to prepare website output")
            files = build_performance_files(args.website)
            # Keep source/navigation links functional in the local preview.
            shutil.copytree(args.website, output, dirs_exist_ok=True)
            for relative, raw in files.items():
                # All-season preview retains the public site's relative structure.
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            print(f"All-season performance: {output / 'cfb/performance/index.html'}")
        else:
            report = backforecast(args.website, int(args.season), from_week=args.from_week,
                                  through_week=args.through_week)
            output = args.output or Path(".sportsrank/backforecasts") / args.season
            write_report(report, output, args.website)
            print(f"{report['summary']['game_count']} evaluated games; report: {output / 'report.html'}")
    except (OSError, ValueError, ArithmeticError, KeyError) as error:
        parser.exit(1, f"backforecast failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
