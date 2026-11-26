"""Retain the direct API fields needed to replay CORS before the ranked football archive."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import zlib


def prepare(database, output):
    years = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for year, body in db.execute(
            "SELECT season,body FROM responses WHERE endpoint='games' AND season<1897 ORDER BY season"
        ):
            rows = json.loads(zlib.decompress(body))
            if not rows:
                continue
            roster = db.execute(
                "SELECT body FROM responses WHERE endpoint='teams' AND season=?", (year,)
            ).fetchone()
            if not roster:
                raise ValueError(f"missing official {year} football roster")
            teams = sorted(
                (t["school"], t.get("conference") or "") for t in json.loads(zlib.decompress(roster[0]))
            )
            games = []
            for g in rows:
                if not g.get("completed") or not all(
                    type(g.get(k)) is int and g[k] >= 0 for k in ("homePoints", "awayPoints")
                ):
                    continue
                games.append(
                    [
                        g["week"],
                        g["homeTeam"],
                        g.get("homeClassification") or "",
                        g["homePoints"],
                        g["awayTeam"],
                        g.get("awayClassification") or "",
                        g["awayPoints"],
                        bool(g["neutralSite"]),
                        str(g["id"]),
                        g["startDate"][:10],
                    ]
                )
            years[year] = dict(teams=teams, games=sorted(games, key=lambda g: (g[-1], g[-2])))
    output.write_text(
        json.dumps(dict(provider="CollegeFootballData", seasons=years), separators=(",", ":")) + "\n"
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--database", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    prepare(args.database, args.output)
