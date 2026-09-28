"""Auditable, narrowly scoped exceptions for Classification Entrants.

The registry is data, not a fallback policy: reconciliation only applies an
entry when its exact team is present in that season's current classification
snapshot and absent from the preceding FINAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ClassificationEntrant:
    season: int
    classification: str
    team: str
    baseline: float
    prior_wins_vs_expected: float
    source_urls: tuple[str, ...]

    def evidence(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "classification": self.classification,
            "team": self.team,
            "baseline": self.baseline,
            "prior_wins_vs_expected": self.prior_wins_vs_expected,
            "source_urls": list(self.source_urls),
        }


@dataclass(frozen=True)
class IdentityRepair:
    season: int
    classification: str
    legacy_team: str
    canonical_team: str
    reason: str
    source_urls: tuple[str, ...]

    def evidence(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "classification": self.classification,
            "legacy_team": self.legacy_team,
            "canonical_team": self.canonical_team,
            "reason": self.reason,
            "source_urls": list(self.source_urls),
        }


@dataclass(frozen=True)
class CarryoverReconciliation:
    cors: dict[str, float]
    wins_vs_expected: dict[str, float]
    entrants: tuple[ClassificationEntrant, ...]
    identity_repairs: tuple[IdentityRepair, ...]

    def evidence(self) -> dict[str, Any]:
        return {
            "entrants": [entry.evidence() for entry in self.entrants],
            "identity_repairs": [repair.evidence() for repair in self.identity_repairs],
        }


CLASSIFICATION_ENTRANTS: tuple[ClassificationEntrant, ...] = (
    ClassificationEntrant(2024, "FBS", "Kennesaw State", -10.0, 0.0, (
        "https://ksuowls.com/news/2022/10/14/general-kennesaw-state-to-join-conference-usa-in-2024-25.aspx",
    )),
    ClassificationEntrant(2025, "FBS", "Delaware", -10.0, 0.0, (
        "https://bluehens.com/news/2023/11/28/delaware-athletics-delaware-accepts-invitation-to-join-conference-usa-as-full-member",
    )),
    ClassificationEntrant(2025, "FBS", "Missouri State", -10.0, 0.0, (
        "https://news.missouristate.edu/2024/05/10/missouri-state-accepts-invitation-to-join-conference-usa/",
    )),
    ClassificationEntrant(2026, "FBS", "North Dakota State", -10.0, 0.0, (
        "https://www.ndsu.edu/news/2026-02-09-ndsu-joining-mountain-west-conference",
    )),
    ClassificationEntrant(2026, "FBS", "Sacramento State", -10.0, 0.0, (
        "https://hornetsports.com/news/2026/2/16/hornet-football-to-join-the-mac-in-2026.aspx",
    )),
)

_IDENTITY_REPAIRS: tuple[IdentityRepair, ...] = (
    IdentityRepair(
        2024,
        "FBS",
        "San Jos\N{REPLACEMENT CHARACTER} State",
        "San Jos\N{LATIN SMALL LETTER E WITH ACUTE} State",
        "official SJSU Athletics branding supplies the canonical San José State spelling; the legacy Published Site carries the replacement-character alias",
        ("https://sjsuspartans.com/trademark-and-licensing",),
    ),
)


def reconcile_previous_final(
    season: int,
    classification: str,
    cors: Mapping[str, float],
    wins_vs_expected: Mapping[str, float],
    expected_teams: set[str] | frozenset[str],
) -> CarryoverReconciliation:
    """Return exact adjustments and deterministic evidence for this transition."""

    normalized_classification = classification.upper()
    adjusted_cors = {str(team): float(value) for team, value in cors.items()}
    adjusted_wve = {str(team): float(value) for team, value in wins_vs_expected.items()}
    applied_repairs: list[IdentityRepair] = []
    for repair in _IDENTITY_REPAIRS:
        if (repair.season, repair.classification) != (int(season), normalized_classification):
            continue
        if (
            repair.legacy_team in adjusted_cors
            and repair.canonical_team in expected_teams
            and repair.legacy_team not in expected_teams
            and repair.canonical_team not in adjusted_cors
        ):
            adjusted_cors[repair.canonical_team] = adjusted_cors.pop(repair.legacy_team)
            if repair.legacy_team in adjusted_wve:
                adjusted_wve[repair.canonical_team] = adjusted_wve.pop(repair.legacy_team)
            applied_repairs.append(repair)

    applied_entrants: list[ClassificationEntrant] = []
    for entrant in CLASSIFICATION_ENTRANTS:
        if (entrant.season, entrant.classification) != (int(season), normalized_classification):
            continue
        if entrant.team not in expected_teams or entrant.team in adjusted_cors:
            continue
        adjusted_cors[entrant.team] = entrant.baseline
        if adjusted_wve:
            adjusted_wve[entrant.team] = entrant.prior_wins_vs_expected
        applied_entrants.append(entrant)

    return CarryoverReconciliation(
        cors=dict(sorted(adjusted_cors.items())),
        wins_vs_expected=dict(sorted(adjusted_wve.items())),
        entrants=tuple(applied_entrants),
        identity_repairs=tuple(applied_repairs),
    )


__all__ = [
    "CLASSIFICATION_ENTRANTS",
    "CarryoverReconciliation",
    "ClassificationEntrant",
    "IdentityRepair",
    "reconcile_previous_final",
]
