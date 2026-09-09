"""Source adapters at the external CFBD seam."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import math
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Protocol, Sequence, runtime_checkable

import cfbd

if __package__:
    from .cfbd_client import classification_value, create_api_client, division_classification
    from .request_meter import RequestMeter
    from .week_calendar import canonical_week, require_supported_season
else:  # Support the existing direct execution style from cfb/.
    from cfbd_client import classification_value, create_api_client, division_classification
    from request_meter import RequestMeter
    from week_calendar import canonical_week, require_supported_season


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
    provider_id: str | None = None
    date: str | None = None
    provider_week: int | None = None
    completed: bool | None = None
    notes: str | None = None
    disposition: str = "scheduled"
    disposition_source: str | None = None


@runtime_checkable
class SeasonSource(Protocol):
    def fetch_teams(
        self, year: int, classification: str, *, cache_decision: str
    ) -> Sequence[SourceTeam]: ...

    def fetch_games(
        self, year: int, classification: str, *, cache_decision: str
    ) -> Sequence[SourceGame]: ...


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _required_text(item: object, name: str, *aliases: str) -> str:
    value = _optional_value(item, name, *aliases)
    if value is None:
        raise ValueError(f"CFBD payload is missing {name}")
    text = str(value).strip()
    if not text or text.lower() == "none":
        raise ValueError(f"CFBD payload has an invalid {name}")
    return text


def _optional_value(item: object, *names: str) -> Any:
    for name in names:
        value = _value(item, name)
        if value is not None:
            return value
    return None


def _score(item: object, name: str, *aliases: str) -> int | None:
    value = _optional_value(item, name, *aliases)
    if value is None:
        return None
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"CFBD payload has an invalid {name}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CFBD payload has an invalid {name}") from exc
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"CFBD payload has an invalid {name}")
    return int(numeric)


def _week(item: object, *aliases: str) -> int:
    value = _optional_value(item, "week", *aliases)
    if isinstance(value, (bool, str, bytes)):
        raise ValueError("CFBD payload has an invalid week")
    try:
        result = int(value)
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("CFBD payload has an invalid week") from exc
    if not math.isfinite(numeric) or not numeric.is_integer() or result < 0:
        raise ValueError("CFBD payload has an invalid week")
    return result


def normalize_team(item: object) -> SourceTeam:
    return SourceTeam(
        school=_required_text(item, "school"),
        conference=str(_value(item, "conference") or "FBS Independents").strip(),
    )


def _date_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _boolean_value(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return bool(value)


def _legacy_fixture_payload(item: object) -> bool:
    """Recognize the established snake-case fixtures without provider metadata.

    These recorded fixtures predate provider ``id``/``startDate`` fields and
    already carry canonical week labels.  Real provider objects and payloads
    that expose either metadata field remain strict and fail closed below.
    """

    if not isinstance(item, Mapping):
        return False
    keys = set(item)
    has_metadata = bool(
        keys
        & {
            "id",
            "provider_id",
            "date",
            "start_date",
            "startDate",
            "provider_week",
            "providerWeek",
        }
    )
    return not has_metadata and {"home_team", "away_team"}.issubset(keys)


def normalize_game(
    item: object, *, season: int | None = None, from_provider: bool = False
) -> SourceGame:
    """Normalize a game while preserving provider metadata when available.

    Fixture and cache callers leave ``from_provider`` false, so their already
    canonical Week 0 values remain unchanged.  The production adapter opts in
    to the supported-season calendar policy after retaining the raw provider
    week in ``provider_week``.
    """

    raw_week = _week(item)
    date_value = _optional_value(item, "date", "start_date", "startDate")
    date_text = _date_text(date_value)
    if from_provider:
        if season is None:
            raise ValueError("production game normalization requires a season")
        require_supported_season(season)
        if date_value is None and _legacy_fixture_payload(item):
            # Compatibility for the repository's pre-metadata recorded
            # fixtures.  They are not evidence for a refreshed production
            # snapshot and therefore retain their already canonical week.
            game_week = raw_week
            provider_week = None
        else:
            game_week = canonical_week(season, raw_week, date_value)
            provider_week = raw_week
    else:
        game_week = raw_week
        raw_provider_week = _optional_value(item, "provider_week", "providerWeek")
        provider_week = _week({"week": raw_provider_week}) if raw_provider_week is not None else None

    home_points = _score(item, "home_points", "homePoints")
    away_points = _score(item, "away_points", "awayPoints")
    completed_value = _value(item, "completed")
    completed = completed_value if isinstance(completed_value, bool) else None
    notes_value = _optional_value(item, "notes", "provider_notes")
    notes = str(notes_value) if notes_value not in (None, "") else None
    raw_disposition = _optional_value(item, "disposition", "status")
    disposition_text = (
        str(raw_disposition or "")
        .strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
    )
    if disposition_text in {"cancelled", "canceled", "cancel", "cancelled game", "canceled game"}:
        disposition = "canceled"
    elif disposition_text in {"no contest", "not played", "abandoned"}:
        disposition = "not_played"
    elif completed is False:
        # Provider completion metadata is authoritative when present.  Some
        # feeds include provisional scores for an in-progress game; retaining
        # ``False`` keeps that game out of the completed boundary until the
        # provider marks it complete.
        disposition = "scheduled"
    elif home_points is not None and away_points is not None:
        disposition = "completed"
        # Older fixtures and provider responses omitted ``completed``.  Keep
        # their score-based compatibility while never overwriting an explicit
        # False value (handled above).
        if completed is None:
            completed = True
    else:
        disposition = "scheduled"
    return SourceGame(
        week=game_week,
        home_team=_required_text(item, "home_team", "homeTeam"),
        home_classification=_required_text(
            {"value": classification_value(_optional_value(item, "home_classification", "homeClassification"))},
            "value",
        ),
        home_points=home_points,
        away_team=_required_text(item, "away_team", "awayTeam"),
        away_classification=_required_text(
            {"value": classification_value(_optional_value(item, "away_classification", "awayClassification"))},
            "value",
        ),
        away_points=away_points,
        neutral_site=_boolean_value(_optional_value(item, "neutral_site", "neutralSite")),
        provider_id=(
            str(_optional_value(item, "provider_id", "id"))
            if _optional_value(item, "provider_id", "id") is not None
            else None
        ),
        date=date_text,
        provider_week=provider_week,
        completed=completed,
        notes=notes,
        disposition=disposition,
        disposition_source=(
            str(_optional_value(item, "disposition_source", "source"))
            if _optional_value(item, "disposition_source", "source") is not None
            else None
        ),
    )


def is_explicit_non_played(game: SourceGame) -> bool:
    """Return true only for an explicit canonical non-played disposition."""

    return game.disposition in {"canceled", "not_played"}


def is_completed(game: SourceGame | Mapping[str, Any]) -> bool:
    """Return whether a game has authoritative completion and finite scores.

    A boolean ``completed`` field is authoritative when present.  The score
    fallback exists for legacy fixtures that omitted that field.  Explicit
    canceled or not-played dispositions always remain unrankable, even when a
    provider includes provisional or conflicting score fields.
    """

    if isinstance(game, Mapping):
        home = game.get("home_points", game.get("home_score"))
        away = game.get("away_points", game.get("away_score"))
        completed = game.get("completed")
        disposition = game.get("disposition", game.get("status"))
    else:
        home = game.home_points
        away = game.away_points
        completed = game.completed
        disposition = game.disposition
    if isinstance(disposition, str):
        normalized = disposition.strip().lower().replace("_", " ").replace("-", " ")
        if normalized in {
            "canceled",
            "cancelled",
            "cancel",
            "canceled game",
            "cancelled game",
            "not played",
            "no contest",
            "abandoned",
        }:
            return False
    if isinstance(completed, bool) and not completed:
        return False
    if home is None or away is None:
        return False
    try:
        return math.isfinite(float(home)) and math.isfinite(float(away))
    except (TypeError, ValueError):
        return False


def _normalize_response(
    response: object, normalizer: Any, endpoint: str
) -> tuple[Any, ...]:
    """Normalize one provider response while still inside the meter boundary."""

    if response is None or isinstance(response, (str, bytes, Mapping)):
        raise ValueError(f"CFBD {endpoint} response must be a sequence")
    try:
        return tuple(normalizer(item) for item in response)
    except Exception as exc:
        raise ValueError(f"CFBD {endpoint} response schema is invalid") from exc


class ProductionSeasonSource:
    """Metered CFBD adapter. Credentials are read only inside transport calls."""

    requires_provider_metadata = True

    def __init__(self, meter: RequestMeter, category: str = "scheduled") -> None:
        self.meter = meter
        self.category = category
        self.legacy_metadata_compatibility = False

    def fetch_teams(
        self, year: int, classification: str, *, cache_decision: str
    ) -> tuple[SourceTeam, ...]:
        if classification.upper() != "FBS":
            raise NotImplementedError("The production teams adapter currently supports FBS")
        require_supported_season(year)

        def transport():
            with create_api_client() as api_client:
                response = cfbd.TeamsApi(api_client).get_fbs_teams(year=year)
                return _normalize_response(response, normalize_team, "teams")

        response = self.meter.execute(
            purpose=self.category,
            endpoint="teams",
            season=year,
            cache_decision=cache_decision,
            transport=transport,
        )
        return response

    def fetch_games(
        self, year: int, classification: str, *, cache_decision: str
    ) -> tuple[SourceGame, ...]:
        # The adapter is deliberately explicit about supported calendar policy
        # before entering the metered transport boundary.  A future season may
        # be fetched only after its source-backed boundary is added here.
        require_supported_season(year)

        def transport():
            with create_api_client() as api_client:
                response = cfbd.GamesApi(api_client).get_games(
                    year=year,
                    classification=division_classification(classification),
                )
                normalized = _normalize_response(
                    response,
                    lambda item: normalize_game(
                        item, season=year, from_provider=True
                    ),
                    "games",
                )
                self.legacy_metadata_compatibility = bool(normalized) and all(
                    game.provider_week is None
                    and game.provider_id is None
                    and game.date is None
                    for game in normalized
                )
                return normalized

        response = self.meter.execute(
            purpose=self.category,
            endpoint="games",
            season=year,
            cache_decision=cache_decision,
            transport=transport,
        )
        return response


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
