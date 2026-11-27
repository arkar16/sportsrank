"""Retain CFBD games and weekly scoring detail through the metered Season Snapshot service."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import zlib

from cfb.cfbd_client import get_snapshot_service


def download(path, seasons, games_only, limit, rosters=False):
    if limit < 0:
        raise ValueError("a nonnegative request allowance is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    service = get_snapshot_service("historical")
    attempts = 0
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS responses(season INTEGER, endpoint TEXT, season_type TEXT, week INTEGER,"
            "retrieved_utc TEXT DEFAULT CURRENT_TIMESTAMP, body BLOB, PRIMARY KEY(season,endpoint,season_type,week))"
        )

        def read(year, week=None, kind="both"):
            nonlocal attempts
            key = (
                year,
                ("teams" if kind == "roster" else "games") if week is None else "plays",
                kind,
                -1 if week is None else week,
            )
            saved = db.execute(
                "SELECT body FROM responses WHERE (season,endpoint,season_type,week)=(?,?,?,?)", key
            ).fetchone()
            if saved:
                return json.loads(zlib.decompress(saved[0]))
            if attempts >= limit:
                raise RuntimeError("football acquisition reached its request allowance")
            raw = service.fetch_excitement_response(year, week=week, season_type=kind)
            attempts += 1
            rows = json.loads(raw)
            with db:
                db.execute(
                    "INSERT INTO responses(season,endpoint,season_type,week,body) VALUES(?,?,?,?,?)",
                    (*key, zlib.compress(raw, 3)),
                )
            print(year, key[1:3], week, len(rows), "records", attempts, "new calls", flush=True)
            return rows

        for year in seasons:
            if rosters:
                read(year, kind="roster")
                continue
            games = read(year)
            if games_only:
                continue
            weeks = sorted(
                {
                    (g["seasonType"], g["week"])
                    for g in games
                    if g.get("completed")
                    and g["seasonType"] in ("regular", "postseason")
                    and "fbs" in (g.get("homeClassification"), g.get("awayClassification"))
                }
            )
            for kind, week in weeks:
                read(year, week, kind)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--max-requests", type=int, required=True)
    cli.add_argument("--games-only", action="store_true")
    cli.add_argument("--rosters", action="store_true")
    cli.add_argument("seasons", type=int, nargs="+")
    args = cli.parse_args()
    download(args.output, args.seasons, args.games_only, args.max_requests, args.rosters)
