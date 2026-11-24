"""Rebuild final 1981–2019 CORS standings from pinned historical compact results."""
import argparse
import csv
from pathlib import Path

from cbb.historical_replay import EASTERN, FIRST, HASHES, LAST, ROOT, iter_raw_historical_seasons


def calculate(directory):
    for season in iter_raw_historical_seasons(Path(directory)):
        year = season["season"]
        for r in season["final_rankings"]:
            yield year, r["rank"], r["team_id"], r["school"], r["conference"], r["wins"], r["losses"], f"{r['cors']:.2f}"


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
