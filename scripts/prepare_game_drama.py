"""Prepare score-clock evidence from a local, pinned ESPN game-summary archive."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from hashlib import sha1, sha256
import json
from pathlib import Path
import re
import subprocess

from cfb.excitement import normalize_timeline
from cfb.game_excitement import DATA, Matchup, flow_inputs
from cfb.reference_archive import _rows

EXCEPTION = re.compile(r"\b(?:forfeit(?:ed)?|shortened|abandoned)\b|game (?:was )?called (?:early|due to)", re.I)
POINTS = {"touchdown": {6, 7, 8}, "field-goal": {3}, "safety": {2}, "extra-point": {1},
          "two-point-conversion": {2}, "defensive-two-point-conversion": {2}}


def cumulative_quarters(teams, final):
    lines = [[int(q["displayValue"]) for q in team.get("linescores", [])] for team in teams]
    if not lines[0] or not lines[1]:
        return None
    if (len(lines[0]) != len(lines[1]) or len(lines[0]) < 4 or
            any(min(points) < 0 or sum(points) != score for points, score in zip(lines, final))):
        raise ValueError("period totals disagree with the final")
    cumulative = []
    home = away = 0
    for hp, ap in zip(*lines):
        home, away = home + hp, away + ap
        cumulative.append((home, away))
    if any(home != away for home, away in cumulative[3:-1]):
        raise ValueError("overtime period follows an untied score")
    return cumulative


def scoring_states(scoring, totals, team_ids):
    """Validate the complete scoring summary against both teams' period totals."""
    by_period = defaultdict(list)
    score = [0, 0]
    seen = set()
    prior_period = 0
    for play in scoring:
        period = play["period"]["number"]
        if not 1 <= period <= len(totals) or period < prior_period or not play.get("id") or play["id"] in seen:
            raise ValueError("ambiguous scoring order")
        seen.add(play["id"])
        points = [play["homeScore"], play["awayScore"]]
        side = team_ids.index(str(play["team"]["id"]))
        gain = points[side] - score[side]
        if gain not in POINTS.get(play["scoringType"]["name"], ()) or points[1-side] != score[1-side]:
            raise ValueError("unexplained scoring transition")
        seconds = None
        if period <= 4:
            clock = play["clock"]
            remaining = clock["value"]
            if not (type(remaining) in (int, float) and 0 <= remaining <= 900 and int(remaining) == remaining):
                raise ValueError("invalid scoring clock")
            if clock["displayValue"] != f"{int(remaining)//60}:{int(remaining)%60:02d}":
                raise ValueError("conflicting scoring clocks")
            seconds = period * 900 - int(remaining)
        by_period[period].append([seconds, *points, period])
        score, prior_period = points, period
    # A completed four-quarter game starts tied and the final score of each
    # period carries to its endpoint. Only timed scoring events split intervals.
    states = [[0, 0, 0, 1]]
    for period, total in enumerate(totals, 1):
        states.extend(by_period[period])
        if tuple(states[-1][1:3]) != total:
            raise ValueError("scoring summary misses a period total")
        if period == 4:
            states.append([3600, *total, 4])
    return states


def supports_regulation(summary):
    """Corroborate the listed game format with clocks in all four quarters."""
    clocks = defaultdict(list)
    drives = summary.get("drives", {})
    current = [drives["current"]] if isinstance(drives.get("current"), dict) else []
    for drive in [*drives.get("previous", []), *current]:
        for play in [drive.get("start", {}), *drive.get("plays", []), drive.get("end", {})]:
            period = play.get("period", {}).get("number")
            stamp = play.get("clock", {}).get("displayValue", "")
            if period in (1, 2, 3, 4) and re.fullmatch(r"(?:[0-9]|1[0-5]):[0-5][0-9]", stamp):
                minutes, seconds = map(int, stamp.split(":"))
                clocks[period].append(minutes * 60 + seconds)
    # A shortened quarter or a game called well before the end cannot qualify
    # solely because ESPN's generic football format says "four quarters".
    return (all(clocks[p] and 840 < max(clocks[p]) <= 900 for p in range(1, 5))
            and min(clocks[4]) <= 60)


def game_evidence(summary, row, year):
    header = summary["header"]
    if (header["id"] != row["game_id"] or header["season"]["year"] != year or
            len(header["competitions"]) != 1):
        raise ValueError("game summary identity mismatch")
    comp = header["competitions"][0]
    if comp["id"] != header["id"]:
        raise ValueError("competition ID differs from the game")
    status = comp["status"]["type"]
    regulation = summary.get("format", {}).get("regulation", {})
    notes = [summary.get("gameNotes", []), comp.get("notes", []), status]
    if (status.get("completed") is not True or status.get("name") != "STATUS_FINAL" or
            (regulation.get("periods"), regulation.get("clock")) != (4, 900) or
            EXCEPTION.search(json.dumps(notes)) or not supports_regulation(summary)):
        return None
    sides = {team["homeAway"]: team for team in comp["competitors"]}
    teams = [sides["home"], sides["away"]]
    ids = [str(t["id"]) for t in teams]
    final = [int(t["score"]) for t in teams]
    if (len(comp["competitors"]) != 2 or ids != [row["home_id"], row["away_id"]] or
            final != [int(row["home_score"]), int(row["away_score"])]):
        raise ValueError("game summary teams or scores disagree with schedule")
    try:
        totals = cumulative_quarters(teams, final)
    except (KeyError, ValueError):
        return None  # Contradictory independent totals require reconciliation.
    evidence = {"game_id": header["id"], "final": final, "quarters": totals[:3] if totals else None,
                "overtime": len(totals) > 4 if totals else None}
    if totals is None:
        evidence["reduced_because"] = "period scores unavailable"
        return evidence
    try:
        evidence["states"] = scoring_states(summary["scoringPlays"], totals, ids)
        qualified, points = flow_inputs(evidence, year, "preparation")
        normalize_timeline(points, qualified)
    except (KeyError, TypeError, ValueError) as error:
        evidence.pop("states", None)
        evidence["reduced_because"] = str(error)
    return evidence


