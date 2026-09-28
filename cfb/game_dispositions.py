"""Auditable corrections for games whose cached provider metadata was lost.

Entries require an exact season/classification/week/home/away identity.  This
registry is intentionally tiny: generic null scores are never inferred to be
cancellations.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

if __package__:
    from .season_source import SourceGame
else:  # pragma: no cover - direct execution compatibility
    from season_source import SourceGame


APP_STATE_CANCELLATION_SOURCES = (
    "https://appstatesports.com/news/2024/9/27/app-state-liberty-football-game-canceled.aspx",
    "https://sunbeltsports.org/news/2024/9/27/app-state-liberty-football-game-cancelled-additional-events-impacted-by-hurricane-helene.aspx",
)

_CANCELLATIONS = {
    (2024, "FBS", 5, "App State", "Liberty"): {
        "notes": "Canceled due to Hurricane Helene; the game was not rescheduled.",
        "source": " ".join(APP_STATE_CANCELLATION_SOURCES),
    },
}


def apply_cancellation_registry(
    year: int,
    classification: str,
    games: Iterable[SourceGame],
) -> tuple[SourceGame, ...]:
    """Return games enriched only by exact, cited cancellation matches."""

    result: list[SourceGame] = []
    for game in games:
        evidence = _CANCELLATIONS.get(
            (int(year), classification.upper(), int(game.week), game.home_team, game.away_team)
        )
        if evidence is None or game.disposition == "canceled":
            result.append(game)
            continue
        result.append(
            replace(
                game,
                completed=False,
                disposition="canceled",
                disposition_source=str(evidence["source"]),
                notes=game.notes or str(evidence["notes"]),
            )
        )
    return tuple(result)


__all__ = ["APP_STATE_CANCELLATION_SOURCES", "apply_cancellation_registry"]
