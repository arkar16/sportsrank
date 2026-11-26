"""Freeze the football comparison sample, evaluate the reserved 2025 season and export CORS scores."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from cfb.excitement_evaluation import _correlation, _ranks

from cfb.cfbd_appeal import FootballEvidence, fingerprint
from cfb.excitement import matchup_quality
from cfb.game_excitement import Matchup, read_forecasts

TRAIN = (2024, 2026)
RESERVED = 2025


def features(row):
    home, away = row["final"]
    sign = 1 if home > away else -1
    return [abs(home - away), home + away, *[sign * (h - a) for h, a in row["ends"][:3]]]


def save(path, data):
    blob = (json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    path.write_bytes(blob)
    return sha256(blob).hexdigest()


def metrics(expected, actual):
    errors = np.asarray(expected) - actual
    return dict(
        games=len(actual),
        mae=float(abs(errors).mean()),
        bias=float(errors.mean()),
        spearman=float(_correlation(_ranks(expected), _ranks(actual))),
    )


def freeze(private, output, site):
    output.mkdir(parents=True, exist_ok=True)
    frozen_cohort = json.loads(
        (Path(__file__).resolve().parents[1] / "cfb/cfbd_excitement/reference-members-v1.json").read_bytes()
    )
    members = set(frozen_cohort["members"])
    train = []
    ties = []
    for year in TRAIN:
        data = json.loads((private / f"{year}.json").read_bytes())
        for gid, row in sorted(data.items()):
            if gid not in members or "drama" not in row or row["final"][0] == row["final"][1]:
                continue
            ot = len(row["ends"]) > 4
            train.append((gid, features(row), row["drama"], ot))
            if row.get("tied_drama") is not None:
                ties.append((gid, row["ends"][3][0], row["tied_drama"]))
    pools = {}
    for ot in (False, True):
        group = [r for r in train if r[3] == ot]
        if not group:
            raise ValueError("insufficient qualified football training games")
        matrix = np.asarray([r[1] for r in group], dtype=float)
        width = np.maximum(1, np.percentile(matrix, 75, axis=0) - np.percentile(matrix, 25, axis=0))
        pools[ot] = matrix, width, np.asarray([r[2] for r in group]), {r[0]: i for i, r in enumerate(group)}

    def quarter(gid, row):
        vector = features(row)
        matrix, width, targets, indices = pools[len(row["ends"]) > 4]
        distances = np.sum(((matrix - vector) / width) ** 2, axis=1)
        if gid in indices:
            distances[indices[gid]] = np.inf
        k = min(50, len(distances) - int(gid in indices)) - 1
        cutoff = np.partition(distances, k)[k]
        selected = targets[distances <= cutoff]
        return float(selected.mean()), len(selected)

    # This digest covers training membership, features and target values before reading the holdout.
    frozen = sha256(json.dumps(train, separators=(",", ":")).encode()).hexdigest()
    if frozen != frozen_cohort["sha256"]:
        raise ValueError("football training sample differs from the reserved-season reference")
    policy = dict(
        provider="CollegeFootballData",
        training=list(TRAIN),
        training_games=len(train),
        frozen_sha256=frozen,
        margin_reference=[(gid, vec[0], drama, ot) for gid, vec, drama, ot in train],
        regulation_ties=ties,
        reserved=RESERVED,
        seasons={},
    )
    ref = FootballEvidence(policy, output)
    forecasts = read_forecasts(site / f"cfb/years/{RESERVED}", RESERVED)
    measured, estimated, final, hidden = [], [], [], []
    reserved = json.loads((private / f"{RESERVED}.json").read_bytes())
    for gid, row in sorted(reserved.items()):
        f = forecasts.get(Matchup(*row["key"]))
        if "drama" not in row or not f:
            continue
        h, a = row["final"]
        ot = len(row["ends"]) > 4
        winner_margin = float(f.home_margin) * (1 if h > a else -1)
        bonus = 3 * matchup_quality(f.home_rank, f.away_rank) + 3 * min(1, max(0, -winner_margin / 14))
        measured.append(row["drama"] + 2 * ot + bonus)
        estimated.append(quarter(gid, row)[0] + 2 * ot + bonus)
        final.append(ref.final_pools[ot].estimate(abs(h - a), gid)[0] + bonus)
        hidden.append(ref.final_pools[None].estimate(abs(h - a), gid)[0] + bonus)
    q = metrics(estimated, measured)
    policy.update(
        quarter=q,
        final=metrics(final, measured),
        final_unknown_ot=metrics(hidden, measured),
        quarter_qualified=bool(q["mae"] <= 5 and abs(q["bias"]) <= 2 and q["spearman"] >= 0.90),
    )
    for source in sorted(private.glob("[0-9][0-9][0-9][0-9].json")):
        year = int(source.stem)
        rows = {}
        for gid, r in sorted(json.loads(source.read_bytes()).items()):
            sig = fingerprint(year, r["key"], r["final"])
            ot = len(r["ends"]) > 4
            if "drama" in r:
                drama, tier, support = r["drama"], "full", None
            elif policy["quarter_qualified"] and r["final"][0] != r["final"][1]:
                drama, support = quarter(gid, r)
                tier = "quarter"
            else:
                drama, tier, support = None, "final", None
            if sig in rows:
                raise ValueError(f"duplicate CollegeFootballData match {year}/{gid}")
            rows[sig] = [gid, ot, tier, drama, support]
        if rows:
            policy["seasons"][year] = save(output / source.name, rows)
    save(output / "policy.json", policy)
    print(
        "training",
        len(train),
        "holdout",
        q,
        "final",
        policy["final"],
        "unknown OT",
        policy["final_unknown_ot"],
        flush=True,
    )


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--private", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--site", type=Path, default=Path("website"))
    args = cli.parse_args()
    freeze(args.private, args.output, args.site)
