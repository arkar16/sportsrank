"""CollegeFootballData-only, checksummed evidence consumed by the public watch guides."""

from dataclasses import dataclass
from functools import cached_property, lru_cache
from hashlib import sha256
import json
from pathlib import Path

from .sparse_excitement import MarginPool

DATA = Path(__file__).with_name("cfbd_excitement")


def fingerprint(year, key, final):
    return sha256(
        json.dumps([year, *key[:4], *final], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass
class FootballEvidence:
    policy: dict
    root: Path

    @cached_property
    def final_pools(self):
        training = self.policy["margin_reference"]
        return {
            ot: MarginPool(
                (gid, margin, drama + 2 * known)
                for gid, margin, drama, known in training
                if ot is None or known == ot
            )
            for ot in (None, False, True)
        }

    @cached_property
    def tie_pool(self):
        return MarginPool(self.policy["regulation_ties"])

    def season(self, year):
        digest = self.policy["seasons"].get(str(year))
        if digest is None:
            return {}
        blob = (self.root / f"{year}.json").read_bytes()
        if sha256(blob).hexdigest() != digest:
            raise ValueError(f"CollegeFootballData evidence changed for {year}")
        return json.loads(blob)

    @property
    def tiers(self):
        return ("final", "quarter") if self.policy["quarter_qualified"] else ("final",)


@lru_cache(maxsize=1)
def load():
    data = json.loads((DATA / "policy.json").read_bytes())
    if (
        data.get("provider") != "CollegeFootballData"
        or data.get("training") != [2024, 2026]
        or data.get("reserved") != 2025
    ):
        raise ValueError("incompatible football excitement reference")
    cohort = json.loads((DATA / "reference-members-v1.json").read_bytes())
    if (data["frozen_sha256"] != cohort["sha256"] or
            {r[0] for r in data["margin_reference"]} != set(cohort["members"])):
        raise ValueError("football reference differs from the confirmed cohort")
    metrics = data["quarter"]
    if data["quarter_qualified"] != (
        metrics["mae"] <= 5 and abs(metrics["bias"]) <= 2 and metrics["spearman"] >= 0.90
    ):
        raise ValueError("football quarter validation disagrees with release criteria")
    return FootballEvidence(data, DATA)
