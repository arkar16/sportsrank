"""Reduce pinned hoopR play-by-play and schedules to independently verifiable game measurements."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from hashlib import file_digest
from itertools import groupby
import json
from pathlib import Path
import re
import sqlite3

from cfb.excitement import BASKETBALL, score_path_drama

DATA = Path(__file__).resolve().parents[1] / "cbb/excitement_data"
COLUMNS = ("date", "game_id", "home_id", "away_id", "home_score", "away_score", "periods",
           "half_home", "half_away", "tension", "lead_changes", "winner_deficit")
_PERIOD_POINTS = re.compile(r"""['"]value['"]\s*:\s*(-?\d+(?:\.\d+)?)\b""")
_EXCEPTION = re.compile(r"\b(?:forfeit(?:ed)?|abandoned|exhibition|shortened)\b", re.I)


def period_totals(home, away, final, periods):
    if home is None or away is None:
        return None
    values = [[float(v) for v in _PERIOD_POINTS.findall(s)] for s in (home, away)]
    if not any(values):
        return None
    if any(len(line) != periods or sum(line) != score or any(v < 0 or not v.is_integer() for v in line)
           for line, score in zip(values, final)):
        raise ValueError("box score period totals conflict with the final")
    result = []
    h = a = 0
    for hp, ap in zip(*values):
        h, a = h + int(hp), a + int(ap)
        result.append((h, a))
    if any(h != a for h, a in result[1:-1]):
        raise ValueError("overtime follows an untied box score")
    return result


def confirms_periods(plays, event, boxes):
    """Corroborate period lengths and endpoints for a game with incomplete scoring plays."""
    home, away, _, _, periods = event
    if not boxes:
        return False
    starts, ends = {}, {}
    for _, p, label, clock, h, a, _, _, _, hid, aid in plays:
        if (hid, aid) != (home, away) or not isinstance(p, int) or not 1 <= p <= periods:
            return False
        if (p <= 2 and label != ("1st Half", "2nd Half")[p - 1]) or (p > 2 and "OT" not in (label or "")):
            return False
        if not isinstance(clock, str) or not re.fullmatch(r"\d{1,2}:[0-5]\d", clock):
            continue
        minutes, seconds = map(int, clock.split(":"))
        remaining = minutes * 60 + seconds
        starts[p] = max(starts.get(p, 0), remaining)
        if remaining == 0:
            ends[p] = (h, a)
    return all((1200 if p <= 2 else 300) - 120 <= starts.get(p, -1) <= (1200 if p <= 2 else 300)
               and ends.get(p) == boxes[p - 1] for p in range(1, periods + 1))


def measure(plays, event, boxes):
    """Qualify ordered plays against the two halves, terminal scores and official final."""
    home, away, hs, aws, periods = event
    regulation = [(0.0, 0)]
    margins = [0]
    last_period = last_sequence = 0
    last_clock = 0
    scores = (0, 0)
    half = None
    for seq, p, label, clock, h, a, scoring, points, team, home_id, away_id in plays:
        if (home_id, away_id) != (home, away) or seq is None or seq <= last_sequence:
            raise ValueError("conflicting participants or sequence")
        if p is None or not 1 <= p <= periods or clock is None:
            raise ValueError("missing period or clock")
        if (p <= 2 and label != ("1st Half", "2nd Half")[p - 1]) or (p > 2 and "OT" not in (label or "")):
            raise ValueError("unsupported period format")
        minutes, seconds = map(int, clock.split(":"))
        remaining = 60 * minutes + seconds
        length = 1200 if p <= 2 else 300
        if seconds not in range(60) or not 0 <= remaining <= length:
            raise ValueError("invalid period clock")
        if p != last_period:
            if p != last_period + 1 or (last_period and last_clock != 0) or remaining < length - 120:
                raise ValueError("incomplete or out-of-order period")
            if last_period and boxes and scores != boxes[last_period - 1]:
                raise ValueError("play-by-play disagrees with the period score")
            if last_period == 1:
                half = scores
            if last_period == 2:
                regulation.append((40.0, scores[0] - scores[1]))
            if p > 2 and scores[0] != scores[1]:
                raise ValueError("overtime follows an untied period")
        elif remaining > last_clock:
            raise ValueError("clock moved backward")
        if h is None or a is None:
            raise ValueError("missing score")
        if scoring or (h, a) != scores:
            if not scoring or points not in (1, 2, 3) or team not in (home, away):
                raise ValueError("unexplained scoring transition")
            delta = (points, 0) if team == home else (0, points)
            if (h - scores[0], a - scores[1]) != delta:
                raise ValueError("score change disagrees with the scoring team or play")
            margins.append(h - a)
            if p <= 2:
                regulation.append(((p * 1200 - remaining) / 60, h - a))
        scores, last_sequence, last_period, last_clock = (h, a), seq, p, remaining
    if last_period != periods or last_clock != 0 or scores != (hs, aws) or half is None:
        raise ValueError("final period, clock or score is incomplete")
    if periods == 2:
        regulation.append((40.0, hs - aws))
    tension, changes, deficit = score_path_drama(regulation, margins, BASKETBALL)
    return (*half, format(tension, ".12g"), changes, deficit)


