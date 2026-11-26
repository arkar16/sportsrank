"""Build CORS basketball measurements and freeze a CollegeBasketballData-only reference."""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import csv
from datetime import date, timedelta
from hashlib import sha256
from itertools import accumulate
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
import zlib

import numpy as np
from cfb.excitement_evaluation import _correlation, _ranks

from cbb.api_history import HOST
from cbb.halftime import FEATURES, HalftimePool
from cbb.source import period_evidence
from cfb.excitement import BASKETBALL, score_path_drama
from cfb.sparse_excitement import MarginPool
from scripts.cache_basketball_plays import FIELDS

TRAIN = (2024, 2026)
RESERVED = 2025
MEASURES = (
    "game_id",
    "home_id",
    "away_id",
    "home_score",
    "away_score",
    "periods",
    "tension",
    "lead_changes",
    "winner_deficit",
)
REFERENCE = ("game", "season", "overtime", *FEATURES, "drama")
ABNORMAL = re.compile(r"shortened|abandon|forfeit|exhibition", re.I)


def measure(game, shots):
    overtime, half = period_evidence(game)
    if half is None or ABNORMAL.search(game.get("gameNotes") or ""):
        return None
    ends = list(zip(accumulate(game["homePeriodPoints"]), accumulate(game["awayPeriodPoints"])))
    score = (0, 0)
    regulation, margins = [(0.0, 0)], [0]
    events = defaultdict(list)
    seen = {}
    try:
        for raw in shots:
            gid, period, clock, sec, h, a, team, is_home, scored, value = raw[1:11]
            if gid != game["id"] or type(period) is not int or not 1 <= period <= len(ends):
                return None
            identity = raw[0]
            if identity in seen:
                if seen[identity] != raw:
                    return None
                continue
            seen[identity] = raw
            length = 1200 if period <= 2 else 300
            minutes, seconds = map(int, clock.split(":"))
            if not (0 <= seconds < 60 and 0 <= sec <= length and minutes * 60 + seconds == sec):
                return None
            if type(h) is not int or type(a) is not int or min(h, a) < 0:
                return None
            events[period].append((-sec, h + a, identity, h, a, team, is_home, scored, value))
        for period, end in enumerate(ends, 1):
            if not events[period]:
                return None
            for minus_sec, _, _, h, a, team, is_home, scored, value in sorted(events[period]):
                if not scored:
                    if (h, a) != score:
                        return None
                    continue
                if (
                    value not in (1, 2, 3)
                    or type(is_home) is not bool
                    or team != game["homeTeamId" if is_home else "awayTeamId"]
                ):
                    return None
                delta = h - score[0], a - score[1]
                if delta != ((value, 0) if is_home else (0, value)):
                    return None
                score = h, a
                margins.append(h - a)
                if period <= 2:
                    regulation.append(((period * 1200 + minus_sec) / 60, h - a))
            if score != end:
                return None
            if period == 2:
                regulation.append((40.0, end[0] - end[1]))
        tension, leads, deficit = score_path_drama(regulation, margins, BASKETBALL)
        return dict(
            game_id=game["id"],
            home_id=game["homeTeamId"],
            away_id=game["awayTeamId"],
            home_score=score[0],
            away_score=score[1],
            periods=len(ends),
            tension=tension,
            lead_changes=leads,
            winner_deficit=deficit,
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def write_rows(path, fields, rows):
    with path.open("w", newline="") as stream:
        w = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return sha256(path.read_bytes()).hexdigest()


def read_measured(database, plays):
    result = defaultdict(dict)
    with (
        closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as box,
        closing(sqlite3.connect(plays.resolve().as_uri() + "?mode=ro", uri=True)) as events,
    ):
        for url, count, blob in events.execute("SELECT url,rows,compressed_json FROM responses ORDER BY url"):
            uri = urlsplit(url)
            query = parse_qs(uri.query)
            kind = uri.path.removeprefix("/plays/")
            if (uri.scheme, uri.netloc) != ("https", HOST) or kind not in ("date", "team", "tournament"):
                continue
            required = {"date"} if kind == "date" else {"season", kind}
            if set(query) - {"shootingPlaysOnly"} != required or any(len(v) != 1 for v in query.values()):
                continue
            if query.get("shootingPlaysOnly", ["true"]) != ["true"]:
                continue
            rows = json.loads(zlib.decompress(blob))
            if len(rows) != count:
                raise ValueError("corrupt CBBD plays response")
            by_game = defaultdict(list)
            for row in rows:
                raw = [row[k] for k in FIELDS] if isinstance(row, dict) else row
                if kind == "date" and raw[-1][:10] != query["date"][0]:
                    # CBBD also includes games at the following UTC midnight in a date response.
                    next_day = date.fromisoformat(query["date"][0]) + timedelta(days=1)
                    if raw[-1].startswith(f"{next_day}T00:00:00"):
                        continue
                    raise ValueError("CBBD play date mismatch")
                by_game[raw[1]].append(raw)
            for gid, shots in by_game.items():
                found = box.execute(
                    "SELECT raw_json FROM games WHERE id=? AND status='final'", (gid,)
                ).fetchone()
                if not found:
                    continue
                game = json.loads(found[0])
                if kind != "date" and int(query["season"][0]) != game["season"]:
                    raise ValueError("CBBD play season mismatch")
                if kind == "team" and query["team"][0] not in (game["homeTeam"], game["awayTeam"]):
                    raise ValueError("CBBD play team mismatch")
                if kind == "tournament" and query["tournament"][0] != game.get("tournament"):
                    raise ValueError("CBBD play tournament mismatch")
                record = measure(game, shots)
                if record is not None:
                    year = game["season"]
                    half = game["homePeriodPoints"][0] - game["awayPeriodPoints"][0]
                    delta = record["home_score"] - record["away_score"]
                    comparison = dict(
                        game=str(gid),
                        season=year,
                        overtime=int(record["periods"] > 2),
                        abs_margin=abs(delta),
                        total=record["home_score"] + record["away_score"],
                        halftime_margin=half if delta > 0 else -half,
                        drama=80 * record["tension"]
                        + 8 * min(1, record["lead_changes"] / 3)
                        + 4 * min(1, record["winner_deficit"] / BASKETBALL.comeback_margin),
                    )
                    pair = record, comparison
                    if result[year].setdefault(gid, pair) != pair:
                        raise ValueError("conflicting CBBD measurements for a game")
    return result


def metrics(estimates, observed):
    errors = np.asarray(estimates) - observed
    return dict(
        games=len(observed),
        mae=float(np.abs(errors).mean()),
        bias=float(errors.mean()),
        spearman=float(_correlation(_ranks(estimates), _ranks(observed))),
    )


def pregame_bonuses(database):
    from cbb.replay import replay_seasons
    from cbb.cors_v1 import cors_v1_seasons
    from cfb.excitement import matchup_quality

    with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        first = db.execute("SELECT MIN(season) FROM games").fetchone()[0]
    baseline = replay_seasons(database, RESERVED, first_season=first)
    season = cors_v1_seasons(database, baseline, api_only=True)[-1]
    ranks = {c["cutoff_utc"]: {r["team_id"]:r["rank"] for r in c["rows"]} for c in season["rankings"]}
    bonuses = {}
    for game in season["forecasts"]:
        delta = game["actual_home_margin"]
        if delta is None:
            continue
        pos = ranks[game["cutoff_utc"]]
        quality = matchup_quality(pos[game["home_team_id"]],pos[game["away_team_id"]])
        margin = game["home_margin"] * (1 if delta > 0 else -1)
        bonuses[str(game["game_id"])] = 3*quality + 3*min(1,max(0,-margin/BASKETBALL.close_margin))
    return bonuses


def build(database, plays, output):
    records = read_measured(database, plays)
    output.mkdir(parents=True, exist_ok=True)
    train = [pair[1] for year in TRAIN for _, pair in sorted(records[year].items())]
    if not train:
        raise ValueError("no qualified CollegeBasketballData training games")
    filename = "halftime-reference-v1.csv"
    checksum = write_rows(output / filename, REFERENCE, train)
    widths = {}
    pools = {}
    for ot in (0, 1):
        group = [r for r in train if r["overtime"] == ot]
        features = np.array([[r[k] for k in FEATURES] for r in group], dtype=float)
        if len(group) < 50:
            raise ValueError("insufficient comparable regulation or overtime games in training seasons")
        widths[ot] = np.maximum(
            1, np.percentile(features, 75, axis=0) - np.percentile(features, 25, axis=0)
        ).tolist()
        pools[ot] = HalftimePool(
            {r["game"]: i for i, r in enumerate(group)},
            features,
            np.array([r["drama"] for r in group]),
            widths[ot],
        )
    policy = dict(
        provider="CollegeBasketballData", training=list(TRAIN), file=filename, sha256=checksum, widths=widths
    )
    # Freeze training before evaluating the owner's reserved season. The reserved rows never enter these pools.
    frozen = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    policy["frozen_sha256"] = sha256(frozen).hexdigest()
    values = pregame_bonuses(database)
    held = [pair[1] for _, pair in sorted(records[RESERVED].items()) if pair[1]["game"] in values]
    if not held:
        raise ValueError("no reserved games with a pregame CORS checkpoint")
    final_pool = {
        ot: MarginPool(
            (r["game"], r["abs_margin"], r["drama"] + 2 * ot) for r in train if r["overtime"] == ot
        )
        for ot in (0, 1)
    }
    mixed = MarginPool((r["game"], r["abs_margin"], r["drama"] + 2 * r["overtime"]) for r in train)
    obs, half, final, hidden = [], [], [], []
    for r in held:
        gid, ot = r["game"], r["overtime"]
        bonus = values[gid]
        obs.append(r["drama"] + 2 * ot + bonus)
        value, _, _ = pools[ot].estimate(gid, *(r[k] for k in FEATURES))
        half.append(value + 2 * ot + bonus)
        final.append(final_pool[ot].estimate(r["abs_margin"], gid)[0] + bonus)
        hidden.append(mixed.estimate(r["abs_margin"], gid)[0] + bonus)
    policy.update(
        reserved=RESERVED,
        halftime=metrics(half, obs),
        final=metrics(final, obs),
        final_unknown_ot=metrics(hidden, obs),
    )
    m = policy["halftime"]
    policy["halftime_qualified"] = bool(m["mae"] <= 7 and abs(m["bias"]) <= 2 and m["spearman"] >= 0.85)
    policy["seasons"] = {}
    for year, data in sorted(records.items()):
        policy["seasons"][year] = write_rows(
            output / f"{year}.csv", MEASURES, (r[0] for _, r in sorted(data.items()))
        )
    (output / "policy.json").write_text(json.dumps(policy, sort_keys=False, indent=2) + "\n")
    print(
        "train",
        len(train),
        "seasons",
        {y: len(r) for y, r in sorted(records.items())},
        "halftime",
        m,
        "final",
        policy["final"],
        "unknown OT",
        policy["final_unknown_ot"],
        flush=True,
    )


def extend(database, plays, output, existing):
    # Adding historical measurements cannot refit a reference or change the reserved evaluation.
    policy = json.loads((existing/"policy.json").read_bytes())
    if policy["provider"] != "CollegeBasketballData":
        raise ValueError("unapproved basketball measurement source")
    records = read_measured(database,plays)
    if set(records).intersection((*TRAIN,RESERVED)):
        raise ValueError("historical additions overlap the frozen reference or reserved season")
    for year,data in sorted(records.items()):
        policy["seasons"][str(year)] = write_rows(output/f"{year}.csv",MEASURES,
                                                 (r[0] for _,r in sorted(data.items())))
    (output/"policy.json").write_text(json.dumps(policy,indent=2)+"\n")
    print("Qualified additional CBBD measurements", {y:len(r) for y,r in sorted(records.items())})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("database", "plays", "output"):
        parser.add_argument("--"+arg,required=True,type=Path)
    parser.add_argument("--extend",action="store_true",help="Add other seasons with the existing frozen reference")
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent) as directory:
        stage=Path(directory)
        if args.extend:
            extend(args.database,args.plays,stage,args.output)
        else:
            build(args.database,args.plays,stage)
            previous=args.output/"policy.json"
            if previous.exists() and (json.loads(previous.read_bytes())["frozen_sha256"] !=
                                      json.loads((stage/"policy.json").read_bytes())["frozen_sha256"]):
                raise ValueError("basketball training sample differs from the reserved-season reference")
        args.output.mkdir(parents=True,exist_ok=True)
        for file in sorted(stage.glob("*.csv")):
            file.replace(args.output/file.name)
        (stage/"policy.json").replace(args.output/"policy.json")
