"""The frozen men's basketball halftime reference accepted under ADR-0021."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from io import StringIO
from math import sqrt
from pathlib import Path

import numpy as np


REFERENCE = Path(__file__).with_name("excitement_data") / "halftime-reference-v1.csv"
REFERENCE_SHA256 = "3e6cd747ca8259debd07edfbd33a99d12fa18dd9c39f53b602ab287a65f613e5"
FEATURES = ("abs_margin", "total", "halftime_margin")
# Training-pool interquartile ranges in feature order, frozen with the reference.
WIDTHS = {False: (13, 25, 11), True: (3.75, 27, 9.75)}


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
    data = REFERENCE.read_bytes()
    if sha256(data).hexdigest() != REFERENCE_SHA256:
        raise ValueError("basketball halftime reference differs from the accepted checksum")
    rows = list(csv.DictReader(StringIO(data.decode("ascii"))))
    pools = {}
    for overtime, widths in WIDTHS.items():
        group = [r for r in rows if int(r["overtime"]) == overtime]
        pools[overtime] = HalftimePool(
            {r["game"]: i for i, r in enumerate(group)},
            np.array([[int(r[k]) for k in FEATURES] for r in group], dtype=float),
            np.array([float(r["drama"]) for r in group]),
            widths,
        )
    return pools
