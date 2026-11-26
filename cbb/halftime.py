"""The frozen men's basketball halftime reference accepted under ADR-0021."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from io import StringIO
import json
from math import sqrt
from pathlib import Path

import numpy as np

from cfb.sparse_excitement import MarginPool


DATA = Path(__file__).with_name("cbbd_excitement")
FEATURES = ("abs_margin", "total", "halftime_margin")


@lru_cache(maxsize=1)
def policy():
    record = json.loads((DATA / "policy.json").read_bytes())
    if (record["provider"] != "CollegeBasketballData" or record["training"] != [2024, 2026]
            or record["reserved"] != 2025 or record["file"] != "halftime-reference-v1.csv"):
        raise ValueError("unknown basketball excitement reference")
    frozen = {k: record[k] for k in ("provider","training","file","sha256","widths")}
    if sha256(json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != record["frozen_sha256"]:
        raise ValueError("corrupt frozen basketball reference")
    m = record["halftime"]
    if record["halftime_qualified"] != (m["mae"] <= 7 and abs(m["bias"]) <= 2 and m["spearman"] >= .85):
        raise ValueError("basketball halftime validation disagrees with release criteria")
    return record


@dataclass(frozen=True)
class HalftimePool:
    index: dict[str, int]
    features: np.ndarray
    targets: np.ndarray
    widths: tuple[float, float, float]

    def estimate(self, game_id, final_margin, total_points, winner_halftime_margin):
        target = (final_margin, total_points, winner_halftime_margin)
        squared = np.sum(((self.features - target) / self.widths) ** 2, axis=1)
        excluded = self.index.get(game_id)
        eligible = len(squared)
        if excluded is not None:
            squared[excluded] = np.inf
            eligible -= 1
        if not eligible:
            raise ValueError("no comparable basketball games after excluding the target")
        kth = min(50, eligible) - 1
        cutoff = np.partition(squared, kth)[kth]
        selected = self.targets[squared <= cutoff]
        return float(selected.mean()), len(selected), sqrt(cutoff)


@lru_cache(maxsize=1)
def halftime_pools():
    reference = policy()
    data = (DATA / reference["file"]).read_bytes()
    if sha256(data).hexdigest() != reference["sha256"]:
        raise ValueError("basketball halftime reference differs from the accepted checksum")
    rows = list(csv.DictReader(StringIO(data.decode("ascii"))))
    pools = {}
    for overtime in (False, True):
        widths = tuple(reference["widths"][str(int(overtime))])
        group = [r for r in rows if int(r["overtime"]) == overtime]
        pools[overtime] = HalftimePool(
            {r["game"]: i for i, r in enumerate(group)},
            np.array([[int(r[k]) for k in FEATURES] for r in group], dtype=float),
            np.array([float(r["drama"]) for r in group]),
            widths,
        )
    return pools

@lru_cache(maxsize=1)
def final_pools():
    pools = halftime_pools()
    rows = [(gid, int(pool.features[index, 0]), pool.targets[index] + 2 * ot, ot)
            for ot, pool in pools.items() for gid, index in pool.index.items()]
    return {ot: MarginPool((gid, margin, value) for gid, margin, value, known in rows
                           if ot is None or known == ot) for ot in (None, False, True)}
