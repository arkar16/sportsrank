"""Deep Season Snapshot module shared by every CFB data consumer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
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
    from .week_calendar import (
        POSTSEASON_WINDOWS,
        _provider_datetime,
        calendar_provenance,
        canonical_postseason_week,
        canonical_week,
        postseason_calendar_provenance,
    )
    from .postseason_registry import (
        PINNED_REGISTRY,
        PostseasonCorrectionRegistry,
        trusted_registry,
        validate_recovery_classification,
        validate_recovered_games,
    )
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
    from week_calendar import (
        POSTSEASON_WINDOWS,
        _provider_datetime,
        calendar_provenance,
        canonical_postseason_week,
        canonical_week,
        postseason_calendar_provenance,
    )
    from postseason_registry import (
        PINNED_REGISTRY,
        PostseasonCorrectionRegistry,
        trusted_registry,
        validate_recovery_classification,
        validate_recovered_games,
    )
    from snapshot_cache import SnapshotCache


class SnapshotUnavailable(RuntimeError):
    pass


SCHEMA_VERSION = 3
REPAIR_SCHEMA_VERSION = 4
LEGACY_SCHEMA_VERSION = 3
_NEW_SCHEMA_GAME_FIELDS = frozenset(
    {"provider_season_type", "provider_playoff", "phase", "phase_source"}
)


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


def _game_payload(game: SourceGame, *, schema_version: int) -> dict[str, object]:
    payload = asdict(game)
    if payload.get("provider_week") is None:
        payload.pop("provider_week", None)
    if schema_version < REPAIR_SCHEMA_VERSION:
        # Null compatibility fields may appear after reconstructing a legacy
        # dataclass, but non-null phase metadata requires schema 4.
        for field in _NEW_SCHEMA_GAME_FIELDS:
            if payload.get(field) is None:
                payload.pop(field, None)
            else:
                raise ValueError(
                    f"schema {schema_version} cannot carry game field {field}"
                )
    return payload


def _legacy_checksum(year: int, classification: str, teams, games) -> str:
    game_payload = []
    for game in games:
        payload = _game_payload(game, schema_version=LEGACY_SCHEMA_VERSION)
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
    schema_version = int(state.get("schema_version") or 1)
    game_payload = []
    for game in games:
        payload = _game_payload(game, schema_version=schema_version)
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
    if schema_version >= REPAIR_SCHEMA_VERSION:
        content.update(
            {
                "calendar_provenance": state.get("calendar_provenance"),
                "correction_registry_provenance": state.get(
                    "correction_registry_provenance"
                ),
                "migration_provenance": state.get("migration_provenance"),
            }
        )
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
            if game.phase == "postseason":
                expected = canonical_postseason_week(year, game.date)
            else:
                expected = canonical_week(year, game.provider_week, game.date)
        except ValueError as error:
            raise ValueError(
                f"Season snapshot provider metadata verification failed for {year}"
            ) from error
        if int(game.week) != int(expected):
            raise ValueError(
                f"Season snapshot canonical week verification failed for {year}"
            )


def _requires_repair_schema(games: tuple[SourceGame, ...]) -> bool:
    """Return whether schema 4 metadata is needed to seal game phases."""

    return any(
        game.provider_season_type is not None
        or game.provider_playoff is not None
        or game.phase is not None
        or game.phase_source is not None
        for game in games
    )


def _validate_repair_metadata(
    state: Mapping[str, object],
    games: tuple[SourceGame, ...],
    teams: tuple[SourceTeam, ...] | None = None,
) -> None:
    """Validate schema-4 phase/provenance shape without applying policy.

    Date-window and correction-registry membership are validated by their
    owning calendar/registry helpers.  This layer only rejects malformed or
    contradictory sealed fields before a snapshot reaches a consumer.
    """

    allowed_phases = {None, "regular", "postseason"}
    allowed_sources = {None, "provider", "recovery_registry"}
    for game in games:
        if game.phase not in allowed_phases:
            raise ValueError("Season snapshot phase metadata is invalid")
        if game.phase_source not in allowed_sources:
            raise ValueError("Season snapshot phase source metadata is invalid")
        raw_phase = game.provider_season_type
        if raw_phase is not None:
            normalized = str(raw_phase).strip().lower()
            if normalized not in {"regular", "postseason"}:
                raise ValueError("Season snapshot provider seasonType is unsupported")
            if game.phase_source == "provider" and game.phase != normalized:
                raise ValueError("Season snapshot provider phase disagrees with seasonType")
        if game.phase_source == "provider" and game.provider_season_type is None:
            raise ValueError("Season snapshot provider phase is missing seasonType")
        if game.phase_source == "recovery_registry":
            if game.phase != "postseason":
                raise ValueError("Season snapshot recovery phase must be postseason")
            if not game.provider_id or not game.date:
                raise ValueError("Season snapshot recovery phase is missing game identity")
    calendar = state.get("calendar_provenance")
    if calendar is not None and not isinstance(calendar, Mapping):
        raise ValueError("Season snapshot calendar provenance is invalid")
    registry = state.get("correction_registry_provenance")
    if registry is not None and not isinstance(registry, Mapping):
        raise ValueError("Season snapshot correction registry provenance is invalid")
    recovery_rows = any(game.phase_source == "recovery_registry" for game in games)
    provider_backed = any(
        game.provider_id is not None
        or game.date is not None
        or game.provider_week is not None
        or game.provider_season_type is not None
        or game.provider_playoff is not None
        for game in games
    )
    validate_recovery_classification(
        int(state["year"]), games, state.get("correction_registry_provenance")
    )
    if recovery_rows:
        if not isinstance(registry, Mapping):
            raise ValueError("Season snapshot recovery phases require registry provenance")
        if state.get("migration_provenance") is None:
            raise ValueError("Season snapshot recovery phases require migration provenance")
        if not registry.get("version") or not registry.get("checksum"):
            raise ValueError("Season snapshot correction registry provenance is incomplete")
        validate_recovered_games(int(state["year"]), games, registry)
    calendar = state.get("calendar_provenance")
    if any(game.phase == "postseason" for game in games):
        expected_calendar = {
            "regular": calendar_provenance(int(state["year"])),
            "postseason": postseason_calendar_provenance(int(state["year"])),
        }
        if calendar != expected_calendar:
            raise ValueError("Season snapshot calendar provenance is not code-pinned")
    elif calendar is not None and calendar != calendar_provenance(int(state["year"])):
        raise ValueError("Season snapshot calendar provenance is not code-pinned")
    migration = state.get("migration_provenance")
    if migration is None:
        if registry is not None:
            raise ValueError(
                "Season snapshot correction registry provenance requires migration provenance"
            )
        if provider_backed:
            for game in games:
                raw_phase = (
                    str(game.provider_season_type).strip().lower()
                    if game.provider_season_type is not None
                    else None
                )
                if (
                    raw_phase not in {"regular", "postseason"}
                    or game.phase != raw_phase
                    or game.phase_source != "provider"
                ):
                    raise ValueError(
                        "Season snapshot provider metadata lacks a valid phase origin"
                    )
    if migration is not None:
        if not isinstance(migration, Mapping):
            raise ValueError("Season snapshot migration provenance is invalid")
        if not isinstance(registry, Mapping) or dict(registry) != PINNED_REGISTRY.provenance:
            raise ValueError(
                "Season snapshot migration requires pinned registry provenance"
            )
        if (
            migration.get("kind") != "postseason-calendar-repair"
            or migration.get("source_schema_version") != LEGACY_SCHEMA_VERSION
            or migration.get("target_schema_version") != REPAIR_SCHEMA_VERSION
            or migration.get("registry_version") != PINNED_REGISTRY.version
            or migration.get("registry_checksum") != PINNED_REGISTRY.computed_checksum
        ):
            raise ValueError("Season snapshot migration provenance is not code-pinned")
        expected_source_checksum = PINNED_REGISTRY.snapshot_checksums.get(
            int(state["year"])
        )
        if (
            expected_source_checksum is None
            or migration.get("source_snapshot_checksum") != expected_source_checksum
        ):
            raise ValueError("Season snapshot migration source identity is not code-pinned")
        if teams is None:
            raise ValueError("Season snapshot migration validation requires teams")
        _validate_migration_derivation(state, teams, games)


def _validate_migration_derivation(
    state: Mapping[str, object],
    teams: tuple[SourceTeam, ...],
    games: tuple[SourceGame, ...],
) -> None:
    """Reverse the bounded repair and verify the exact schema-3 source digest."""

    migration = state.get("migration_provenance")
    if not isinstance(migration, Mapping):
        raise ValueError("Season snapshot migration provenance is invalid")
    season = int(state["year"])
    reversed_games: list[SourceGame] = []
    for game in games:
        if game.provider_season_type is not None or game.provider_playoff is not None:
            raise ValueError("Season snapshot migration contains unauthorized provider phase metadata")
        entry = PINNED_REGISTRY.lookup(season, game.provider_id or "")
        if game.phase_source == "recovery_registry":
            if entry is None or game.phase != "postseason":
                raise ValueError("Season snapshot migration recovery row is not pinned")
            if int(game.week) != int(entry.canonical_week):
                raise ValueError("Season snapshot migration recovery week is not pinned")
            reversed_games.append(
                replace(
                    game,
                    week=entry.original_canonical_week,
                    phase=None,
                    phase_source=None,
                )
            )
        elif game.phase is None and game.phase_source is None:
            if entry is not None:
                raise ValueError("Season snapshot migration omitted a pinned recovery row")
            reversed_games.append(game)
        else:
            raise ValueError("Season snapshot migration contains unauthorized game phase")
    source_state = dict(state)
    source_state["schema_version"] = LEGACY_SCHEMA_VERSION
    source_state["games"] = [
        _game_payload(game, schema_version=LEGACY_SCHEMA_VERSION)
        for game in reversed_games
    ]
    source_state["complete_through_week"] = _complete_through(tuple(reversed_games))
    for key in (
        "calendar_provenance",
        "correction_registry_provenance",
        "migration_provenance",
        "checksum",
    ):
        source_state.pop(key, None)
    source_checksum = str(migration.get("source_snapshot_checksum", ""))
    if _checksum(source_state, teams, tuple(reversed_games)) != source_checksum:
        raise ValueError("Season snapshot migration does not reproduce the pinned source snapshot")


def _legacy_state_for_migration(
    state: Mapping[str, object], *, season: int, classification: str
) -> tuple[tuple[SourceTeam, ...], tuple[SourceGame, ...]]:
    """Validate one immutable schema-3 input without upgrading it in place."""

    if int(state.get("schema_version", -1)) != LEGACY_SCHEMA_VERSION:
        raise ValueError("postseason migration requires schema-3 source snapshots")
    if state.get("sport") != "cfb" or int(state.get("year", -1)) != int(season):
        raise ValueError("postseason migration source snapshot identity is invalid")
    if str(state.get("classification", "")).upper() != classification.upper():
        raise ValueError("postseason migration source classification is invalid")
    teams = tuple(SourceTeam(**item) for item in (state.get("teams") or []))
    games = tuple(normalize_game(item) for item in (state.get("games") or []))
    _validate_provider_week_mapping(season, games)
    expected_completion = _complete_through(games)
    if int(state.get("complete_through_week", -1)) != expected_completion:
        raise ValueError("postseason migration source completion metadata is invalid")
    if state.get("checksum") != _checksum(state, teams, games):
        raise ValueError("postseason migration source checksum is invalid")
    expected_source_checksum = PINNED_REGISTRY.snapshot_checksums.get(int(season))
    if expected_source_checksum is None or state.get("checksum") != expected_source_checksum:
        raise ValueError("postseason migration source snapshot is not an approved input identity")
    return teams, games


def _migrate_postseason_games(
    season: int,
    games: tuple[SourceGame, ...],
    source_checksum: str,
    registry: PostseasonCorrectionRegistry,
) -> tuple[SourceGame, ...]:
    matched: set[tuple[int, str]] = set()
    migrated: list[SourceGame] = []
    for game in games:
        entry = registry.lookup(season, game.provider_id or "")
        if entry is not None:
            if entry.source_snapshot_checksum != source_checksum:
                raise ValueError("postseason migration source snapshot binding is invalid")
            identity = (
                game.provider_id,
                game.date,
                game.home_team,
                game.away_team,
                game.notes,
                game.provider_week,
                game.week,
            )
            expected_identity = (
                entry.provider_id,
                entry.snapshot_date,
                entry.home_team,
                entry.away_team,
                entry.notes,
                entry.provider_week,
                entry.original_canonical_week,
            )
            if identity != expected_identity:
                raise ValueError(
                    "postseason migration game identity differs from correction registry"
                )
            expected_week = canonical_postseason_week(season, game.date)
            if entry.canonical_week != expected_week:
                raise ValueError("postseason migration correction week is not calendar-derived")
            migrated.append(
                replace(
                    game,
                    week=expected_week,
                    phase="postseason",
                    phase_source="recovery_registry",
                )
            )
            matched.add(entry.key)
            continue
        if (
            game.provider_week == 1
            and game.date is not None
            and season in (2024, 2025)
        ):
            local_day = _provider_datetime(game.date).date()
            if local_day >= POSTSEASON_WINDOWS[season].start_date:
                raise ValueError(
                    "legacy provider Week 1 postseason game is absent from correction registry"
                )
        migrated.append(game)
    expected_keys = {entry.key for entry in registry.entries if entry.season == season}
    if matched != expected_keys:
        raise ValueError("postseason migration did not match every correction registry row")
    return tuple(migrated)


def migrate_postseason_cache(
    source_root: str | Path,
    destination_root: str | Path,
    registry: PostseasonCorrectionRegistry | None = None,
    *,
    classification: str = "FBS",
    seasons: tuple[int, ...] = (2024, 2025, 2026),
) -> tuple[Path, ...]:
    """Copy validated legacy snapshots into a new phase-aware schema-4 root.

    ``source_root`` is the directory containing ``cfb-<classification>-<year>.json``
    files.  It is read-only; ``destination_root`` must be a new empty root.
    Production callers always use the code-pinned registry.  This operation
    never performs provider transport or mutates the source cache.
    """

    classification = classification.upper()
    if not seasons:
        raise ValueError("postseason migration requires at least one season")
    source_path = Path(source_root).resolve()
    destination_path = Path(destination_root).resolve()
    if source_path == destination_path:
        raise ValueError("postseason migration source and destination must differ")
    if destination_path.exists() and any(destination_path.iterdir()):
        raise ValueError("postseason migration destination must be new and empty")
    destination_path.mkdir(parents=True, exist_ok=True)
    pinned = trusted_registry(registry)
    source_cache = SnapshotCache(source_path)
    destination_cache = SnapshotCache(destination_path)
    written: list[Path] = []
    try:
        for season in tuple(int(value) for value in seasons):
            state = source_cache.load(season, classification)
            if state is None:
                raise ValueError(f"postseason migration source snapshot is missing for {season}")
            teams, games = _legacy_state_for_migration(
                state, season=season, classification=classification
            )
            source_checksum = str(state["checksum"])
            corrected_games = _migrate_postseason_games(
                season, games, source_checksum, pinned
            )
            has_postseason = any(game.phase == "postseason" for game in corrected_games)
            calendar = (
                {
                    "regular": calendar_provenance(season),
                    "postseason": postseason_calendar_provenance(season),
                }
                if has_postseason
                else calendar_provenance(season)
            )
            migrated_state = dict(state)
            migrated_state["schema_version"] = REPAIR_SCHEMA_VERSION
            migrated_state["games"] = [
                _game_payload(game, schema_version=REPAIR_SCHEMA_VERSION)
                for game in corrected_games
            ]
            migrated_state["complete_through_week"] = _complete_through(corrected_games)
            migrated_state["calendar_provenance"] = calendar
            migrated_state["correction_registry_provenance"] = pinned.provenance
            migrated_state["migration_provenance"] = {
                "kind": "postseason-calendar-repair",
                "source_schema_version": LEGACY_SCHEMA_VERSION,
                "source_snapshot_checksum": source_checksum,
                "target_schema_version": REPAIR_SCHEMA_VERSION,
                "registry_version": pinned.version,
                "registry_checksum": pinned.computed_checksum,
            }
            migrated_state["checksum"] = _checksum(
                migrated_state, teams, corrected_games
            )
            _validate_repair_metadata(migrated_state, corrected_games, teams)
            written.append(destination_cache.save(season, classification, migrated_state))
    except BaseException:
        # A failed migration must never masquerade as a complete destination.
        for path in written:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    return tuple(written)


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
            if any(game.phase == "postseason" for game in games):
                state["calendar_provenance"] = {
                    "regular": calendar_provenance(int(state["year"])),
                    "postseason": postseason_calendar_provenance(int(state["year"])),
                }
            elif any(game.provider_week is not None for game in games):
                state["calendar_provenance"] = calendar_provenance(int(state["year"]))
        if _requires_repair_schema(games):
            state["schema_version"] = REPAIR_SCHEMA_VERSION
        elif int(state.get("schema_version", SCHEMA_VERSION)) >= REPAIR_SCHEMA_VERSION:
            state["schema_version"] = REPAIR_SCHEMA_VERSION
        else:
            state["schema_version"] = SCHEMA_VERSION
        if state["schema_version"] >= REPAIR_SCHEMA_VERSION:
            _validate_repair_metadata(state, games, teams)
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
            schema_version = 2
        elif schema_version == 2:
            if stored_checksum != _stored_checksum(state, include_schema=True):
                raise ValueError("Season snapshot checksum verification failed")
        elif schema_version == SCHEMA_VERSION:
            # Schema 3 is a sealed legacy input.  It may be consumed without
            # mutation, but postseason correction data must use the explicit
            # schema 4 migration path below.
            pass
        elif schema_version == REPAIR_SCHEMA_VERSION:
            pass
        else:
            raise ValueError("Unsupported Season snapshot schema version")
        games = tuple(normalize_game(item) for item in (state.get("games") or []))
        _validate_provider_week_mapping(int(state["year"]), games)
        validate_recovery_classification(
            int(state["year"]), games, state.get("correction_registry_provenance")
        )
        if schema_version in (1, 2):
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
        if schema_version == REPAIR_SCHEMA_VERSION:
            _validate_repair_metadata(state, games, teams)

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
                "calendar_provenance": state.get("calendar_provenance"),
                "correction_registry_provenance": state.get(
                    "correction_registry_provenance"
                ),
                "migration_provenance": state.get("migration_provenance"),
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
