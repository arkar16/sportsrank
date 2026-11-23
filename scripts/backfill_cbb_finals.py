"""Rebuild final 1981–2019 CORS standings from pinned historical compact results."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, time, timedelta
from hashlib import sha256
from itertools import chain
from pathlib import Path
from zoneinfo import ZoneInfo

from cbb.blend import iter_blended_seasons
from cbb.replay import _replay, CARRYOVER_STRENGTH, HOME_ADVANTAGE, MARGIN_WEIGHT
from cbb.source import Game, Season, Team

ROOT = Path(__file__).resolve().parents[1] / "cbb"
FIRST, LAST = 1981, 2019
# Calendar-day results are grouped in their Eastern week. Noon is a grouping
# convention only and never appears as a historical tipoff on the website.
EASTERN = ZoneInfo("America/New_York")
HASHES = {
    "1981-83/teams": "e60d2aba0ed022f287f38bf88e60b4b607b66926aa9e332a8987e10178844078",
    "1981-83/results": "36634d77e396811cbd5f35fe9fa0320f9503204f68964aa850bcb340f6fd031d",
    "1984/teams": "ea3577ae279e4f4304093558e878e49035b1dae2191114d9273f6e56c1a617d7",
    "1984/results": "c336ea6c06d7aa5aa127467e0a326d49d226b62fc30bc4461bbb2c83e9d194d7",
    "MNCAATourneyCompactResults": "5260a076b4e0937aa0420fd5eb72789b9931c662ed071c5060407458d8de8a05",
    "MRegularSeasonCompactResults": "f50b602151584b598d015a78355e7ffee533b031a66b8d9711ca01ad08b99e69",
    "MSeasons": "984af245340df5c304348c2cc1899e770d5511ce0622de022591834b6e23a579",
    "MSecondaryTourneyCompactResults": "141e274ebe5a17266e302857d7c3a24ad20bd78692feb926465de946b1e53810",
    "MTeamConferences": "a798dcd8ac5ddfbcbe13fe6217054f05ccc8b952b0c662eb3f1973ae6be3f6bc",
    "MTeams": "2f5d3b1bc6bf6afbf80a6900f1aa1bfaebfa68b33a8d66dc29034800b1dd7b24",
}


def calculate(directory):
    def rows(name, source=directory):
        path = source / (name + ".csv")
        if sha256(path.read_bytes()).hexdigest() != HASHES[name]:
            raise ValueError(f"Unexpected historical input: {name}")
        with path.open(encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)

    names = {int(r["TeamID"]): r["TeamName"] for r in rows("MTeams")}
    # These programs left Division I before the March Mania series begins.
    names.update({10001: "Baltimore", 10002: "West Chester State", 10003: "Catholic"})
    with (ROOT / "archive_programs.csv").open() as f:
        names.update((int(r["historical_id"]), r["school"]) for r in csv.DictReader(f))
    starts = {int(r["Season"]): datetime.strptime(r["DayZero"], "%m/%d/%Y").date() for r in rows("MSeasons")}
    starts.update((year, datetime(year - 1, 10, 31).date()) for year in range(FIRST, 1985))
    historical_inputs = ROOT / "historical_inputs"
    rosters = defaultdict(dict)
    for r in chain(rows("1981-83/teams", historical_inputs), rows("1984/teams", historical_inputs), rows("MTeamConferences")):
        year, tid = int(r["Season"]), int(r["TeamID"])
        if FIRST <= year <= LAST:
            rosters[year][tid] = Team(tid, names[tid], r["ConfAbbrev"])
    games = defaultdict(list)
    records = defaultdict(Counter)
    seen = set()
    for results in (rows("1981-83/results", historical_inputs), rows("1984/results", historical_inputs), rows("MRegularSeasonCompactResults"),
                    rows("MNCAATourneyCompactResults"), rows("MSecondaryTourneyCompactResults")):
        for r in results:
            year = int(r["Season"])
            if not FIRST <= year <= LAST:
                continue
            w, l, ws, ls, day = (int(r[k]) for k in ("WTeamID", "LTeamID", "WScore", "LScore", "DayNum"))
            site = r["WLoc"]
            key = year, day, min(w, l), max(w, l)
            if (key in seen or site not in ("H", "N", "A") or ws <= ls or w == l
                    or not {w, l} <= rosters[year].keys()):
                raise ValueError(f"Invalid historical game {key}")
            seen.add(key)
            h, a, score = (l, w, (ls, ws)) if site == "A" else (w, l, (ws, ls))
            start = datetime.combine(starts[year] + timedelta(days=day), time(12), EASTERN)
            games[year].append(Game(len(seen), start, h, a, site == "N", "final", score))
            records[year, w]["wins"] += 1
            records[year, l]["losses"] += 1

    def baseline():
        prior = ()
        for year in range(FIRST, LAST + 1):
            season = Season(year, rosters[year], tuple(sorted(games[year], key=lambda g: (g.start_utc, g.id))))
            replay = _replay(season, prior, HOME_ADVANTAGE, CARRYOVER_STRENGTH, MARGIN_WEIGHT)
            prior = replay["final_rankings"]
            yield replay

    keys = {(year, tid): tid for year, roster in rosters.items() for tid in roster}
    seed = {"teams": {}, "xx": 0., "xy": 0.}
    for season in iter_blended_seasons(baseline(), keys, seed):
        year = season["season"]
        final = season["final_rankings"]
        if {r["team_id"] for r in final} != rosters[year].keys():
            raise ValueError(f"Incomplete final roster in {year}")
        for rank, r in enumerate(final, 1):
            rec = records[year, r["team_id"]]
            if r["rank"] != rank or any(r[k] != rec[k] for k in ("wins", "losses")):
                raise ValueError(f"Historical standing disagrees with results: {year} {r['school']}")
            yield year, rank, r["team_id"], r["school"], r["conference"], r["wins"], r["losses"], f"{r['cors']:.2f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    results = list(calculate(args.directory))
    with (ROOT / "archive_finals.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(("season", "rank", "team_id", "school", "conference", "wins", "losses", "cors"))
        writer.writerows(results)


if __name__ == "__main__":
    main()
