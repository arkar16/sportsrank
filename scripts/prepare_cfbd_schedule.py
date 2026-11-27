"""Reduce a retained official games response to a dated active-season schedule."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import zlib

from cfb.week_calendar import canonical_week, canonical_postseason_week


def prepare(database, year, output):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        acquired, blob = db.execute(
            "SELECT retrieved_utc,body FROM responses WHERE season=? AND endpoint='games'", (year,)
        ).fetchone()
    games = []
    for g in json.loads(zlib.decompress(blob)):
        if "fbs" not in (g.get("homeClassification"), g.get("awayClassification")):
            continue
        if g["seasonType"] not in ("regular", "postseason"):
            continue
        week = (
            canonical_postseason_week(year, g["startDate"])
            if g["seasonType"] == "postseason"
            else canonical_week(year, g["week"], g["startDate"])
        )
        final = [g["homePoints"], g["awayPoints"]] if g.get("completed") else None
        if final is not None and not all(type(v) is int and v >= 0 for v in final):
            continue
        games.append(
            dict(
                id=str(g["id"]),
                week=week,
                home=g["homeTeam"],
                away=g["awayTeam"],
                neutral=g["neutralSite"],
                home_division=g.get("homeClassification") or "",
                away_division=g.get("awayClassification") or "",
                start=g["startDate"],
                postseason=g["seasonType"] == "postseason",
                final=final,
            )
        )
    output.write_text(
        json.dumps(
            dict(provider="CollegeFootballData", year=year, acquired_utc=acquired, games=games),
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--database", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("year", type=int)
    args = cli.parse_args()
    prepare(args.database, args.year, args.output)