def prepare(inputs, output, year, pins, teams):
    import duckdb  # Input preparation only. The normal build reads compact CSVs.

    paths = {}
    for kind, prefix in (("plays", "play_by_play"), ("schedule", "mbb_schedule")):
        path = inputs / kind / f"{prefix}_{year}.parquet"
        with path.open("rb") as source:
            if file_digest(source, "sha256").hexdigest() != pins[kind]:
                raise ValueError(f"{year} {kind} differs from its frozen input")
        paths[kind] = path
    with duckdb.connect(config={"threads": 2}) as db:
        schedule = db.read_parquet(str(paths["schedule"]))
        columns = set(schedule.columns)
        def available(name):
            return name if name in columns else "NULL"
        # The release's game_date can be UTC or Eastern depending on source era.
        # The timestamp preserves the date used by the SportsRank game tables.
        db.execute("SET TimeZone='America/New_York'")
        events = {}
        select = ("game_id, CAST(game_date_time AS TIMESTAMPTZ)::DATE, home_id, away_id, home_score, "
                  "away_score, status_period, " + ", ".join(available(k) for k in
                  ("home_linescores", "away_linescores", "notes_headline")))
        for gid, day, home, away, h, a, periods, hbox, abox, note in schedule.filter(
            "status_type_completed and status_type_name='STATUS_FINAL'"
        ).project(select).fetchall():
            if (day is None or home not in teams or away not in teams or periods is None
                    or int(periods) != periods or periods < 2 or h is None or a is None or h == a
                    or _EXCEPTION.search(note or "")):
                continue
            if gid in events:
                raise ValueError(f"duplicate completed game {gid}")
            try:
                boxes = period_totals(hbox, abox, (h, a), periods)
            except ValueError:
                continue
            events[gid] = (day, (home, away, int(h), int(a), int(periods)), boxes)

        plays = db.read_parquet(str(paths["plays"])).project(
            "game_id, game_play_number, period_number, period_display_value, clock_display_value, "
            "home_score, away_score, scoring_play, score_value, team_id, home_team_id, away_team_id"
        ).order("game_id, game_play_number").execute()
        def rows():
            while batch := plays.fetchmany(8192):
                yield from batch

        measured = {}
        limited = set()
        skipped = Counter()
        for gid, group in groupby(rows(), key=lambda row: row[0]):
            if gid not in events:
                continue
            _, event, boxes = events[gid]
            timeline = [r[1:] for r in group]
            try:
                measured[gid] = measure(timeline, event, boxes)
            except (TypeError, ValueError) as exc:
                skipped[str(exc)] += 1
                if confirms_periods(timeline, event, boxes):
                    limited.add(gid)
    dest = output / f"{year}.csv"
    with dest.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(COLUMNS)
        for gid, (day, (home, away, h, a, periods), boxes) in sorted(events.items()):
            if gid in measured:
                terms = measured[gid]
            elif gid in limited:
                terms = (*boxes[0], "", "", "")
            else:
                continue
            writer.writerow((day, gid, teams[home], teams[away], h, a, periods, *terms))
    return len(measured), skipped


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--inputs", required=True, type=Path)
    cli.add_argument("--database", required=True, type=Path, help="retained basketball team identity crosswalk")
    cli.add_argument("--output", type=Path, default=DATA)
    cli.add_argument("years", nargs="+", type=int)
    args = cli.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        pairs = db.execute("select distinct json_extract(raw_json, '$.sourceId'), id from teams")
        teams = {}
        for source_id, program in pairs:
            if str(source_id).isdecimal() and teams.setdefault(int(source_id), program) != program:
                raise ValueError(f"ambiguous basketball program for ESPN {source_id}")
    args.output.mkdir(exist_ok=True, parents=True)
    pins = json.loads((DATA / "sources.json").read_bytes())["seasons"]
    for year in args.years:
        count, skipped = prepare(args.inputs, args.output, year, pins[str(year)], teams)
        print(year, count, "measured", skipped, flush=True)


if __name__ == "__main__":
    main()
