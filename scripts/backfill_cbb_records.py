"""Rebuild early_records.csv from Trono's ordered, NCAA and NIT HTML files.

Download the three linked score files per season from
https://academics.smcvt.edu/jtrono/BBallArchive.htm into one directory.
The ordered file already includes additional invitational tournaments.
Run uv run python -m scripts.backfill_cbb_records DIRECTORY from the repo root.
"""
import argparse
import csv
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1] / "cbb"
FIRST, LAST = 1950, 1984
INPUT_HASH = "f4fc8024a39160a11c7967dd56b310cf2ced5a2b7f58a961c5001c32b28de88c"


def read_scores(raw, schools):
    document = BeautifulSoup(raw, "lxml")
    # FrontPage wraps text inside rows. Only HTML breaks delimit games.
    for tag in document.body.find_all(["br", "p"]):
        if tag.name == "br":
            tag.replace_with("\x1e")
        else:
            tag.append("\x1e")
    for line in document.body.get_text().split("\x1e"):
        fields = line.upper().split()
        if not fields:
            continue
        first, points, second, other_points, *_ = fields
        if first == second or first not in schools or second not in schools:
            raise ValueError(f"Unknown or repeated team in {line.strip()}")
        yield first, int(points), second, int(other_points)


def calculate(directory):
    with (ROOT / "early_school_names.csv").open(encoding="utf-8") as f:
        schools = {r["code"]: r["school"] for r in csv.DictReader(f)}
    files = {p.name.lower(): p for p in directory.glob("*.htm")}
    digest = sha256()
    rows = []
    for year in range(FIRST, LAST + 1):
        records = defaultdict(Counter)
        for kind in ("dat", "ncaa", "nit"):
            name = f"{(year - 1) % 100:02}_{year % 100:02}_{kind}.htm"
            raw = files[name].read_bytes()
            digest.update(sha256(raw).digest())
            for first, points, second, other_points in read_scores(raw, schools):
                if points == other_points:
                    raise ValueError(f"Tied basketball result in {name}")
                winner, loser = (first, second) if points > other_points else (second, first)
                records[winner][kind, "w"] += 1
                records[loser][kind, "l"] += 1
        for code, record in sorted(records.items(), key=lambda x: schools[x[0]]):
            rows.append((year, schools[code], *(record[kind, stat] for kind in ("dat", "ncaa", "nit")
                                                for stat in ("w", "l"))))
    if digest.hexdigest() != INPUT_HASH:
        raise ValueError(f"Historical score files changed: {digest.hexdigest()}")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    records = calculate(parser.parse_args().directory)
    with (ROOT / "early_records.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("season", "school", "wins", "losses", "ncaa_w", "ncaa_l", "nit_w", "nit_l"))
        writer.writerows(records)
