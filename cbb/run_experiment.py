"""Select a basketball challenger on the development seasons, offline."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

from .experiment import CANDIDATE_GRID, ForecastRow, evaluate_experiment
from .replay import HOME_ADVANTAGE, MARGIN_WEIGHT, replay_seasons
from .rest import RestHistory
from .source import read_history


def candidate_rows(db, candidate, end_year, first_year, rests):
    seasons = replay_seasons(
        db, end_year, home_advantage=HOME_ADVANTAGE + candidate.home_advantage_delta,
        carryover_strength=candidate.preseason_carryover_scale,
        margin_weight=MARGIN_WEIGHT * candidate.margin_weight_scale,
    )
    for season in seasons:
        year = season["season"]
        if year < first_year:
            continue
        for game in season["forecasts"]:
            if game["actual_home_margin"] is None:
                continue
            differential = rests[year].differential(
                game["home_team_id"], game["away_team_id"], datetime.fromisoformat(game["cutoff_utc"])
            )
            yield ForecastRow(year, game["game_id"], game["home_margin"], game["actual_home_margin"], differential)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    rests = {s.year: RestHistory(s.games) for s in read_history(args.database, 2021, 2024)}
    rows = {c: candidate_rows(args.database, c, 2024, 2021, rests) for c in CANDIDATE_GRID}
    print(json.dumps(asdict(evaluate_experiment(rows)), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
