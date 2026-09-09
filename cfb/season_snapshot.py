"""Deep Season Snapshot module shared by every CFB data consumer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import Callable, Mapping

if __package__:
    from .game_dispositions import apply_cancellation_registry
    from .season_source import (
        SeasonSource,
        SourceGame,
        SourceTeam,
        is_completed,
        is_explicit_non_played,
        normalize_game,
    )
    from .week_calendar import canonical_week
    from .snapshot_cache import SnapshotCache
else:
    from game_dispositions import apply_cancellation_registry
    from season_source import (
        SeasonSource,
        SourceGame,
        SourceTeam,
        is_completed,
        is_explicit_non_played,
        normalize_game,
    )
    from week_calendar import canonical_week
    from snapshot_cache import SnapshotCache


class SnapshotUnavailable(RuntimeError):
    pass


SCHEMA_VERSION = 3


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
        # The persisted value is provenance, not authority.  A caller can
        # reseal a snapshot after changing metadata, so every consumer must
        # derive the completion edge from the games' dispositions and scores.
        return _complete_through(self.games)


def _legacy_checksum(year: int, classification: str, teams, games) -> str:
    game_payload = []
    for game in games:
        payload = asdict(game)
        # ``provider_week`` was added as optional metadata.  Omitting its null
        # compatibility value keeps the established v1/v2 digest byte-for-byte
        # stable while non-null provider metadata remains covered below.
        if payload.get("provider_week") is None:
            payload.pop("provider_week", None)
        game_payload.append(payload)
    content = {
        "sport": "cfb",
        "classification": classification.upper(),
        "year": int(year),
        "teams": [asdict(team) for team in teams],
        "games": game_payload,
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _checksum(state: Mapping[str, object], teams, games) -> str:
    game_payload = []
    for game in games:
        payload = asdict(game)
        if payload.get("provider_week") is None:
            payload.pop("provider_week", None)
        game_payload.append(payload)
    content = {
        "schema_version": state.get("schema_version"),
        "sport": state.get("sport"),
        "classification": state.get("classification"),
        "year": state.get("year"),
        "teams_fetched_at": state.get("teams_fetched_at"),
        "games_fetched_at": state.get("games_fetched_at"),
        "complete_through_week": state.get("complete_through_week"),
        "teams": [asdict(team) for team in teams],
        "games": game_payload,
    }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _failure_message(
    state: Mapping[str, object], classification: str, year: int
) -> str:
    failure = state.get("last_fetch_failure")
    endpoint = "snapshot"
    if isinstance(failure, Mapping):
        endpoint_value = failure.get("endpoint")
        if endpoint_value:
            endpoint = str(endpoint_value)
    return (
        f"Cached CFB {classification} {year} {endpoint} fetch previously failed; "
        "explicit refresh required"
    )


def _stored_checksum(state: Mapping[str, object], *, include_schema: bool) -> str:
    """Reproduce pre-v3 checksums from their exact stored normalized payload."""

    content = {
        "sport": state.get("sport"),
        "classification": state.get("classification"),
        "year": state.get("year"),
        "teams": state.get("teams") or [],
        "games": state.get("games") or [],
    }
    if include_schema:
        content = {
            "schema_version": state.get("schema_version"),
            "sport": state.get("sport"),
            "classification": state.get("classification"),
            "year": state.get("year"),
            "teams_fetched_at": state.get("teams_fetched_at"),
            "games_fetched_at": state.get("games_fetched_at"),
            "complete_through_week": state.get("complete_through_week"),
            "teams": state.get("teams") or [],
            "games": state.get("games") or [],
        }
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _complete_through(games: tuple[SourceGame, ...]) -> int:
    complete_through = -1
    active = tuple(game for game in games if not is_explicit_non_played(game))
    for week in sorted({game.week for game in active}):
        week_games = tuple(game for game in active if game.week == week)
        if any(not is_completed(game) for game in week_games):
            break
        complete_through = week
    return complete_through


def _provider_metadata_complete(item: object) -> bool:
    """Return whether a cached game seals the fields needed for Week 0."""

    if isinstance(item, Mapping):
        values = (
            item.get("provider_id", item.get("id")),
            item.get("date", item.get("start_date", item.get("startDate"))),
            item.get("provider_week", item.get("providerWeek")),
        )
    else:
        values = (
            getattr(item, "provider_id", None),
            getattr(item, "date", None),
            getattr(item, "provider_week", None),
        )
    return all(value not in (None, "") for value in values)


def _requires_provider_metadata_refresh(
    source: SeasonSource,
    state: Mapping[str, object],
) -> bool:
    if not getattr(source, "requires_provider_metadata", False):
        return False
    games = state.get("games")
    return games is not None and any(
        not _provider_metadata_complete(item) for item in (games or [])
    )


def _validate_provider_week_mapping(year: int, games: tuple[SourceGame, ...]) -> None:
    for game in games:
        if game.provider_week is None:
            continue
        try:
            expected = canonical_week(year, game.provider_week, game.date)
        except ValueError as error:
            raise ValueError(
                f"Season snapshot provider metadata verification failed for {year}"
            ) from error
        if int(game.week) != int(expected):
            raise ValueError(
                f"Season snapshot canonical week verification failed for {year}"
            )


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

    def prime_teams(
        self, year: int, classification: str = "FBS", *, refresh: bool = False
    ) -> tuple[SourceTeam, ...]:
        """Fetch and persist only the teams portion of a Season Snapshot.

        The recovery smoke/prime step deliberately stops before games.  A
        later :meth:`get` call sees the persisted teams and spends its one
        request on games only, while preserving the normal cache and audit
        boundary used by every other consumer.
        """

        classification = classification.upper()
        with self.cache.lock(year, classification):
            state = self.cache.load(year, classification)
            if state is None:
                state = self._empty_state(year, classification)
            else:
                self._validate_and_upgrade(state)
            if (
                state.get("last_fetch_failure") is not None
                and state.get("teams") is None
                and not refresh
            ):
                raise SnapshotUnavailable(
                    _failure_message(state, classification, int(year))
                )
            if state.get("teams") is None:
                cache_decision = (
                    "refresh" if refresh and state.get("last_fetch_failure") else "miss"
                )
                try:
                    teams = tuple(
                        self.source.fetch_teams(
                            year, classification, cache_decision=cache_decision
                        )
                    )
                except Exception as error:
                    self._record_fetch_failure(
                        state,
                        endpoint="teams",
                        cache_decision=cache_decision,
                        error=error,
                    )
                    raise
                state["teams"] = [asdict(team) for team in teams]
                state["teams_fetched_at"] = self._now()
                state.pop("last_fetch_failure", None)
                self._persist(state)
            return tuple(
                SourceTeam(**item) for item in (state.get("teams") or [])
            )

    prime = prime_teams

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
            if (
                _requires_provider_metadata_refresh(
                    self.source,
                    state,
                )
                and not refresh_games
            ):
                raise SnapshotUnavailable(
                    f"Cached CFB {classification} {year} games lack provider metadata; "
                    "explicit games refresh required"
                )
            self._validate_and_upgrade(state)
        if (
            state.get("last_fetch_failure") is not None
            and not refresh_games
            and self._fetch_needed(state, required_week)
        ):
            raise SnapshotUnavailable(_failure_message(state, classification, int(year)))
        if state.get("teams") is None:
            cache_decision = (
                "refresh"
                if refresh_games and state.get("last_fetch_failure")
                else "miss"
            )
            try:
                teams = tuple(
                    self.source.fetch_teams(
                        year, classification, cache_decision=cache_decision
                    )
                )
            except Exception as error:
                self._record_fetch_failure(
                    state,
                    endpoint="teams",
                    cache_decision=cache_decision,
                    error=error,
                )
                raise
            state["teams"] = [asdict(team) for team in teams]
            state["teams_fetched_at"] = self._now()
            state.pop("last_fetch_failure", None)
            self._persist(state)
        cache_decision = None
        if state.get("games") is None:
            cache_decision = (
                "refresh"
                if refresh_games and state.get("last_fetch_failure")
                else "miss"
            )
        elif refresh_games:
            cache_decision = "refresh"
        if required_week is not None:
            is_incomplete = int(state.get("complete_through_week", -1)) < int(
                required_week
            )
            if cache_decision is None and is_incomplete:
                cache_decision = "incomplete"
        if cache_decision is not None:
            try:
                games = tuple(
                    self.source.fetch_games(
                        year, classification, cache_decision=cache_decision
                    )
                )
            except Exception as error:
                self._record_fetch_failure(
                    state,
                    endpoint="games",
                    cache_decision=cache_decision,
                    error=error,
                )
                raise
            state["games"] = [asdict(game) for game in games]
            state["games_fetched_at"] = self._now()
            state["complete_through_week"] = _complete_through(games)
            state.pop("last_fetch_failure", None)
            self._persist(state)
        return self._snapshot(state)

    @staticmethod
    def _fetch_needed(
        state: Mapping[str, object], required_week: int | None
    ) -> bool:
        if state.get("teams") is None or state.get("games") is None:
            return True
        if required_week is not None:
            return int(state.get("complete_through_week", -1)) < int(required_week)
        return False

    def _record_fetch_failure(
        self,
        state: dict[str, object],
        *,
        endpoint: str,
        cache_decision: str,
        error: Exception,
    ) -> None:
        # Persist only credential-safe operational facts.  The marker prevents
        # a normal cache read from automatically spending another request; an
        # explicit refresh can clear it after a successful retry.
        state["last_fetch_failure"] = {
            "endpoint": endpoint,
            "cache_decision": cache_decision,
            "error_type": type(error).__name__,
            "failed_at": self._now(),
        }
        self._persist(state)

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
            if (
                _requires_provider_metadata_refresh(
                    self.source,
                    state,
                )
            ):
                raise SnapshotUnavailable(
                    f"Cached CFB {classification} {year} games lack provider metadata; "
                    "explicit games refresh required"
                )
            self._validate_and_upgrade(state)
            return self._snapshot(state)

    def _empty_state(self, year: int, classification: str) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
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
        games = tuple(normalize_game(item) for item in (state.get("games") or []))
        if state.get("games") is not None:
            games = apply_cancellation_registry(int(state["year"]), str(state["classification"]), games)
            state["games"] = [asdict(game) for game in games]
            state["complete_through_week"] = _complete_through(games)
            _validate_provider_week_mapping(int(state["year"]), games)
        state["schema_version"] = SCHEMA_VERSION
        state["checksum"] = _checksum(state, teams, games)
        self.cache.save(int(state["year"]), str(state["classification"]), state)

    def _validate_and_upgrade(self, state: dict[str, object]) -> None:
        if state.get("sport") != "cfb":
            raise ValueError("Season snapshot sport verification failed")
        teams = tuple(SourceTeam(**item) for item in (state.get("teams") or []))
        schema_version = int(state.get("schema_version", 1))
        stored_checksum = state.get("checksum")
        if schema_version == 1:
            legacy_checksum = _stored_checksum(state, include_schema=False)
            if stored_checksum is not None and stored_checksum != legacy_checksum:
                raise ValueError("Season snapshot checksum verification failed")
            # Preserve the established one-time v1-on-disk migration contract.
            # Existing v2 production evidence is never rewritten by the v3
            # disposition enrichment below.
            migrated_v2 = dict(state)
            migrated_v2["schema_version"] = 2
            migrated_v2["checksum"] = _stored_checksum(migrated_v2, include_schema=True)
            self.cache.save(int(state["year"]), str(state["classification"]), migrated_v2)
        elif schema_version == 2:
            if stored_checksum != _stored_checksum(state, include_schema=True):
                raise ValueError("Season snapshot checksum verification failed")
        elif schema_version != SCHEMA_VERSION:
            raise ValueError("Unsupported Season snapshot schema version")
        games = tuple(normalize_game(item) for item in (state.get("games") or []))
        _validate_provider_week_mapping(int(state["year"]), games)
        if schema_version < SCHEMA_VERSION:
            legacy_completion = _complete_through(games)
            if int(state.get("complete_through_week", -1)) != legacy_completion:
                raise ValueError("Season snapshot checksum verification failed: completion metadata verification failed")
            games = apply_cancellation_registry(int(state["year"]), str(state["classification"]), games)
            state["games"] = [asdict(game) for game in games]
            state["complete_through_week"] = _complete_through(games)
            state["schema_version"] = SCHEMA_VERSION
            state["checksum"] = _checksum(state, teams, games)
            return
        authoritative_completion = _complete_through(games)
        if int(state.get("complete_through_week", -1)) != authoritative_completion:
            raise ValueError("Season snapshot checksum verification failed: completion metadata verification failed")
        if stored_checksum != _checksum(state, teams, games):
            raise ValueError("Season snapshot checksum verification failed")

    def _snapshot(self, state: dict[str, object]) -> SeasonSnapshot:
        teams = tuple(SourceTeam(**item) for item in (state.get("teams") or []))
        games = tuple(normalize_game(item) for item in (state.get("games") or []))
        checksum = _checksum(state, teams, games)
        metadata = MappingProxyType(
            {
                "schema_version": state.get("schema_version", SCHEMA_VERSION),
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
