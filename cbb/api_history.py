"""Materialize website inputs exclusively from retained basketball API response bodies."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit
import zlib

HOST = "api.collegebasketballdata.com"
SCHEMA = """
CREATE TABLE teams(season INTEGER, id INTEGER, raw_json TEXT NOT NULL, PRIMARY KEY(season,id));
CREATE TABLE games(id INTEGER PRIMARY KEY, season INTEGER, start_utc TEXT, status TEXT, raw_json TEXT NOT NULL, verified_at TEXT);
CREATE INDEX games_season ON games(season);
"""


def materialize(source, output, through):
    """Ignore unprovenanced table overlays. A later API response can correct an earlier response."""
    with (
        closing(
            sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
        ) as src,
        closing(sqlite3.connect(output)) as dst,
    ):
        dst.executescript(SCHEMA)
        endpoints = set()
        windows = {}
        saturated = set()
        for url, n, blob, acquired in src.execute(
            "SELECT url,rows,compressed_json,acquired_utc FROM responses ORDER BY acquired_utc,url"
        ):
            uri = urlsplit(url)
            if (uri.scheme, uri.netloc) != ("https", HOST) or uri.path not in (
                "/games",
                "/teams",
            ):
                continue
            params = parse_qs(uri.query)
            if (
                set(params) - {"season", "startDateRange", "endDateRange"}
                or len(params.get("season", [])) != 1
            ):
                continue
            year = int(params["season"][0])
            if year > through:
                continue
            rows = json.loads(zlib.decompress(blob))
            if not isinstance(rows, list) or len(rows) != n:
                raise ValueError(f"invalid {year} {uri.path} response count")
            endpoints.add((year, uri.path))
            if uri.path == "/games":
                season_start = datetime(year - 1, 7, 1, tzinfo=timezone.utc)
                season_end = datetime(year, 7, 1, tzinfo=timezone.utc)
                period = windows.setdefault(year, [])
                if n < 3000:
                    start = (
                        datetime.fromisoformat(params["startDateRange"][0])
                        if "startDateRange" in params
                        else season_start
                    )
                    end = (
                        datetime.fromisoformat(params["endDateRange"][0])
                        if "endDateRange" in params
                        else season_end
                    )
                    if end <= start:
                        raise ValueError(f"invalid {year} API date range")
                    period.append((start, end))
                else:
                    saturated.add(year)
            for row in rows:
                raw = json.dumps(row, separators=(",", ":"))
                if uri.path == "/teams":
                    dst.execute(
                        "INSERT OR REPLACE INTO teams VALUES(?,?,?)",
                        (year, row["id"], raw),
                    )
                    continue
                if row["season"] != year or not isinstance(row["id"], int):
                    raise ValueError("basketball API season or game identity mismatch")
                status = row["status"]
                home, away = row.get("homePoints"), row.get("awayPoints")
                if status == "final" and (
                    type(home) is not int
                    or type(away) is not int
                    or min(home, away) < 0
                    or home == away
                ):
                    status = "unverified"
                updated = dst.execute(
                    "INSERT INTO games VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "start_utc=excluded.start_utc,status=excluded.status,raw_json=excluded.raw_json,verified_at=excluded.verified_at "
                    "WHERE games.season=excluded.season",
                    (
                        row["id"],
                        year,
                        row.get("startDate"),
                        status,
                        raw,
                        acquired if status == "final" else None,
                    ),
                )
                if not updated.rowcount:
                    raise ValueError("CBBD reused a game ID across seasons")
        for year, intervals in windows.items():
            frontier = datetime(year - 1, 7, 1, tzinfo=timezone.utc)
            for start, end in sorted(intervals):
                if start > frontier:
                    break
                frontier = max(frontier, end)
            required = datetime(year, 7, 1, tzinfo=timezone.utc)
            # An in-season snapshot may explicitly stop at its scheduled-through date.
            if year == through and year not in saturated and intervals:
                required = min(required, max(end for _, end in intervals))
            if frontier < required:
                raise ValueError(f"incomplete CBBD games response range for {year}")
        years = [
            y
            for (y,) in dst.execute("SELECT DISTINCT season FROM games ORDER BY season")
        ]
        if through not in years or (through, "/teams") not in endpoints:
            raise ValueError(
                f"No retained CBBD games and teams responses for {through}"
            )
        dst.commit()
    return years


def national_final(label):
    # The provider's historical round may include the final's host city.
    round_name = (label or "").split(" - ")[-1].casefold().split(" at ", 1)[0].strip()
    return round_name in {
        "national final",
        "national championship",
        "ncaa championship",
        "ncaa-championship",
        "championship",
    }


def championship_finals(database, as_of):
    games = []
    with closing(
        sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        for year, raw in db.execute(
            "SELECT season,raw_json FROM games WHERE status='final' ORDER BY season,id"
        ):
            g = json.loads(raw)
            if (
                g.get("tournament") != "NCAA"
                or not national_final(g.get("gameNotes"))
                or not g.get("startDate")
                or datetime.fromisoformat(g["startDate"]) >= as_of
            ):
                continue
            h, a = g["homePoints"], g["awayPoints"]
            if type(h) is not int or type(a) is not int or h == a:
                continue
            home = g["homeTeam"], g["awayTeam"]
            winner, runner = home if h > a else home[::-1]
            games.append(
                dict(
                    year=year,
                    winner=winner,
                    runner_up=runner,
                    record="",
                    score=f"{max(h, a)}–{min(h, a)}",
                )
            )
    if len({g["year"] for g in games}) != len(games):
        raise ValueError("ambiguous API national championship")
    return games


if __name__ == "__main__":
    import argparse
    from tempfile import TemporaryDirectory

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--database", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--season", type=int, required=True)
    args = cli.parse_args()
    if args.database.resolve() == args.output.resolve():
        cli.error("the input response cache and prepared database need distinct paths")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as scratch:
        target = Path(scratch) / "games.sqlite3"
        years = materialize(args.database, target, args.season)
        target.replace(args.output)
    print(f"Prepared {len(years)} CollegeBasketballData seasons")
