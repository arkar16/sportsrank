"""Compare rendered football watch guides with the official games export and retained API responses."""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3
import zlib

from lxml import html

from cfb.historical_rebuild import _school, local_day
from cfb.reference_archive import _score
from excitement_grades import read_number


def inventory(export, caches):
    fixtures = defaultdict(dict)
    export_names = defaultdict(set)
    current_names = {}
    with export.open(encoding="utf-8-sig", newline="") as stream:
        for r in csv.DictReader(stream):
            year = int(r["season"])
            for side in ("home", "away"):
                export_names[year, r[side + "_team_id"]].add(_school(r[side + "_team"]))
            if year >= 1897 and "fbs" not in (
                r["home_classification"],
                r["away_classification"],
            ):
                continue
            if r["status"] == "completed":
                fixtures[year][r["id"]] = (
                    r["start_date"],
                    _school(r["home_team"]),
                    _school(r["away_team"]),
                    _score(r["home_points"]),
                    _score(r["away_points"]),
                )
    latest = {}
    for cache in caches:
        with closing(
            sqlite3.connect(cache.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            for year, stamp, body in db.execute(
                "SELECT season,retrieved_utc,body FROM responses WHERE endpoint='games' AND season_type='both'"
            ):
                if year in latest and latest[year] > stamp:
                    continue
                latest[year] = stamp
                games = json.loads(zlib.decompress(body))
                for g in games:
                    if year >= 1897 and "fbs" not in (g["homeClassification"], g["awayClassification"]):
                        continue
                    for side in ("home", "away"):
                        name = _school(g[side + "Team"])
                        team_id = str(g[side + "Id"])
                        for old in export_names[year, team_id]:
                            if current_names.setdefault((year, old), name) != name:
                                raise ValueError(
                                    f"ambiguous CFBD identity for {year}/{old}"
                                )
                fixtures[year] = {
                    str(g["id"]): (
                        g["startDate"],
                        _school(g["homeTeam"]),
                        _school(g["awayTeam"]),
                        g["homePoints"] if g["completed"] else None,
                        g["awayPoints"] if g["completed"] else None,
                    )
                    for g in games
                    if (
                        year < 1897
                        or "fbs" in (g["homeClassification"], g["awayClassification"])
                    )
                }
    return {year: games for year, games in fixtures.items() if games}, current_names


def verify(export, caches, site):
    source, current_names = inventory(export, caches)
    pages = {
        int(p.parents[1].name): p
        for p in (site / "cfb/years").glob("*/excitement/index.html")
    }
    differences = []
    totals = Counter()
    if set(source) != set(pages):
        differences.append(("seasons", sorted(set(source) ^ set(pages))))
    for year in sorted(set(source) & set(pages)):
        raw = source[year]
        if year < 1897:
            expected = Counter(
                (gid, home, away, h, a)
                for gid, (_, home, away, h, a) in raw.items()
                if h is not None
            )
        else:
            # A small number of CFBD IDs describe the same played game. Keep
            # separate rematches on different dates, even with the same final.
            unique = {
                (local_day(stamp), home, away, h, a)
                for stamp, home, away, h, a in raw.values()
            }
            expected = Counter((home, away, h, a) for _, home, away, h, a in unique)
        actual = Counter()
        doc = html.parse(pages[year], parser=html.HTMLParser(encoding="utf-8"))
        for row in doc.xpath("//table/tbody/tr"):
            cells = [c.text_content().strip() for c in row.xpath("./td")]
            away, home, bev = cells[1:4]
            home = current_names.get((year, home), home)
            away = current_names.get((year, away), away)
            assert 0 <= float(read_number("cfb", "BEV", bev.split(" · ")[0])) <= 100, (year, cells)
            if len(cells) == 6:
                a, h = map(int, cells[4].split("–"))
                value, tier = cells[5].split(" · ")
                value = read_number("cfb", "AEV", value)
                assert 0 <= float(value.rstrip("*")) <= 100 and (
                    tier == "Play-by-play"
                ) != value.endswith("*"), (year, cells)
                totals[tier] += 1
            else:
                assert len(cells) == 4, (year, cells)
                a = h = None
                totals["pending"] += 1
            key = (home, away, h, a)
            if year < 1897:
                key = (row.get("data-cfbd-game"), *key)
            actual[key] += 1
        if actual != expected:
            differences.append(
                (
                    year,
                    list((expected - actual).items())[:5],
                    list((actual - expected).items())[:5],
                )
            )
    print(len(pages), "guides", dict(totals), "differing seasons", len(differences))
    for item in differences:
        print(item)
    if differences:
        raise SystemExit(1)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--export", type=Path, required=True, help="CollegeFootballData games.csv"
    )
    cli.add_argument("--cache", type=Path, action="append", default=[])
    cli.add_argument("--site", type=Path, default=Path("website"))
    args = cli.parse_args()
    verify(args.export, args.cache, args.site)
