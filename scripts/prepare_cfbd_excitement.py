"""Reduce official CollegeFootballData games and plays to original CORS drama measurements."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from dataclasses import replace
from functools import lru_cache
from itertools import accumulate
import json
from pathlib import Path
import re

from cfb.excitement import QualifiedFinal, QualifiedPregame, TimelinePoint, calculate_full_aev
from cfb.historical_rebuild import _school
from cfb.reference_archive import _rows, _score

PROVIDER = "CollegeFootballData"
PRE = QualifiedPregame(1, 1, None, "measurement", "cfbd")
ABNORMAL = re.compile(r"shortened|abandoned|forfeit|shorten.*quarter", re.I)


def period_scores(game):
    try:
        home, away = (json.loads(game[side + "_line_scores"]) for side in ("home", "away"))
        if (
            len(home) != len(away)
            or len(home) < 4
            or any(type(v) is not int or v < 0 for v in home + away)
            or (sum(home), sum(away)) != (game["h"], game["a"])
        ):
            return ()
        ends = tuple(zip(accumulate(home), accumulate(away)))
        if any(h != a for h, a in ends[3:-1]):
            return ()
        return ends
    except (ValueError, TypeError):
        return ()


@lru_cache(maxsize=2048)
def remaining(clock):
    match = re.fullmatch(r"\{'seconds': (\d+), 'minutes': (\d+)\}", clock)
    if match:
        second, minute = map(int, match.groups())
    else:
        data = json.loads(clock.replace("'", '"'))
        second, minute = data["seconds"], data["minutes"]
    if (
        type(minute) is not int
        or type(second) is not int
        or not (0 <= second < 60 and 0 <= minute * 60 + second <= 900)
    ):
        raise ValueError("invalid CFBD football clock")
    return minute * 60 + second


def measure(game, plays):
    ends = period_scores(game)
    if not ends or ABNORMAL.search(game.get("notes") or "") or not plays:
        return None
    home, away = game["home_team"], game["away_team"]
    current = (0, 0)
    states = [(0, 0, 0, 1)]
    observed = {}
    period_seen = set()
    previous_order = None
    seen = {}
    try:
        for play in sorted(plays, key=lambda p: (int(p["driveNumber"]), int(p["playNumber"]), int(p["id"]))):
            identity = play["id"]
            signature = tuple(
                play[k]
                for k in ("period", "clock", "offense", "defense", "offenseScore", "defenseScore", "scoring")
            )
            if identity in seen:
                if seen[identity] != signature:
                    return None
                continue
            seen[identity] = signature
            period = int(play["period"])
            if (play["home"], play["away"]) != (home, away) or not 1 <= period <= len(ends):
                return None
            if (play["offense"], play["defense"]) == (home, away):
                score = int(play["offenseScore"]), int(play["defenseScore"])
            elif (play["offense"], play["defense"]) == (away, home):
                score = int(play["defenseScore"]), int(play["offenseScore"])
            else:
                return None
            period_seen.add(period)
            if str(play["scoring"]).lower() != "true":
                # Kickoffs and other non-scoring snaps can carry stale or provisional score fields.
                observed[period] = current
                continue
            if score != current:
                change = (score[0] - current[0], score[1] - current[1])
                if min(change) != 0 or not 1 <= max(change) <= 8:
                    return None
                elapsed = (period * 900 - remaining(play["clock"])) if period <= 4 else None
                key = (period, elapsed or 0)
                if previous_order is not None and key < previous_order:
                    return None
                previous_order = key
                states.append((elapsed, *score, period))
                current = score
            observed[period] = current
        if (
            period_seen != set(range(1, len(ends) + 1))
            or tuple(observed[i] for i in sorted(observed)) != ends
        ):
            return None
        regulation = [s for s in states if s[3] <= 4]
        regulation.append((3600, *ends[3], 4))
        states = regulation + [s for s in states if s[3] > 4]
        points = tuple(
            TimelinePoint(
                h, a, i, elapsed_minute=t / 60 if t is not None else None, overtime=t is None, period=p
            )
            for i, (t, h, a, p) in enumerate(states)
        )
        final = QualifiedFinal(
            str(game["id"]),
            int(game["season"]),
            game["h"],
            game["a"],
            "cfbd",
            overtime=len(ends) > 4,
            quarter_scores=ends[:3] if game["h"] != game["a"] else None,
        )
        drama = calculate_full_aev(PRE, final, points).components.drama_points
        tie = None
        if len(ends) > 4:
            tied = replace(
                final, home_score=ends[3][0], away_score=ends[3][1], overtime=False, quarter_scores=None
            )
            tie = calculate_full_aev(PRE, tied, points[: len(regulation)]).components.drama_points
        return drama, tie
    except (IndexError, KeyError, ValueError, TypeError):
        return None


def prep(data, private, site):
    games = defaultdict(dict)
    with (data / "games.csv").open(encoding="utf-8-sig", newline="") as source:
        for g in csv.DictReader(source):
            if g["status"] != "completed" or not g["home_points"] or not g["away_points"]:
                continue
            g["h"], g["a"] = int(float(g["home_points"])), int(float(g["away_points"]))
            games[int(g["season"])][g["id"]] = g
    private.mkdir(parents=True, exist_ok=True)
    for year, roster in sorted(games.items()):
        results = site / f"cfb/years/{year}/data/results/{year}_FBS_results.html"
        if not results.is_file():
            continue
        path = private / f"{year}.json"
        by_names = defaultdict(list)
        for g in roster.values():
            by_names[_school(g["home_team"]), _school(g["away_team"]), g["h"], g["a"]].append(g)
        matched = {}
        for row in _rows(results):
            h, a = _score(row.get("home_score")), _score(row.get("away_score"))
            if h is None or a is None:
                continue
            home, away = _school(row["home_team"]), _school(row["away_team"])
            shape = home, away, h, a
            game = roster.get(row.get("game_id"))
            if game is None:
                matches = by_names[shape]
                if len(matches) != 1:
                    continue
                game = matches[0]
            if (_school(game["home_team"]), _school(game["away_team"]), game["h"], game["a"]) != shape:
                continue
            ends = period_scores(game)
            if not ends:
                continue
            key = int(row["week"]), home, away, row["neutral_site"].lower() == "true"
            matched[game["id"]] = dict(key=key, final=[h, a], ends=ends)
        for file in sorted((data / "plays" / str(year)).glob("*.csv")):
            by_id = defaultdict(list)
            with file.open(encoding="utf-8-sig", newline="") as stream:
                for play in csv.DictReader(stream):
                    if play["gameId"] in matched:
                        by_id[play["gameId"]].append(play)
            for gid, plays in by_id.items():
                metric = measure(roster[gid], plays)
                if metric is not None:
                    matched[gid].update(drama=metric[0], tied_drama=metric[1])
        path.write_text(json.dumps(matched, sort_keys=True, separators=(",", ":")) + "\n")
        if matched:
            print(
                year,
                "periods",
                len(matched),
                "measured",
                sum("drama" in g for g in matched.values()),
                flush=True,
            )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--export", type=Path, required=True)
    cli.add_argument("--private", type=Path, required=True)
    cli.add_argument("--site", type=Path, default=Path("website"))
    args = cli.parse_args()
    prep(args.export, args.private, args.site)
