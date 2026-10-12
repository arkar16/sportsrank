"""Prepare an offline historical postseason/carryover review website."""
import argparse
from pathlib import Path

from cfb.historical_rebuild import rebuild_history


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games-csv", type=Path, required=True)
    parser.add_argument("--website", type=Path, default=Path("website"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--from-season", type=int)
    args = parser.parse_args(argv)
    try:
        report = rebuild_history(args.website, args.games_csv, args.output,
                                 from_season=args.from_season, progress=lambda value: print(value, flush=True))
    except (OSError, ValueError, ArithmeticError, KeyError) as error:
        parser.exit(1, f"historical rebuild failed: {error}\n")
    print(f'{report["added_postseason_games"]} postseason games restored; review: '
          f'{args.output / "cfb/performance/index.html"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