def prepare(site: Path, inputs: Path, repository: Path, year: int, pins: dict, summaries: Path | None = None):
    schedule_path = inputs / f"schedule-{year}.csv"
    if sha256(schedule_path.read_bytes()).hexdigest() != pins["schedule"]["sha256"]:
        raise ValueError(f"schedule differs from pinned {year} snapshot")
    listing = subprocess.check_output(
        ["git", "-c", "protocol.allow=never", "-C", str(repository), "ls-tree", "-r", pins["revision"], "--", "cfb/json/raw"],
        text=True,
    )
    objects = {path: metadata.split()[2] for metadata, path in (line.split("\t") for line in listing.splitlines())}
    marks = json.loads((site / "cfb/assets/program-marks/manifest.json").read_bytes())["schools"]
    schools = {str(m["cfbd_id"]): m["school"] for m in marks}
    indexed, used = defaultdict(list), set()
    with schedule_path.open() as stream:
        for row in csv.DictReader(stream):
            if int(row["season"]) != year or row["game_id"] in used:
                raise ValueError("wrong season or duplicate schedule ID")
            used.add(row["game_id"])
            if row["status"] == "STATUS_FINAL":
                indexed[schools.get(row["home_id"]), schools.get(row["away_id"]),
                        row["home_score"], row["away_score"]].append(row)
    directory = site / f"cfb/years/{year}"
    games, used = [], set()
    for row in _rows(directory / f"data/results/{year}_FBS_results.html"):
        if any(row.get(k, "").lower() != "fbs" for k in ("home_division", "away_division")):
            continue
        home, away = row["home_team"], row["away_team"]
        hs, aws = row["home_score"], row["away_score"]
        matches = indexed[home, away, hs, aws] + indexed[away, home, aws, hs]
        if row.get("game_id", "").isdigit():
            matches = [m for m in matches if m["game_id"] == row["game_id"]]
        if len(matches) != 1:
            continue
        schedule = matches[0]
        gid = schedule["game_id"]
        if gid in used:
            raise ValueError(f"ambiguous CORS game for ESPN {gid}")
        used.add(gid)
        relative = f"cfb/json/raw/{gid}.json"
        if relative not in objects:
            continue
        local = repository / relative
        if summaries is not None and not local.is_file():
            local = summaries / relative
        raw = local.read_bytes()
        if sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() != objects[relative]:
            raise ValueError(f"summary differs from pinned revision for {gid}")
        evidence = game_evidence(json.loads(raw), schedule, year)
        if evidence:
            if schools[schedule["home_id"]] == away:
                evidence["source_sides_reversed"] = True
                evidence["final"].reverse()
                if evidence["quarters"]:
                    evidence["quarters"] = [(a, h) for h, a in evidence["quarters"]]
                if "states" in evidence:
                    evidence["states"] = [[t, a, h, p] for t, h, a, p in evidence["states"]]
            key = Matchup(int(row["week"]), row["home_team"], row["away_team"], row["neutral_site"].lower() == "true")
            games.append({"matchup": list(key), "source_blob": objects[relative], **evidence})
    return {"season": year, "source": pins["revision"], "schema": "espn-scoring-summary/v1",
            "games": sorted(games, key=lambda row: row["matchup"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True, help="directory of pinned season schedule CSVs")
    parser.add_argument("--espn-repo", type=Path, required=True, help="checkout containing retained game summaries")
    parser.add_argument("--summaries", type=Path, help="directory containing individually retained blobs from the same revision")
    parser.add_argument("--site", type=Path, default=Path("website"))
    parser.add_argument("--output", type=Path, default=DATA)
    parser.add_argument("seasons", type=int, nargs="+")
    args = parser.parse_args()
    pins = json.loads((DATA / "sources.json").read_bytes())
    args.output.mkdir(parents=True, exist_ok=True)
    for year in args.seasons:
        result = prepare(args.site, args.inputs, args.espn_repo, year, pins[str(year)], args.summaries)
        path = args.output / f"{year}.json"
        header = {k: v for k, v in result.items() if k != "games"}
        # One game per line keeps source refreshes reviewable.
        rows = [json.dumps(row, separators=(",", ":"), ensure_ascii=False) for row in result["games"]]
        path.write_text(json.dumps(header)[:-1] + ',"games":[\n' + ",\n".join(rows) + "\n]}\n")
        print(f"{year}: {sum('states' in r for r in result['games'])} measured of {len(rows)} qualified finals")


if __name__ == "__main__":
    main()
