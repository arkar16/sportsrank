"""Inspect an offline basketball checkpoint or grade development years."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .grade import grade_margins
from .replay import replay_seasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--season", required=True, type=int, help="Season ending year")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--week", type=int, help="Print rankings and matchup forecasts for this checkpoint")
    group.add_argument("--grade", action="store_true", help="Grade the frozen development baseline")
    args = parser.parse_args()
    if args.grade and not 2021 <= args.season <= 2024:
        parser.error("Development grades are available for seasons ending 2021 through 2024")
    season = replay_seasons(args.database, args.season)[-1]
    forecasts = season["forecasts"]
    if args.grade:
        grade = grade_margins((r["home_margin"], r["actual_home_margin"]) for r in forecasts
                              if r["actual_home_margin"] is not None)
        result = {**asdict(grade), "winner_accuracy": grade.winner_accuracy}
    else:
        checkpoint = next((r for r in season["rankings"] if r["week"] == args.week), None)
        if checkpoint is None:
            parser.error("No such weekly checkpoint")
        result = {"checkpoint": checkpoint, "forecasts": [
            {k: row[k] for k in ("game_id", "start_utc", "home_team", "away_team", "home_margin")}
            for row in forecasts if row["cutoff_utc"] == checkpoint["cutoff_utc"]
        ]}
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
