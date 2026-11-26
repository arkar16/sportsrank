"""Qualify retained Season Snapshot game and play responses, offline."""

import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import zlib

from cfb.reference_archive import _rows, _score
from cfb.week_calendar import canonical_week, canonical_postseason_week
from cfb.historical_rebuild import _school
from scripts.prepare_cfbd_excitement import period_scores, measure


def reduce_live(database, private, site):
    early = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for year, blob in db.execute(
            "SELECT season,body FROM responses WHERE endpoint='games' ORDER BY season"
        ):
            raw = json.loads(zlib.decompress(blob))
            results = site / f"cfb/years/{year}/data/results/{year}_FBS_results.html"
            if year < 1897:
                early[year] = [
                    dict(
                        id=str(g["id"]),
                        day=g["startDate"][:10],
                        home=g["homeTeam"],
                        away=g["awayTeam"],
                        h=g["homePoints"],
                        a=g["awayPoints"],
                        neutral=bool(g["neutralSite"]),
                    )
                    for g in raw
                    if g.get("completed")
                    and all(type(g.get(k)) is int and g[k] >= 0 for k in ("homePoints", "awayPoints"))
                ]
                continue
            if not results.is_file():
                continue
            keys = defaultdict(list)
            for r in _rows(results):
                h, a = _score(r.get("home_score")), _score(r.get("away_score"))
                name = _school(r["home_team"]), _school(r["away_team"])
                keys[(*name, h, a)].append((int(r["week"]), *name, r["neutral_site"].lower() == "true"))
            games = {}
            reduced = {}
            for g in raw:
                if not g.get("completed") or "fbs" not in (
                    g.get("homeClassification"),
                    g.get("awayClassification"),
                ):
                    continue
                h, a = g["homePoints"], g["awayPoints"]
                home, away = _school(g["homeTeam"]), _school(g["awayTeam"])
                matches = keys[home, away, h, a]
                if not matches:
                    week = (
                        canonical_postseason_week(year, g["startDate"])
                        if g["seasonType"] == "postseason"
                        else canonical_week(year, g["week"], g["startDate"])
                    )
                    matches = [(week, home, away, g["neutralSite"])]
                if len(matches) != 1:
                    continue
                gid = str(g["id"])
                games[gid] = dict(
                    id=gid,
                    season=year,
                    home_team=home,
                    away_team=away,
                    h=h,
                    a=a,
                    notes=g.get("notes"),
                    home_line_scores=json.dumps(g.get("homeLineScores")),
                    away_line_scores=json.dumps(g.get("awayLineScores")),
                )
                ends = period_scores(games[gid])
                if ends:
                    reduced[gid] = dict(key=matches[0], final=[h, a], ends=ends)
            for (body,) in db.execute(
                "SELECT body FROM responses WHERE season=? AND endpoint='plays' ORDER BY season_type,week",
                (year,),
            ):
                by_game = defaultdict(list)
                for play in json.loads(zlib.decompress(body)):
                    gid = str(play["gameId"])
                    if gid in reduced:
                        play["clock"] = json.dumps(play["clock"])
                        by_game[gid].append(play)
                for gid, plays in by_game.items():
                    result = measure(games[gid], plays)
                    if result is not None:
                        reduced[gid].update(drama=result[0], tied_drama=result[1])
            (private / f"{year}.json").write_text(
                json.dumps(reduced, sort_keys=True, separators=(",", ":")) + "\n"
            )
            print(year, len(reduced), sum("drama" in r for r in reduced.values()), flush=True)
    return early


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--database", type=Path, required=True)
    cli.add_argument("--private", type=Path, required=True)
    cli.add_argument("--site", type=Path, default=Path("website"))
    args = cli.parse_args()
    early = reduce_live(args.database, args.private, args.site)
    (args.private / "early-games.json").write_text(
        json.dumps(early, sort_keys=True, separators=(",", ":")) + "\n"
    )
