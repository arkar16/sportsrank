"""Source adapters at the external CFBD seam."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

import cfbd

if __package__:
    from .cfbd_client import classification_value, create_api_client, division_classification
    from .request_meter import RequestMeter
else:  # Support the existing direct execution style from cfb/.
    from cfbd_client import classification_value, create_api_client, division_classification
    from request_meter import RequestMeter


@dataclass(frozen=True)
class SourceTeam:
    school: str
    conference: str


@dataclass(frozen=True)
class SourceGame:
    week: int
    home_team: str
    home_classification: str
    home_points: int | None
    away_team: str
    away_classification: str
    away_points: int | None
    neutral_site: bool


@runtime_checkable
class SeasonSource(Protocol):
    def fetch_teams(
        self, year: int, classification: str, *, cache_decision: str
    ) -> Sequence[SourceTeam]: ...

    def fetch_games(
        self, year: int, classification: str, *, cache_decision: str
    ) -> Sequence[SourceGame]: ...


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def normalize_team(item: object) -> SourceTeam:
    return SourceTeam(
        school=str(_value(item, "school")),
        conference=str(_value(item, "conference") or "FBS Independents"),
    )


def normalize_game(item: object) -> SourceGame:
    return SourceGame(
        week=int(_value(item, "week")),
        home_team=str(_value(item, "home_team")),
        home_classification=str(
            classification_value(_value(item, "home_classification"))
        ),
        home_points=_value(item, "home_points"),
        away_team=str(_value(item, "away_team")),
        away_classification=str(
            classification_value(_value(item, "away_classification"))
        ),
        away_points=_value(item, "away_points"),
        neutral_site=bool(_value(item, "neutral_site", False)),
    )


class ProductionSeasonSource:
    """Metered CFBD adapter. Credentials are read only inside transport calls."""

    def __init__(self, meter: RequestMeter, category: str = "scheduled") -> None:
        self.meter = meter
        self.category = category

    def fetch_teams(
        self, year: int, classification: str, *, cache_decision: str
    ) -> tuple[SourceTeam, ...]:
        if classification.upper() != "FBS":
            raise NotImplementedError("The production teams adapter currently supports FBS")

        def transport():
            with create_api_client() as api_client:
                return cfbd.TeamsApi(api_client).get_fbs_teams(year=year)

        response = self.meter.execute(
            purpose=self.category,
            endpoint="teams",
            season=year,
            cache_decision=cache_decision,
            transport=transport,
        )
        return tuple(normalize_team(item) for item in response)

    def fetch_games(
        self, year: int, classification: str, *, cache_decision: str
    ) -> tuple[SourceGame, ...]:
        def transport():
            with create_api_client() as api_client:
                return cfbd.GamesApi(api_client).get_games(
                    year=year,
                    classification=division_classification(classification),
                )

        response = self.meter.execute(
            purpose=self.category,
            endpoint="games",
            season=year,
            cache_decision=cache_decision,
            transport=transport,
        )
        return tuple(normalize_game(item) for item in response)


class FixtureSeasonSource:
    """Recorded-file adapter with the same consumer interface as production."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.call_count = 0
        self.calls: list[tuple[str, int, str, str]] = []

    def _read(
        self, kind: str, year: int, classification: str, cache_decision: str
    ) -> list[dict]:
        self.call_count += 1
        self.calls.append(
            (kind, int(year), classification.upper(), cache_decision)
        )
        path = self.root / str(year) / classification.lower() / f"{kind}.json"
        with path.open(encoding="utf-8") as fixture:
            value = json.load(fixture)
        if not isinstance(value, list):
            raise ValueError(f"Fixture {path} must contain a JSON list")
        return value

    def fetch_teams(
        self, year: int, classification: str, *, cache_decision: str
    ) -> tuple[SourceTeam, ...]:
        return tuple(
            normalize_team(item)
            for item in self._read("teams", year, classification, cache_decision)
        )

    def fetch_games(
        self, year: int, classification: str, *, cache_decision: str
    ) -> tuple[SourceGame, ...]:
        return tuple(
            normalize_game(item)
            for item in self._read("games", year, classification, cache_decision)
        )
