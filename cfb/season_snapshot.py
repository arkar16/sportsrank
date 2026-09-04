"""Deep Season Snapshot module shared by every CFB data consumer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import Callable, Mapping

if __package__:
    from .season_source import SeasonSource, SourceGame, SourceTeam
    from .snapshot_cache import SnapshotCache
else:
    from season_source import SeasonSource, SourceGame, SourceTeam
    from snapshot_cache import SnapshotCache


class SnapshotUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SeasonSnapshot:
    sport: str
    classification: str
    year: int
    teams: tuple[SourceTeam, ...]
    games: tuple[SourceGame, ...]
    metadata: Mapping[str, object]
    checksum: str

    @property
    def complete_through_week(self) -> int:
        return int(self.metadata.get("complete_through_week", -1))


def _legacy_checksum(year: int, classification: str, teams, games) -> str:
    content = {
        "sport": "cfb",
        "classification": classification.upper(),
        "year": int(year),
        "teams": [asdict(team) for team in teams],
        "games": [asdict(game) for game in games],
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _checksum(state: Mapping[str, object], teams, games) -> str:
    content = {
        "schema_version": state.get("schema_version"),
        "sport": state.get("sport"),
        "classification": state.get("classification"),
        "year": state.get("year"),
        "teams_fetched_at": state.get("teams_fetched_at"),
        "games_fetched_at": state.get("games_fetched_at"),
        "complete_through_week": state.get("complete_through_week"),
        "teams": [asdict(team) for team in teams],
        "games": [asdict(game) for game in games],
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _complete_through(games: tuple[SourceGame, ...]) -> int:
    complete_through = -1
    for week in sorted({game.week for game in games}):
        week_games = tuple(game for game in games if game.week == week)
        if any(
            game.home_points is None or game.away_points is None
            for game in week_games
        ):
            break
        complete_through = week
    return complete_through


class SeasonSnapshotService:
    """Fetch at most teams plus full-season games, then serve immutable snapshots."""

    def __init__(
        self,
        source: SeasonSource,
        cache: SnapshotCache,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(source, SeasonSource):
            raise TypeError("source must satisfy SeasonSource")
        self.source = source
        self.cache = cache
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get(
        self,
        year: int,
        classification: str = "FBS",
        *,
        refresh_games: bool = False,
        required_week: int | None = None,
    ) -> SeasonSnapshot:
        classification = classification.upper()
        with self.cache.lock(year, classification):
            return self._get_locked(
                year,
                classification,
                refresh_games=refresh_games,
                required_week=required_week,
            )

    def _get_locked(
        self,
        year: int,
        classification: str,
        *,
        refresh_games: bool,
        required_week: int | None,
    ) -> SeasonSnapshot:
        state = self.cache.load(year, classification)
        if state is None:
            state = self._empty_state(year, classification)
        else:
            self._validate_and_upgrade(state)
        if state.get("teams") is None:
            teams = tuple(
                self.source.fetch_teams(
                    year, classification, cache_decision="miss"
                )
            )
            state["teams"] = [asdict(team) for team in teams]
            state["teams_fetched_at"] = self._now()
            self._persist(state)
        cache_decision = None
        if state.get("games") is None:
            cache_decision = "miss"
        elif refresh_games:
            cache_decision = "refresh"
        if required_week is not None:
            is_incomplete = int(state.get("complete_through_week", -1)) < int(
                required_week
            )
            if cache_decision is None and is_incomplete:
                cache_decision = "incomplete"
        if cache_decision is not None:
            games = tuple(
                self.source.fetch_games(
                    year, classification, cache_decision=cache_decision
                )
            )
            state["games"] = [asdict(game) for game in games]
            state["games_fetched_at"] = self._now()
            state["complete_through_week"] = _complete_through(games)
            self._persist(state)
        return self._snapshot(state)

    def load_cached(
        self, year: int, classification: str = "FBS"
    ) -> SeasonSnapshot:
        """Read-only rendering/validation/deployment interface: never calls source."""
        classification = classification.upper()
        with self.cache.lock(year, classification):
            state = self.cache.load(year, classification)
            if (
                state is None
                or state.get("teams") is None
                or state.get("games") is None
            ):
                raise SnapshotUnavailable(
                    f"No complete cached CFB {classification} {year} snapshot"
                )
            self._validate_and_upgrade(state)
            return self._snapshot(state)

    def _empty_state(self, year: int, classification: str) -> dict[str, object]:
        return {
            "schema_version": 2,
            "sport": "cfb",
            "classification": classification,
            "year": int(year),
            "teams": None,
            "games": None,
            "complete_through_week": -1,
        }

    def _now(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat()

    def _persist(self, state: dict[str, object]) -> None:
        teams = tuple(SourceTeam(**item) for item in (state.get("teams") or []))
        games = tuple(SourceGame(**item) for item in (state.get("games") or []))
        state["schema_version"] = 2
        state["checksum"] = _checksum(state, teams, games)
        self.cache.save(int(state["year"]), str(state["classification"]), state)

    def _validate_and_upgrade(self, state: dict[str, object]) -> None:
        if state.get("sport") != "cfb":
            raise ValueError("Season snapshot sport verification failed")
        teams = tuple(SourceTeam(**item) for item in (state.get("teams") or []))
        games = tuple(SourceGame(**item) for item in (state.get("games") or []))
        authoritative_completion = _complete_through(games)
        if int(state.get("complete_through_week", -1)) != authoritative_completion:
            raise ValueError(
                "Season snapshot checksum verification failed: "
                "completion metadata verification failed"
            )
        schema_version = int(state.get("schema_version", 1))
        stored_checksum = state.get("checksum")
        if schema_version == 1:
            legacy_checksum = _legacy_checksum(
                int(state["year"]), str(state["classification"]), teams, games
            )
            if stored_checksum is not None and stored_checksum != legacy_checksum:
                raise ValueError("Season snapshot checksum verification failed")
            self._persist(state)
            return
        if schema_version != 2:
            raise ValueError("Unsupported Season snapshot schema version")
        if stored_checksum != _checksum(state, teams, games):
            raise ValueError("Season snapshot checksum verification failed")

    def _snapshot(self, state: dict[str, object]) -> SeasonSnapshot:
        teams = tuple(SourceTeam(**item) for item in (state.get("teams") or []))
        games = tuple(SourceGame(**item) for item in (state.get("games") or []))
        checksum = _checksum(state, teams, games)
        metadata = MappingProxyType(
            {
                "schema_version": state.get("schema_version", 1),
                "teams_fetched_at": state.get("teams_fetched_at"),
                "games_fetched_at": state.get("games_fetched_at"),
                "complete_through_week": state.get("complete_through_week", -1),
            }
        )
        return SeasonSnapshot(
            sport="cfb",
            classification=str(state["classification"]),
            year=int(state["year"]),
            teams=teams,
            games=games,
            metadata=metadata,
            checksum=checksum,
        )
