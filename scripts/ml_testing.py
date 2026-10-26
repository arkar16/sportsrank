"""Compare a learned margin calibration with CORS on earlier-season-only folds."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

from cfb.ml_testing import experiment, write_experiment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--website", type=Path, default=Path("website"))
    parser.add_argument("--starter-pack", type=Path, help="local CFBD Starter Pack root containing data/")
    parser.add_argument("--output", type=Path, required=True, help="new local directory outside website")
    parser.add_argument("--from-season", type=int, help="first test season; earlier seasons still train")
    parser.add_argument("--through-season", type=int)
    parser.add_argument("--min-training-seasons", type=int, default=2)
    parser.add_argument("--min-training-games", type=int, default=100)
    args = parser.parse_args(argv)
    started = perf_counter()
    try:
        options = dict(from_season=args.from_season, through_season=args.through_season,
                       min_training_seasons=args.min_training_seasons, min_training_games=args.min_training_games)
        if args.starter_pack is None:
            report = experiment(args.website, **options)
        else:
            from cfb.starter_experiment import experiment as starter_experiment
            report = starter_experiment(args.website, args.starter_pack, **options)
        write_experiment(report, args.output, args.website,
                         protected_roots=(args.starter_pack,) if args.starter_pack is not None else ())
        elapsed = perf_counter() - started
        # Keep variable execution timing separate from reproducible result bytes.
        (args.output / "runtime.json").write_text(json.dumps({"elapsed_seconds": elapsed}) + "\n")
    except (OSError, ValueError, ArithmeticError, KeyError) as error:
        parser.exit(1, f"ML testing failed: {error}\n")
    summary = report["summary"]
    print(f"{summary['game_count']} held-out games across {len(report['folds'])} seasons in {elapsed:.2f}s")
    for model, metrics in summary.items():
        if model != "game_count":
            print(f"{model}: MAE {metrics['mae']:.3f}; RMSE {metrics['rmse']:.3f}")
    print(f"Results: {args.output / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
