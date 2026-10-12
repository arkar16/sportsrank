"""Prepare an offline historical postseason/carryover review website."""
import argparse
from pathlib import Path

from cfb.historical_rebuild import rebuild_history
from cfb.market_lines import load_inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games-csv", type=Path, required=True)
    parser.add_argument("--website", type=Path, default=Path("website"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--from-season", type=int)
    parser.add_argument("--market-inputs", type=Path, help="private cached provider-response manifest")
    args = parser.parse_args(argv)
    try:
        report = rebuild_history(args.website, args.games_csv, args.output,
                                 from_season=args.from_season, progress=lambda value: print(value, flush=True),
                                 market_lines=load_inputs(args.market_inputs) if args.market_inputs else None)
    except (OSError, ValueError, ArithmeticError, KeyError) as error:
        parser.exit(1, f"historical rebuild failed: {error}\n")
    print(f'{report["added_postseason_games"]} postseason games restored; review: '
          f'{args.output / "cfb/performance/index.html"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
