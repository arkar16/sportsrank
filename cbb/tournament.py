"""Track men's NCAA tournament games from a local CBBD cache.

report reconstructs weekly matchup estimates. freeze --ledger saves the first
eligible baseline forecast for each scheduled game. report --ledger grades those
saved picks after the game cache is refreshed.
"""
import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
import sqlite3

from .grade import grade_margins
from .replay import replay_seasons


def tournament_games(db, year):
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        games = con.execute("""
            SELECT raw_json FROM games WHERE season = ?
            AND json_extract(raw_json, '$.tournament') = 'NCAA' ORDER BY start_utc, id
        """, (year,))
        return {g["id"]: g for (raw,) in games for g in [json.loads(raw)]}


def matchup(g, forecast):
    return {
        "game_id": g["id"], "home_id": g["homeTeamId"], "away_id": g["awayTeamId"],
        "home": g["homeTeam"], "away": g["awayTeam"], "home_seed": g["homeSeed"],
        "away_seed": g["awaySeed"], "start_utc": g["startDate"],
        "round": (g.get("gameNotes") or "").split(" - ")[-1],
        "cutoff_utc": forecast["cutoff_utc"], "home_margin": forecast["home_margin"],
    }


def record_picks(ledger, year, games, forecasts, model):
    with closing(sqlite3.connect(ledger)) as con, con:
        con.execute("""CREATE TABLE IF NOT EXISTS tournament_picks
                    (season INTEGER, game_id INTEGER, model TEXT NOT NULL, pick TEXT NOT NULL,
                     PRIMARY KEY(season,game_id,model))""")
        con.execute("BEGIN IMMEDIATE")
        now = datetime.now(timezone.utc)
        inserted = 0
        for key, g in games.items():
            f = forecasts.get(key)
            if g["status"] != "scheduled" or f is None:
                continue
            if not datetime.fromisoformat(f["cutoff_utc"]) <= now < datetime.fromisoformat(g["startDate"]):
                continue
            pick = dict(matchup(g, f), prepared_utc=now.isoformat(), model=model)
            inserted += con.execute("INSERT OR IGNORE INTO tournament_picks VALUES(?,?,?,?)",
                                    (year, key, model, json.dumps(pick))).rowcount
    return inserted


def read_picks(ledger, year, model):
    with closing(sqlite3.connect(Path(ledger).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        return {key: json.loads(raw) for key, raw in con.execute(
            "SELECT game_id,pick FROM tournament_picks WHERE season=? AND model=?", (year, model))}


def report(games, picks, frozen):
    pairs, paired, rows = [], [], []
    for key in dict.fromkeys([*games, *picks]):
        g = games.get(key)
        pick = picks.get(key)
        row = {"game_id": key, "home": g["homeTeam"] if g else pick["home"],
               "away": g["awayTeam"] if g else pick["away"],
               "status": g["status"] if g else "missing from cache", "pick": pick}
        rows.append(row)
        if not g or not pick:
            continue
        if (g["homeTeamId"], g["awayTeamId"]) != (pick["home_id"], pick["away_id"]):
            row["grading"] = "matchup changed"
            continue
        if frozen and datetime.fromisoformat(g["startDate"]) <= datetime.fromisoformat(pick["prepared_utc"]):
            row["grading"] = "tipoff before freeze"
            continue
        if g["status"] != "final":
            continue
        if type(g["homePoints"]) is not int or type(g["awayPoints"]) is not int:
            raise ValueError(f"Final game {key} lacks integer scores")
        actual = g["homePoints"] - g["awayPoints"]
        model = pick["home_margin"]
        row["actual_home_margin"] = actual
        pairs.append((model, actual))
        h, a = pick["home_seed"], pick["away_seed"]
        if h and a and h != a and model != 0:
            paired.append(((a > h) == (actual > 0), (model > 0) == (actual > 0)))
    grade = grade_margins(pairs) if pairs else None
    return {"mode": "frozen pregame" if frozen else "weekly retrospective", "fixtures": len(games),
            "grade": {**asdict(grade), "accuracy": grade.winner_accuracy} if grade else None,
            "seed_comparison": {"paired_decisions": len(paired), "seed_correct": sum(s for s,m in paired),
                                "model_correct": sum(m for s,m in paired)}, "games": rows}


BASELINE = "CBB CORS v0.4.0"


def external_forecasts(path, games, weekly):
    """Use the upstream replay's game identities and weekly cutoff to validate predictions."""
    forecasts = {}
    for row in json.loads(path.read_text()):
        key = row["game_id"]
        if key not in games:
            continue
        expected = weekly.get(key)
        if expected is None:
            continue
        for field in ("home_team_id", "away_team_id", "cutoff_utc"):
            if row[field] != expected[field]:
                raise ValueError(f"Forecast {key} disagrees on {field}")
        if key in forecasts or type(row["home_margin"]) not in (float, int) or not isfinite(row["home_margin"]):
            raise ValueError(f"Duplicate or invalid margin for {key}")
        forecasts[key] = row
    return forecasts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "report"])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--season", type=int, required=True, help="CBBD ending year")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--model", default=BASELINE)
    parser.add_argument("--forecasts", type=Path, help="JSON array of model matchup forecasts")
    args = parser.parse_args()
    if args.action == "report" and args.ledger and args.forecasts:
        parser.error("ledger reports read saved forecasts")
    if not (args.action == "report" and args.ledger) and ((args.model != BASELINE) != bool(args.forecasts)):
        parser.error("a separate model name and --forecasts must be supplied together")
    games = tournament_games(args.database, args.season)
    if args.action == "report" and args.ledger:
        result = report(games, read_picks(args.ledger, args.season, args.model), True)
    else:
        if args.action == "freeze" and not args.ledger:
            parser.error("freeze requires --ledger")
        forecasts = {}
        if games:
            season = replay_seasons(args.database, args.season)[-1]
            forecasts = {r["game_id"]: r for r in season["forecasts"]}
        if args.forecasts:
            forecasts = external_forecasts(args.forecasts, games, forecasts)
        if args.action == "freeze":
            result = {"new_picks": record_picks(args.ledger, args.season, games, forecasts, args.model)}
        else:
            picks = {k: matchup(g, forecasts[k]) for k,g in games.items() if k in forecasts}
            result = report(games, picks, False)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
