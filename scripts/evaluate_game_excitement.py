"""Evaluate a frozen ESPN evidence snapshot by withholding whole football seasons."""
import argparse
import json
from pathlib import Path

from cfb.excitement_evaluation import EvaluationGame, evaluate_season_holdouts
from cfb.excitement_reference import dump_reference_artifact, fit_reference_artifact, reference_game_from_timeline
from cfb.game_excitement import DATA, Matchup, flow_inputs, read_forecasts


def cases(site, year):
    evidence = json.loads((DATA / f"{year}.json").read_bytes())
    forecasts = read_forecasts(site / f"cfb/years/{year}", year)
    for row in evidence["games"]:
        if "states" in row and (forecast := forecasts.get(Matchup(*row["matchup"]))):
            final, points = flow_inputs(row, year, evidence["source"])
            yield EvaluationGame(forecast.pregame(), final, points)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--site", type=Path, default=Path("website"))
    cli.add_argument("--training", type=int, nargs="+", required=True)
    cli.add_argument("--holdout", type=int, nargs="+")
    cli.add_argument("--reservation-id", help="identifier of the agreed holdout and release criteria")
    cli.add_argument("--report", type=Path, required=True)
    cli.add_argument("--reference", type=Path, help="write the training-only reference candidate")
    args = cli.parse_args()
    games = [case for year in sorted(set(args.training + (args.holdout or []))) for case in cases(args.site, year)]
    report = evaluate_season_holdouts(games, data_kind="qualified_retained",
                                      training_seasons=args.training if args.holdout else None,
                                      evaluation_seasons=args.holdout,
                                      reservation_id=args.reservation_id)
    args.report.write_text(json.dumps(report, separators=(",", ":")) + "\n")
    if args.reference:
        training = [reference_game_from_timeline(g.pregame, g.final, g.timeline) for g in games
                    if g.final.season in args.training]
        ref = fit_reference_artifact(training, artifact_id="espn-cfb-" + "-".join(map(str, sorted(args.training))))
        dump_reference_artifact(ref, args.reference)
        print("Reference", ref.checksum, len(ref.games))
    for fold in report["folds"]:
        for variant in fold["variants"]:
            metrics = variant["metrics"]
            m = metrics["estimate"]
            print(fold["evaluation_season"], variant["tier"], variant["overtime_input"], metrics["count"],
                  {key: round(m[key], 3) for key in ("mean_absolute_error", "signed_bias", "rank_correlation")
                   if m.get(key) is not None}, "baseline MAE", metrics["baseline"]["mean_absolute_error"])


if __name__ == "__main__":
    main()
