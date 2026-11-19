"""Save paired pregame forecasts from a locked finalist and the published baseline."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .experiment import BASELINE, REST_DAY_LIMIT, Candidate
from .replay import HOME_ADVANTAGE, MARGIN_WEIGHT, replay_seasons
from .rest import RestHistory
from .source import read_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--development", required=True, type=Path)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    start = datetime.now(timezone.utc)
    finalist = json.loads(args.development.read_text())["finalist"]
    selected = finalist["model"]
    data, = read_history(args.database, args.season, args.season)
    rest = RestHistory(data.games)
    forecasts = {}
    for name, candidate, coefficient in (
        ("baseline", BASELINE, 0), ("challenger", Candidate(**selected["candidate"]), selected["rest_coefficient"]),
    ):
        edition = replay_seasons(args.database, args.season,
                                 home_advantage=HOME_ADVANTAGE + candidate.home_advantage_delta,
                                 carryover_strength=candidate.preseason_carryover_scale,
                                 margin_weight=MARGIN_WEIGHT * candidate.margin_weight_scale)[-1]
        checkpoint = max(c["cutoff_utc"] for c in edition["rankings"] if datetime.fromisoformat(c["cutoff_utc"]) <= start)
        for r in edition["forecasts"]:
            if r["status"] != "scheduled" or r["cutoff_utc"] != checkpoint or datetime.fromisoformat(r["start_utc"]) <= start:
                continue
            diff = rest.differential(r["home_team_id"], r["away_team_id"], datetime.fromisoformat(checkpoint)) or 0
            row = forecasts.setdefault(r["game_id"], {k: r[k] for k in ("game_id","start_utc","home_team","away_team","cutoff_utc")})
            row[name] = round(r["home_margin"] + coefficient * max(-REST_DAY_LIMIT, min(REST_DAY_LIMIT, diff)), 2)
    saved = datetime.now(timezone.utc)
    rows = [r for r in forecasts.values() if datetime.fromisoformat(r["start_utc"]) > saved]
    if not rows or any("baseline" not in r or "challenger" not in r for r in rows):
        raise ValueError("Expected paired upcoming games in the current slate")
    body = {"prepared_utc": saved.isoformat(), "season": args.season, "finalist": finalist, "forecasts": rows}
    with args.output.open("x") as output:
        json.dump(body, output, indent=2, allow_nan=False)
        output.write("\n")


if __name__ == "__main__":
    main()
