"""Reduce pinned hoopR play-by-play and schedules to independently verifiable game measurements."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from hashlib import file_digest, sha256
from itertools import groupby
import json
from pathlib import Path
import re
import sqlite3

from cbb.game_excitement import local_date
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
    return cumulative_box(values, final, periods)


def cumulative_box(values, final, periods):
    if any(len(line) != periods or any(type(v) not in (int, float) or v < 0 or int(v) != v for v in line)
           or sum(line) != score
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


def cached_periods(db, year):
    """Index the retained CollegeBasketballData period boxes and fingerprint their inputs."""
    boxes = {}
    digest = sha256()
    for start, raw in db.execute(
        "SELECT start_utc, raw_json FROM games WHERE season = ? AND status = 'final' ORDER BY id", (year,)
    ):
        game = json.loads(raw)
        gid = str(game.get("sourceId") or "")
        lines = game.get("homePeriodPoints"), game.get("awayPeriodPoints")
        if not gid.isdecimal() or not all(lines) or _EXCEPTION.search(game.get("gameNotes") or ""):
            continue
        teams = game["homeTeamId"], game["awayTeamId"]
        final = game["homePoints"], game["awayPoints"]
        record = local_date(start), teams, final, lines
        encoded = json.dumps([gid, *record], separators=(",", ":"))
        digest.update((encoded + "\n").encode("ascii"))
        if int(gid) in boxes:
            raise ValueError(f"duplicate cached period box for {gid}")
        boxes[int(gid)] = record
    return boxes, digest.hexdigest()


def matched_box(record, day, teams, final, periods):
    if record is None:
        return None
    dated, pair, score, lines = record
    if dated != day.isoformat():
        return None
    if pair == teams[::-1]:
        score, lines = score[::-1], lines[::-1]
    elif pair != teams:
        return None
    if score != final:
        return None
    return cumulative_box(lines, final, periods)


def qualified_halftime(plays, event, boxes):
    """Verify the break score for a schedule-confirmed normal final, even with missing plays."""
    home, away, hs, aws, periods = event
    ends = {}
    break_sequence = first_second_sequence = opening_score = None
    for seq, p, label, clock, h, a, scoring, points, team, hid, aid, kind in plays:
        if (hid, aid) != (home, away):
            return None
        if p is None:
            continue
        if (type(p) is not int or not 1 <= p <= periods
                or (label and p <= 2 and label != ("1st Half", "2nd Half")[p - 1])
                or (label and p > 2 and "OT" not in label)):
            return None
        if p == 2 and first_second_sequence is None:
            first_second_sequence = seq
            if (isinstance(clock, str) and re.fullmatch(r"(?:20:00|1[89]:[0-5]\d)", clock)
                    and h is not None and a is not None):
                if scoring and team in (home, away) and points in (1, 2, 3):
                    opening_score = (h - points, a) if team == home else (h, a - points)
                elif scoring is False:
                    opening_score = (h, a)
        if (clock not in ("0:00", "00:00") or kind not in ("End Period", "End Game")
                or h is None or a is None):
            continue
        if (ends.setdefault(p, (h, a)) != (h, a) or not (0 <= h <= hs and 0 <= a <= aws)
                or (boxes and boxes[p - 1] != (h, a)) or (1 < p < periods and h != a)
                or (p == periods and (h, a) != (hs, aws))):
            return None
        if p == 1:
            break_sequence = seq
    if boxes:
        return boxes[0]
    if (break_sequence is not None and first_second_sequence is not None
            and break_sequence < first_second_sequence and ends[1] == opening_score):
        return opening_score
    return None


def measure(plays, event, boxes):
    """Qualify ordered plays against the two halves, terminal scores and official final."""
    home, away, hs, aws, periods = event
    regulation = [(0.0, 0)]
    margins = [0]
    last_period = last_sequence = 0
    last_clock = 0
    scores = (0, 0)
    half = None
    for play in plays:
        seq, p, label, clock, h, a, scoring, points, team, home_id, away_id = play[:11]
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


def prepare(inputs, output, year, pins, teams, cached):
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
        partial_boxes = {}
        select = ("game_id, CAST(game_date_time AS TIMESTAMPTZ)::DATE, home_id, away_id, home_score, "
                  "away_score, status_period, " + ", ".join(available(k) for k in
                  ("home_linescores", "away_linescores", "notes_headline", "format_regulation_periods",
                   "status_clock", "status_type_detail")))
        for gid, day, home, away, h, a, periods, hbox, abox, note, halves, clock, detail in schedule.filter(
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
            overtime = int(periods) - 2
            final_status = ("Final" if not overtime else "Final/OT" if overtime == 1 else f"Final/{overtime}OT")
            if halves == 2 and clock == 0 and detail == final_status:
                try:
                    other = matched_box(cached.get(gid), day, (teams[home], teams[away]), (h, a), periods)
                except ValueError:
                    continue
                if not (boxes and other and boxes != other):
                    partial_boxes[gid] = boxes or other

        plays = db.read_parquet(str(paths["plays"])).project(
            "game_id, game_play_number, period_number, period_display_value, clock_display_value, "
            "home_score, away_score, scoring_play, score_value, team_id, home_team_id, away_team_id, type_text"
        ).order("game_id, game_play_number").execute()
        def rows():
            while batch := plays.fetchmany(8192):
                yield from batch

        measured = {}
        # A complete period box can qualify even when the play file has no row for this game.
        limited = {gid: box[0] for gid, box in partial_boxes.items() if box}
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
                if gid in partial_boxes:
                    limited[gid] = qualified_halftime(timeline, event, partial_boxes[gid])
    dest = output / f"{year}.csv"
    with dest.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(COLUMNS)
        for gid, (day, (home, away, h, a, periods), boxes) in sorted(events.items()):
            if gid in measured:
                terms = measured[gid]
            elif limited.get(gid) is not None:
                terms = (*limited[gid], "", "", "")
            else:
                continue
            writer.writerow((day, gid, teams[home], teams[away], h, a, periods, *terms))
    return len(measured), skipped


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--inputs", required=True, type=Path)
    cli.add_argument("--database", required=True, type=Path, help="retained basketball team identities and period boxes")
    cli.add_argument("--output", type=Path, default=DATA)
    cli.add_argument("years", nargs="+", type=int)
    args = cli.parse_args()
    args.output.mkdir(exist_ok=True, parents=True)
    pins = json.loads((DATA / "sources.json").read_bytes())["seasons"]
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        pairs = db.execute("select distinct json_extract(raw_json, '$.sourceId'), id from teams")
        teams = {}
        for source_id, program in pairs:
            if str(source_id).isdecimal() and teams.setdefault(int(source_id), program) != program:
                raise ValueError(f"ambiguous basketball program for ESPN {source_id}")
        for year in args.years:
            pin = pins[str(year)]
            cached = {}
            if "cached_periods" in pin:
                cached, digest = cached_periods(db, year)
                if digest != pin["cached_periods"]:
                    raise ValueError(f"{year} cached period boxes differ from their frozen inputs")
            count, skipped = prepare(args.inputs, args.output, year, pin, teams, cached)
            print(year, count, "measured", skipped, flush=True)


if __name__ == "__main__":
    main()
