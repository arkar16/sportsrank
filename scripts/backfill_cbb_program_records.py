"""Rebuild pre-1950 records from the pinned College Basketball History export.

Run uv run python -m scripts.backfill_cbb_program_records DIRECTORY where
DIRECTORY contains programs.csv from data/reference and the eligible team JSON
files from site/data/teams in the revision named by cbb.program_records.
"""
import argparse
from collections import Counter, defaultdict
import csv
from hashlib import sha256
import json
from pathlib import Path

from cbb.program_records import ROOT

# SHA-256 of the ordered source file digests, beginning with the program directory.
INPUT_HASH = "4699c7534c51fdb58201b5061122fb66df629b3bde582df13900a38743023e3c"
NAMES = {"Connecticut": "UConn", "Long Island": "Long Island University",
         "Miami (FL)": "Miami", "North Carolina State": "NC State"}
# Season summaries predating the compiled game logs, transcribed from the
# linked school and NCAA records. A blank conference means the source omits it.
FIRST_RECORDS = [
    (1893, "Geneva", "", 1, 0, 0, "", "geneva-founding"),
    (1894, "Chicago", "", 6, 1, 0, "", "chicago-founding"),
    (1895, "Temple", "", 8, 3, 0, "", "temple-founding"),
]


def record_text(w, l, t=0):
    return f"{w}–{l}" + (f"–{t}" if t else "")


def team_rows(team):
    program = team["program"]
    name = NAMES.get(program["display_name"], program["display_name"])
    counts = defaultdict(Counter)
    for g in team["games"]:
        counts[g["season_label"]][g["result"]] += 1
    exceptions = {r["season_label"]: r for field in ("historical_season_accounting", "historical_season_coverage")
                  for r in team.get(field, [])}
    for r in team["seasons"]:
        season = r["season_label"]
        year = int(season[:4]) + 1
        if year >= 1950:
            continue
        w, l, t = (r[k] for k in ("wins", "losses", "ties"))
        known = counts[season]
        if (w, l, t, r["games"]) != (known["W"], known["L"], known["T"], known.total()):
            raise ValueError(f"Season totals disagree with game log: {name} {season}")
        note = ""
        if season in exceptions:
            reported = exceptions[season]["official_record"]
            documented = record_text(w, l, t)
            w, l = reported["wins"], reported["losses"]
            t = reported["games"] - w - l
            if t < 0:
                raise ValueError(f"Invalid school record: {name} {season}")
            note = (f"Identified games give a record of {documented}." if known.total()
                    else "Individual game results unavailable.")
        elif known[None]:
            note = f'Outcome unknown for {known[None]} of {r["games"]} games.'
        conferences = [c["conference_name"] for c in team["conference_history"]
                       if int(c["start_season"][:4]) < year and
                       (not c["end_season"] or int(c["end_season"][:4]) >= year - 1)]
        yield year, name, " / ".join(conferences), w, l, t, note, program["program_key"]


def calculate(directory):
    registry = (directory / "programs.csv").read_bytes()
    digest = sha256(sha256(registry).digest())
    programs = csv.DictReader(registry.decode().splitlines())
    keys = sorted(r["program_key"] for r in programs if r["public_page_enabled"] == "Yes"
                  and r["history_start_season"] and int(r["history_start_season"][:4]) < 1949)
    rows = list(FIRST_RECORDS)
    for key in keys:
        raw = (directory / f"{key}.json").read_bytes()
        digest.update(sha256(raw).digest())
        team = json.loads(raw)
        if team["program"]["program_key"] != key:
            raise ValueError(f"Historical program identity mismatch: {key}")
        rows.extend(team_rows(team))
    if digest.hexdigest() != INPUT_HASH:
        raise ValueError(f"Historical input changed: {digest.hexdigest()}")
    if len({(r[0], r[1]) for r in rows}) != len(rows):
        raise ValueError("Duplicate team in a historical season")
    return sorted(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    rows = calculate(parser.parse_args().directory)
    with (ROOT / "program_records.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("season", "school", "conference", "wins", "losses", "ties", "coverage", "source"))
        writer.writerows(rows)
