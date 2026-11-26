"""Pregame expectations and final-margin comparisons for incomplete game records."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from math import exp
from statistics import fmean


@dataclass(frozen=True)
class AppealPrior:
    bev: float
    quality: float


def pregame_prior(home, away, *, field_size, close, venue=0.0, missing_rating=0.0):
    """Integrate a missing team's rank over the field, without assigning it a rank.

    Each supplied team is (rank, point rating). An uncertain rating spans one
    close-game margin on either side of the sport's pregame population prior.
    This estimates appeal without manufacturing an issued spread or upset bonus.
    """
    size = max(1, field_size)
    mean_rank_factor = sum(exp(-rank / 100) for rank in range(size)) / size
    quality = ((exp(-(home[0] - 1) / 100) if home else mean_rank_factor)
               * (exp(-(away[0] - 1) / 100) if away else mean_rank_factor))
    prior = (missing_rating - close, missing_rating, missing_rating + close)
    homes = (float(home[1]),) if home else prior
    aways = (float(away[1]),) if away else prior
    closeness = fmean(exp(-((h - a + venue) / close) ** 2) for h in homes for a in aways)
    return AppealPrior(60 * quality + 40 * closeness, quality)


class MarginPool:
    """Mean of the nearest 50 measured games, retaining everyone tied at the cutoff."""

    def __init__(self, rows):
        self.groups = defaultdict(list)
        self.members = {}
        for identity, margin, value in rows:
            if identity in self.members or margin < 0 or not 0 <= value <= 100:
                raise ValueError("invalid margin reference")
            self.members[identity] = margin, value
            self.groups[margin].append(value)
        if not self.members:
            raise ValueError("empty margin reference")
        self.totals = {m: (len(values), sum(values)) for m, values in self.groups.items()}

    def estimate(self, margin, identity=None):
        return self._estimate(margin, identity if identity in self.members else None)

    @lru_cache(maxsize=4096)
    def _estimate(self, margin, identity):
        target = self.members.get(identity)
        count, total = 0, 0.0
        by_distance = defaultdict(list)
        for other in self.totals:
            by_distance[abs(other - margin)].append(other)
        for distance in sorted(by_distance):
            for other in by_distance[distance]:
                n, value = self.totals[other]
                if target and target[0] == other:
                    n -= 1
                    value -= target[1]
                count += n
                total += value
            if count >= 50:
                break
        if not count:
            raise ValueError("no comparable games after target exclusion")
        return total / count, count
