"""Validate limited-score basketball AEV against measured games in whole-season folds."""
from __future__ import annotations

import argparse
import csv
from hashlib import file_digest
from math import exp
from pathlib import Path
import sqlite3
from statistics import correlation

import numpy as np

from cbb.game_excitement import read_measurements

FIELDS = ("game", "season", "abs_margin", "total", "halftime_margin", "overtime", "drama")


def cases(year, db, site):
    evidence = read_measurements(year)
    results = db.execute(
        """SELECT id, json_extract(raw_json, '$.homeTeamId'), json_extract(raw_json, '$.awayTeamId'),
                  json_extract(raw_json, '$.homePoints'), json_extract(raw_json, '$.awayPoints')
           FROM games WHERE season = ? AND status = 'final'""",
        (year,),
    )
    finals = {str(game): (str(home), str(away), h, a) for game, home, away, h, a in results}
    games = []
    with (site / f"seasons/{year}/excitement/bev-v1.csv").open() as source:
        for record in csv.DictReader(source):
            observed = finals.get(record["game"])
            if observed is None:
                continue
            home, away, h, a = observed
            if (home, away) != (record["home_id"], record["away_id"]):
                raise ValueError("pregame teams disagree with the game identity")
            key = record["date"], frozenset((home, away))
            matches = [
                row for row in evidence.get(key, ())
                if (int(row["home_score"]), int(row["away_score"])) == ((h, a) if row["home_id"] == home else (a, h))
            ]
            if len(matches) != 1 or not matches[0]["tension"]:
                continue
            row = matches[0]
            halftime = int(row["half_home"]) - int(row["half_away"])
            if int(row["home_score"]) < int(row["away_score"]):
                halftime = -halftime
            drama = (80 * float(row["tension"]) + 8 * min(1, int(row["lead_changes"]) / 3)
                     + 4 * min(1, int(row["winner_deficit"]) / 9))
            quality = exp((2 - int(record["home_rank"]) - int(record["away_rank"])) / 100)
            winner_margin = float(record["home_margin"]) * (1 if h > a else -1)
            surprise = min(1, max(0, -winner_margin / 6))
            overtime = int(int(row["periods"]) > 2)
            games.append((row["game_id"], year, abs(h - a), h + a, halftime, overtime, drama, 3 * quality + 3 * surprise))
    if len({r[0] for r in games}) != len(games):
        raise ValueError("duplicate games in basketball evaluation")
    return sorted(games)


def evaluate(training, testing):
    if not training or not testing or {r[1] for r in training} & {r[1] for r in testing}:
        raise ValueError("whole nonempty training and test seasons must be disjoint")
    if {r[0] for r in training} & {r[0] for r in testing}:
        raise ValueError("a test game appears in the reference")
    ref = np.array([r[2:7] for r in training], dtype=float)
    for dimensions, name in ((1, "margin baseline"), (2, "final"), (3, "halftime")):
        estimated, actual = [], []
        for ot in (0, 1):
            pool = ref[ref[:, 3] == ot]
            if not len(pool):
                raise ValueError(f"no reference games with overtime={ot}")
            features = pool[:, :dimensions]
            lower, upper = np.percentile(features, [25, 75], axis=0)
            width = np.maximum(1, upper - lower)
            for _, _, margin, total, half, overtime, drama, bonus in testing:
                if overtime != ot:
                    continue
                target = np.array((margin, total, half))[:dimensions]
                distances = np.sum(((features - target) / width) ** 2, axis=1)
                kth = min(49, len(distances) - 1)
                selected = distances <= np.partition(distances, kth)[kth]
                estimated.append(pool[selected, 4].mean() + 2 * ot + bonus)
                actual.append(drama + 2 * ot + bonus)
        errors = np.subtract(estimated, actual)
        top_n = (len(actual) + 9) // 10
        actual_top = np.array(actual) >= np.sort(actual)[-top_n]
        predicted_top = np.array(estimated) >= np.sort(estimated)[-top_n]
        print(name, len(actual), "MAE", float(np.mean(abs(errors))), "bias", float(errors.mean()),
              "Spearman", correlation(estimated, actual, method="ranked"),
              "top-decile overlap", np.count_nonzero(actual_top & predicted_top), "of", top_n)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--database", type=Path, required=True)
    cli.add_argument("--site", type=Path, default=Path("website/cbb"))
    cli.add_argument("--training", type=int, nargs="+", required=True)
    cli.add_argument("--reference", required=True, type=Path)
    cli.add_argument("--holdout", type=int)
    cli.add_argument("--checksum", help="accepted SHA-256 of the frozen training CSV")
    args = cli.parse_args()
    years = sorted(set(args.training))
    if args.holdout in years or bool(args.holdout) != bool(args.checksum) or len(years) < 2:
        cli.error("use at least two training seasons and pair a separate holdout with its reference checksum")
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        groups = {year: cases(year, db, args.site) for year in years}
        training = [r for year in years for r in groups[year]]
        if args.holdout is None:
            with args.reference.open("w", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(FIELDS)
                writer.writerows(r[:-1] for r in training)
            for year in years:
                print("Development season", year)
                evaluate([r for r in training if r[1] != year], groups[year])
        else:
            with args.reference.open("rb") as stream:
                if file_digest(stream, "sha256").hexdigest() != args.checksum:
                    raise ValueError("wrong basketball reference checksum")
            with args.reference.open() as stream:
                records = list(csv.reader(stream))
            if records[0] != list(FIELDS) or records[1:] != [[str(v) for v in r[:-1]] for r in training]:
                raise ValueError("the frozen basketball reference differs from its training seasons")
            frozen = [(r[0], *map(int, r[1:6]), float(r[6])) for r in records[1:]]
            print("Reserved season", args.holdout)
            evaluate(frozen, cases(args.holdout, db, args.site))


if __name__ == "__main__":
    main()
