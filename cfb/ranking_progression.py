"""Pure derivation and rendering for a season's CORS progression table.

The ranking engine owns CORS calculation.  This module deliberately sits one
step after that boundary: it accepts the already calculated checkpoint rows,
checks that they belong to the supplied :class:`SeasonSnapshot`, and prepares
safe data for a static reference page.  It never reads ranking HTML and never
recalculates points or national ranks.

``build_progression`` is the integration seam used by Release.  Its output is
an immutable-ish value object with an explicit cell for every team and planned
checkpoint.  Missing or future checkpoints are represented as unavailable
cells; they are never populated from another checkpoint.  ``render_progression_html``
and ``progression_json`` are pure serializers of that value object.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import html
import json
import math
import re
from types import MappingProxyType
from typing import Any

try:  # Package imports are used by tests and the Release builder.
    from .ranking_engine import MODEL_VERSION
    from .season_snapshot import (
        SeasonSnapshot,
        _checksum as _snapshot_checksum,
        _legacy_checksum as _legacy_snapshot_checksum,
    )
    from .season_source import is_completed, is_explicit_non_played
except ImportError:  # Preserve direct execution from the cfb/ directory.
    from ranking_engine import MODEL_VERSION
    from season_snapshot import (
        SeasonSnapshot,
        _checksum as _snapshot_checksum,
        _legacy_checksum as _legacy_snapshot_checksum,
    )
    from season_source import is_completed, is_explicit_non_played


SCHEMA_VERSION = 1
PRESEASON = "PRESEASON"
FINAL = "FINAL"
_WEEK_KEY = re.compile(r"^(?:W(?:EEK)?[ _-]*)?(\d+)$", re.IGNORECASE)
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f<>]+$")


class ProgressionContractError(ValueError):
    """Raised when checkpoint rows cannot satisfy the progression contract."""


@dataclass(frozen=True)
class ProgressionCell:
    """One team's value at one checkpoint.

    ``points`` is the engine's CORS value without a new rounding step and
    ``rank`` is the engine-provided national rank.  An unavailable cell has
    neither value and carries an explicit reason.
    """

    available: bool
    points: float | None = None
    rank: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.available:
            if self.points is None or self.rank is None:
                raise ProgressionContractError(
                    "available progression cells require points and rank"
                )
            if self.reason is not None:
                raise ProgressionContractError(
                    "available progression cells cannot have an unavailable reason"
                )
        elif self.points is not None or self.rank is not None:
            raise ProgressionContractError(
                "unavailable progression cells cannot carry ranking values"
            )
        elif not self.reason:
            raise ProgressionContractError(
                "unavailable progression cells require a reason"
            )

    def to_dict(self) -> dict[str, Any]:
        if self.available:
            # Insertion order is intentional: points precede national rank in
            # the public machine-readable shape as well as the HTML table.
            return {
                "available": True,
                "points": self.points,
                "rank": self.rank,
            }
        return {"available": False, "reason": self.reason}


@dataclass(frozen=True)
class ProgressionCheckpoint:
    """A planned checkpoint and its validated availability."""

    key: str
    kind: str
    week: int | None
    available: bool
    reason: str | None
    source_snapshot: str
    dataset_id: str
    model_version: str

    def __post_init__(self) -> None:
        if self.available and self.reason is not None:
            raise ProgressionContractError(
                f"available checkpoint {self.key} cannot have a reason"
            )
        if not self.available and not self.reason:
            raise ProgressionContractError(
                f"unavailable checkpoint {self.key} requires a reason"
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "key": self.key,
            "kind": self.kind,
            "week": self.week,
            "available": self.available,
            "source_snapshot": self.source_snapshot,
            "dataset_id": self.dataset_id,
            "model_version": self.model_version,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class ProgressionTeam:
    """A stable alphabetical roster row with one cell per checkpoint."""

    school: str
    conference: str
    cells: Mapping[str, ProgressionCell] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "cells", MappingProxyType(dict(self.cells)))

    def to_dict(self) -> dict[str, Any]:
        return {
            # ``team``/``rows`` are deliberate public derived vocabulary.  A
            # mapping with both ``school`` and ``conference`` is recognized by
            # the repository's public-safety guard as a provider Team record.
            "team": self.school,
            "conference": self.conference,
            "checkpoints": {
                key: self.cells[key].to_dict() for key in self.cells
            },
        }


@dataclass(frozen=True)
class ProgressionDocument:
    """Validated progression data ready for independent serialization."""

    sport: str
    classification: str
    season: int
    dataset_id: str
    model_version: str
    source_snapshot: str
    carryover_identity: str
    phase: str
    target_week: int
    checkpoints: tuple[ProgressionCheckpoint, ...]
    teams: tuple[ProgressionTeam, ...]
    schema_version: int = SCHEMA_VERSION

    @property
    def provenance(self) -> dict[str, Any]:
        """Safe provenance fields suitable for public derived output."""

        return {
            "sport": self.sport,
            "classification": self.classification,
            "season": self.season,
            "dataset_id": self.dataset_id,
            "model_version": self.model_version,
            "source_snapshot": self.source_snapshot,
        }

    def checkpoint(self, key: str) -> ProgressionCheckpoint:
        normalized = _normalize_checkpoint_key(key)
        for checkpoint in self.checkpoints:
            if checkpoint.key == normalized:
                return checkpoint
        raise KeyError(normalized)

    def team(self, school: str) -> ProgressionTeam:
        for team in self.teams:
            if team.school == school:
                return team
        raise KeyError(school)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sport": self.sport,
            "classification": self.classification,
            "season": self.season,
            "dataset_id": self.dataset_id,
            "model_version": self.model_version,
            "source_snapshot": self.source_snapshot,
            "carryover_identity": self.carryover_identity,
            "phase": self.phase,
            "target_week": self.target_week,
            "provenance": self.provenance,
            "checkpoints": [checkpoint.to_dict() for checkpoint in self.checkpoints],
            "rows": [team.to_dict() for team in self.teams],
        }

    def to_json(self) -> str:
        return progression_json(self)


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProgressionContractError(f"{field_name} must be text")
    value = value.strip()
    if not value or _SAFE_TEXT.fullmatch(value) is None:
        raise ProgressionContractError(f"{field_name} contains unsafe text")
    return value


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ProgressionContractError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProgressionContractError(
            f"{field_name} must be a finite number"
        ) from exc
    if not math.isfinite(number):
        raise ProgressionContractError(f"{field_name} must be a finite number")
    return number


def _positive_rank(value: Any, field_name: str, roster_size: int) -> int:
    if isinstance(value, bool):
        raise ProgressionContractError(f"{field_name} must be a positive integer")
    if isinstance(value, float) and not value.is_integer():
        raise ProgressionContractError(f"{field_name} must be a positive integer")
    try:
        rank = int(value)
    except (TypeError, ValueError) as exc:
        raise ProgressionContractError(
            f"{field_name} must be a positive integer"
        ) from exc
    if rank < 1 or rank > roster_size:
        raise ProgressionContractError(
            f"{field_name} must be between 1 and {roster_size}"
        )
    return rank


def _strict_int(value: Any, field_name: str) -> int:
    """Parse an integer identity without truncating booleans or fractions."""

    if isinstance(value, bool):
        raise ProgressionContractError(f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ProgressionContractError(f"{field_name} must be an integer")
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not re.fullmatch(r"-?(?:0|[1-9]\d*)", stripped):
            raise ProgressionContractError(f"{field_name} must be an integer")
        return int(stripped)
    raise ProgressionContractError(f"{field_name} must be an integer")


def _normalize_checkpoint_key(value: Any) -> str:
    if isinstance(value, bool):
        raise ProgressionContractError("checkpoint key must not be boolean")
    if isinstance(value, int):
        if value < 0:
            raise ProgressionContractError("checkpoint Week must be non-negative")
        return f"W{value}"
    if not isinstance(value, str):
        raise ProgressionContractError("checkpoint key must be PRESEASON, W<n>, or FINAL")
    normalized = value.strip().upper()
    if normalized in {"PRESEASON", "PRE-SEASON", "PRE"}:
        return PRESEASON
    if normalized == FINAL:
        return FINAL
    match = _WEEK_KEY.fullmatch(normalized)
    if match is not None:
        return f"W{int(match.group(1))}"
    raise ProgressionContractError(
        f"unsupported checkpoint key {value!r}; expected PRESEASON, W<n>, or FINAL"
    )


def _phase(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProgressionContractError("phase must be preseason, week, or final")
    normalized = value.strip().lower()
    if normalized not in {"preseason", "week", "final"}:
        raise ProgressionContractError("phase must be preseason, week, or final")
    return normalized


def _snapshot_roster(snapshot: SeasonSnapshot) -> dict[str, str]:
    try:
        teams = tuple(snapshot.teams)
        classification = _text(snapshot.classification, "snapshot classification").upper()
        season = _strict_int(snapshot.year, "snapshot year")
    except (AttributeError, TypeError, ValueError, ProgressionContractError) as exc:
        raise ProgressionContractError("snapshot is not a valid SeasonSnapshot") from exc
    if season < 0:
        raise ProgressionContractError("snapshot year must be non-negative")
    if not classification:
        raise ProgressionContractError("snapshot classification is required")
    roster: dict[str, str] = {}
    for index, team in enumerate(teams):
        try:
            school = _text(team.school, f"snapshot team {index} school")
            conference = _text(team.conference, f"snapshot team {index} conference")
        except AttributeError as exc:
            raise ProgressionContractError(
                f"snapshot team {index} is missing school or conference"
            ) from exc
        if school in roster:
            raise ProgressionContractError(f"snapshot contains duplicate team {school!r}")
        roster[school] = conference
    if not roster:
        raise ProgressionContractError("snapshot must contain at least one team")
    return roster


def _snapshot_identity(snapshot: SeasonSnapshot) -> tuple[str, str, int, str]:
    try:
        sport = _text(snapshot.sport, "snapshot sport").lower()
        classification = _text(snapshot.classification, "snapshot classification").upper()
        season = _strict_int(snapshot.year, "snapshot year")
        source_snapshot = _text(snapshot.checksum, "snapshot checksum")
    except ProgressionContractError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProgressionContractError("snapshot identity is incomplete") from exc
    if sport != "cfb":
        raise ProgressionContractError(f"unsupported snapshot sport {sport!r}")
    if re.fullmatch(r"[0-9a-f]{64}", source_snapshot) is None:
        raise ProgressionContractError("snapshot checksum must be a lowercase SHA-256 digest")
    metadata = dict(snapshot.metadata)
    schema_version = _strict_int(
        metadata.get("schema_version", 3), "snapshot schema_version"
    )
    state: dict[str, Any] = {
        "schema_version": schema_version,
        "sport": sport,
        "classification": classification,
        "year": season,
        "teams_fetched_at": metadata.get("teams_fetched_at"),
        "games_fetched_at": metadata.get("games_fetched_at"),
        "complete_through_week": metadata.get(
            "complete_through_week", snapshot.complete_through_week
        ),
    }
    if schema_version >= 4:
        state.update(
            {
                "calendar_provenance": metadata.get("calendar_provenance"),
                "correction_registry_provenance": metadata.get(
                    "correction_registry_provenance"
                ),
                "migration_provenance": metadata.get("migration_provenance"),
            }
        )
    try:
        expected_checksum = (
            _legacy_snapshot_checksum(season, classification, snapshot.teams, snapshot.games)
            if schema_version < 3
            else _snapshot_checksum(state, snapshot.teams, snapshot.games)
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ProgressionContractError("snapshot checksum could not be verified") from exc
    if expected_checksum != source_snapshot:
        raise ProgressionContractError("snapshot checksum verification failed")
    return sport, classification, season, source_snapshot


def _season_boundaries(snapshot: SeasonSnapshot) -> tuple[int, int, bool]:
    try:
        games = tuple(snapshot.games)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProgressionContractError("snapshot completion boundary is invalid") from exc
    active = tuple(game for game in games if not is_explicit_non_played(game))
    weeks: dict[int, list[Any]] = {}
    for game in active:
        week = _strict_int(game.week, "snapshot game week")
        if week < 0:
            raise ProgressionContractError("snapshot game week must be non-negative")
        weeks.setdefault(week, []).append(game)
    scheduled_end = max(weeks, default=-1)
    authoritative = -1
    for week in sorted(weeks):
        if any(not is_completed(game) for game in weeks[week]):
            break
        authoritative = week
    try:
        reported = _strict_int(snapshot.complete_through_week, "snapshot complete_through_week")
    except AttributeError as exc:
        raise ProgressionContractError("snapshot completion boundary is invalid") from exc
    if reported != authoritative:
        raise ProgressionContractError(
            "snapshot complete_through_week disagrees with its game dispositions"
        )
    complete_through = authoritative
    if complete_through < -1 or complete_through > scheduled_end:
        raise ProgressionContractError(
            "snapshot complete_through_week is outside its scheduled game boundary"
        )
    season_complete = bool(active) and all(is_completed(game) for game in active)
    return complete_through, scheduled_end, season_complete


def _validate_common_identity(
    value: Mapping[str, Any],
    *,
    label: str,
    sport: str,
    classification: str,
    season: int,
    dataset_id: str,
    model_version: str,
    source_snapshot: str,
) -> None:
    expected: dict[str, Any] = {
        "sport": sport,
        "classification": classification,
        "season": season,
        "dataset_id": dataset_id,
        "model_version": model_version,
        "source_snapshot": source_snapshot,
    }
    # The Release adapter has used both ``season``/``year`` and
    # ``source_snapshot``/``snapshot`` spellings at different boundaries.  A
    # supplied alias remains part of the identity claim, so validate every
    # spelling instead of letting a canonical key hide a conflicting alias.
    fields = {
        "sport": ("sport",),
        "classification": ("classification",),
        "season": ("season", "year"),
        "dataset_id": ("dataset_id",),
        "model_version": ("model_version",),
        "source_snapshot": ("source_snapshot", "snapshot"),
    }
    for key, expected_value in expected.items():
        for value_key in fields[key]:
            if value_key not in value:
                continue
            actual = value[value_key]
            try:
                if key == "season":
                    actual = _strict_int(actual, f"{label} {value_key}")
                elif key in {"sport", "classification"}:
                    if not isinstance(actual, str):
                        raise ProgressionContractError("identity value must be text")
                    actual = (
                        actual.strip().lower()
                        if key == "sport"
                        else actual.strip().upper()
                    )
                elif not isinstance(actual, str):
                    raise ProgressionContractError("identity value must be text")
            except ProgressionContractError as exc:
                raise ProgressionContractError(
                    f"{label} {value_key} does not match snapshot identity"
                ) from exc
            if actual != expected_value:
                raise ProgressionContractError(
                    f"{label} {value_key} does not match snapshot identity"
                )


def _unwrap_checkpoint_value(
    value: Any,
    *,
    label: str,
) -> tuple[Sequence[Mapping[str, Any]] | None, Mapping[str, Any], str | None]:
    """Return rows, optional wrapper identity, and an unavailable reason."""

    if value is None:
        return None, {}, "ranking_missing"
    if isinstance(value, Mapping):
        if value.get("available") is False:
            reason = value.get("reason", "ranking_missing")
            return None, value, _text(reason, f"{label} unavailable reason")
        if "rows" in value:
            rows = value["rows"]
        elif "ranking" in value:
            rows = value["ranking"]
        else:
            raise ProgressionContractError(
                f"{label} wrapper must contain rows or ranking"
            )
        metadata = value
    else:
        rows = value
        metadata = {}
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise ProgressionContractError(f"{label} rows must be a sequence of objects")
    return rows, metadata, None


def _validate_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
    roster: Mapping[str, str],
    sport: str,
    classification: str,
    season: int,
    dataset_id: str,
    model_version: str,
    source_snapshot: str,
) -> dict[str, tuple[float, int]]:
    if len(rows) != len(roster):
        raise ProgressionContractError(
            f"{label} must contain the complete roster ({len(roster)} rows expected)"
        )
    values: dict[str, tuple[float, int]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ProgressionContractError(f"{label} row {index} is not an object")
        _validate_common_identity(
            row,
            label=f"{label} row {index}",
            sport=sport,
            classification=classification,
            season=season,
            dataset_id=dataset_id,
            model_version=model_version,
            source_snapshot=source_snapshot,
        )
        school = _text(row.get("school"), f"{label} row {index} school")
        if school in values:
            raise ProgressionContractError(f"{label} contains duplicate school {school!r}")
        if school not in roster:
            raise ProgressionContractError(
                f"{label} contains school {school!r} outside the snapshot roster"
            )
        if "conference" in row and row.get("conference") is not None:
            conference = _text(
                row.get("conference"), f"{label} row {index} conference"
            )
            if conference != roster[school]:
                raise ProgressionContractError(
                    f"{label} row {index} conference disagrees with snapshot"
                )
        if "cors" not in row and "points" not in row:
            raise ProgressionContractError(f"{label} row {index} has no cors value")
        if "cors" in row and "points" in row:
            cors = _finite_number(row["cors"], f"{label} row {index} cors")
            points = _finite_number(row["points"], f"{label} row {index} points")
            if cors != points:
                raise ProgressionContractError(
                    f"{label} row {index} cors and points disagree"
                )
        else:
            cors = _finite_number(
                row.get("cors", row.get("points")), f"{label} row {index} cors"
            )
        if "rank" not in row:
            raise ProgressionContractError(f"{label} row {index} has no national rank")
        rank = _positive_rank(row["rank"], f"{label} row {index} rank", len(roster))
        values[school] = (cors, rank)
    missing = sorted(set(roster) - set(values))
    if missing:
        raise ProgressionContractError(f"{label} is missing roster teams: {missing}")
    ranks = [rank for _, rank in values.values()]
    if sorted(ranks) != list(range(1, len(roster) + 1)):
        raise ProgressionContractError(
            f"{label} national ranks must be the engine's complete 1..{len(roster)} set"
        )
    return values


def _default_dataset_id(source_snapshot: str) -> str:
    return f"snapshot:{source_snapshot}"


def _default_carryover_identity(
    values_by_key: Mapping[str, Mapping[str, tuple[float, int]]],
) -> str:
    """Bind the rendered run to the effective PRESEASON carryover values."""

    preseason_values = values_by_key.get(PRESEASON)
    if preseason_values is None:
        return "unavailable"
    safe_values = {
        school: {"points": points, "rank": rank}
        for school, (points, rank) in sorted(preseason_values.items())
    }
    encoded = json.dumps(safe_values, sort_keys=True, separators=(",", ":")).encode()
    return f"derived-preseason:{hashlib.sha256(encoded).hexdigest()}"


def build_progression(
    snapshot: SeasonSnapshot,
    rankings: Mapping[Any, Any] | None = None,
    preseason_rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    final_rows: Sequence[Mapping[str, Any]] | None = None,
    phase: str | None = None,
    target_week: int | None = None,
    dataset_id: str | None = None,
    model_version: str = MODEL_VERSION,
    source_snapshot: str | None = None,
    carryover_identity: str | None = None,
    ranking_metadata: Mapping[Any, Mapping[str, Any]] | None = None,
) -> ProgressionDocument:
    """Validate engine rows and derive a full-roster progression document.

    ``rankings`` is keyed by numbered Week (``0`` or ``"W0"``) and may also
    contain ``"PRESEASON"`` or ``"FINAL"``.  ``preseason_rows`` and
    ``final_rows`` are explicit convenience inputs for the Release builder.
    The snapshot completion boundary and the validated ``phase``/``target_week``
    control which checkpoints can be available; labels cannot make a future
    checkpoint eligible.
    """

    if not isinstance(snapshot, SeasonSnapshot):
        raise ProgressionContractError("snapshot must be a SeasonSnapshot")
    sport, classification, season, snapshot_checksum = _snapshot_identity(snapshot)
    roster = _snapshot_roster(snapshot)
    complete_through, scheduled_end, season_complete = _season_boundaries(snapshot)
    if source_snapshot is None:
        source_snapshot = snapshot_checksum
    source_snapshot = _text(source_snapshot, "source_snapshot")
    if source_snapshot != snapshot_checksum:
        raise ProgressionContractError(
            "source_snapshot does not match the supplied SeasonSnapshot checksum"
        )
    if model_version != MODEL_VERSION:
        raise ProgressionContractError(
            f"unsupported CORS model version {model_version!r}; expected {MODEL_VERSION!r}"
        )
    model_version = _text(model_version, "model_version")
    if dataset_id is None:
        dataset_id = _default_dataset_id(source_snapshot)
    dataset_id = _text(dataset_id, "dataset_id")

    requested_phase = _phase(phase)
    if requested_phase is None:
        requested_phase = "final" if season_complete else "week"
    if target_week is not None:
        if isinstance(target_week, bool):
            raise ProgressionContractError("target_week must be a non-negative integer")
        try:
            target_week = _strict_int(target_week, "target_week")
        except ProgressionContractError as exc:
            raise ProgressionContractError(
                "target_week must be a non-negative integer"
            ) from exc
        if target_week < 0:
            raise ProgressionContractError("target_week must be a non-negative integer")
        if target_week > complete_through:
            raise ProgressionContractError(
                "target_week cannot exceed the snapshot's completed boundary"
            )
    elif requested_phase == "preseason":
        target_week = -1
    else:
        target_week = complete_through
    if requested_phase == "final" and not season_complete:
        raise ProgressionContractError(
            "FINAL phase requires every scheduled snapshot game to be complete"
        )
    if requested_phase == "final" and target_week != scheduled_end:
        raise ProgressionContractError(
            "FINAL phase target_week must equal the complete scheduled season boundary"
        )
    if requested_phase == "preseason" and target_week != -1:
        raise ProgressionContractError("PRESEASON phase cannot have a numbered target_week")

    normalized_rankings: dict[str, Any] = {}
    if rankings is not None:
        if not isinstance(rankings, Mapping):
            raise ProgressionContractError("rankings must be keyed by checkpoint")
        for raw_key, value in rankings.items():
            key = _normalize_checkpoint_key(raw_key)
            if key in normalized_rankings:
                raise ProgressionContractError(f"duplicate ranking checkpoint {key}")
            normalized_rankings[key] = value

    if preseason_rows is not None:
        if PRESEASON in normalized_rankings:
            raise ProgressionContractError(
                "PRESEASON was supplied both in rankings and preseason_rows"
            )
        normalized_rankings[PRESEASON] = preseason_rows
    if final_rows is not None:
        if FINAL in normalized_rankings:
            raise ProgressionContractError("FINAL was supplied both in rankings and final_rows")
        normalized_rankings[FINAL] = final_rows

    metadata_by_key: dict[str, Mapping[str, Any]] = {}
    if ranking_metadata is not None:
        if not isinstance(ranking_metadata, Mapping):
            raise ProgressionContractError("ranking_metadata must be keyed by checkpoint")
        for raw_key, value in ranking_metadata.items():
            key = _normalize_checkpoint_key(raw_key)
            if key in metadata_by_key:
                raise ProgressionContractError(f"duplicate ranking metadata checkpoint {key}")
            if not isinstance(value, Mapping):
                raise ProgressionContractError(f"ranking metadata for {key} must be an object")
            metadata_by_key[key] = value

    allowed_last_week = -1 if requested_phase == "preseason" else target_week
    for key in normalized_rankings:
        if key == PRESEASON:
            continue
        if key == FINAL:
            if requested_phase in {"preseason", "week"}:
                raise ProgressionContractError(
                    f"{key} rows are not eligible during {requested_phase} phase"
                )
            if not season_complete:
                raise ProgressionContractError("FINAL rows require a complete snapshot")
            continue
        week = int(key[1:])
        if week > allowed_last_week:
            raise ProgressionContractError(
                f"{key} rows are beyond the validated {requested_phase} checkpoint"
            )
        if week > complete_through:
            raise ProgressionContractError(
                f"{key} rows are beyond the snapshot completed boundary"
            )
        if week > scheduled_end:
            raise ProgressionContractError(f"{key} is outside the snapshot schedule")

    values_by_key: dict[str, dict[str, tuple[float, int]]] = {}
    unavailable_input: dict[str, str] = {}
    for key, value in normalized_rankings.items():
        rows, wrapper_metadata, unavailable_reason = _unwrap_checkpoint_value(
            value, label=key
        )
        if key in metadata_by_key:
            _validate_common_identity(
                metadata_by_key[key],
                label=f"{key} metadata",
                sport=sport,
                classification=classification,
                season=season,
                dataset_id=dataset_id,
                model_version=model_version,
                source_snapshot=source_snapshot,
            )
        if wrapper_metadata:
            _validate_common_identity(
                wrapper_metadata,
                label=f"{key} metadata",
                sport=sport,
                classification=classification,
                season=season,
                dataset_id=dataset_id,
                model_version=model_version,
                source_snapshot=source_snapshot,
            )
        if unavailable_reason is not None:
            unavailable_input[key] = unavailable_reason
            continue
        assert rows is not None
        values_by_key[key] = _validate_rows(
            rows,
            label=key,
            roster=roster,
            sport=sport,
            classification=classification,
            season=season,
            dataset_id=dataset_id,
            model_version=model_version,
            source_snapshot=source_snapshot,
        )

    # A supplied metadata record must refer to an actual input checkpoint.  A
    # typo here must not silently create an identity claim for an unavailable
    # column.
    unknown_metadata = sorted(set(metadata_by_key) - set(normalized_rankings))
    if unknown_metadata:
        raise ProgressionContractError(
            f"ranking metadata has no corresponding checkpoint input: {unknown_metadata}"
        )

    checkpoint_keys: list[str] = [PRESEASON]
    checkpoint_keys.extend(f"W{week}" for week in range(max(0, scheduled_end + 1)))
    checkpoint_keys.append(FINAL)

    def checkpoint_reason(key: str) -> str | None:
        if key in unavailable_input:
            return unavailable_input[key]
        if key == PRESEASON:
            return "ranking_missing"
        if key == FINAL:
            if requested_phase in {"preseason", "week"}:
                return "future_checkpoint"
            if not season_complete:
                return "season_incomplete"
            return "ranking_missing"
        week = int(key[1:])
        if week > allowed_last_week:
            return "future_checkpoint"
        if week > complete_through:
            return "season_incomplete"
        return "ranking_missing"

    checkpoints: list[ProgressionCheckpoint] = []
    for key in checkpoint_keys:
        available = key in values_by_key
        reason = None if available else checkpoint_reason(key)
        checkpoints.append(
            ProgressionCheckpoint(
                key=key,
                kind=("preseason" if key == PRESEASON else "final" if key == FINAL else "week"),
                week=None if key in {PRESEASON, FINAL} else int(key[1:]),
                available=available,
                reason=reason,
                source_snapshot=source_snapshot,
                dataset_id=dataset_id,
                model_version=model_version,
            )
        )

    teams: list[ProgressionTeam] = []
    for school in sorted(roster, key=lambda value: (value.casefold(), value)):
        cells: dict[str, ProgressionCell] = {}
        for checkpoint in checkpoints:
            values = values_by_key.get(checkpoint.key)
            if values is None:
                cells[checkpoint.key] = ProgressionCell(
                    available=False, reason=checkpoint.reason
                )
            else:
                points, rank = values[school]
                cells[checkpoint.key] = ProgressionCell(
                    available=True, points=points, rank=rank
                )
        teams.append(
            ProgressionTeam(school=school, conference=roster[school], cells=cells)
        )

    if carryover_identity is None:
        carryover_identity = _default_carryover_identity(values_by_key)
    else:
        carryover_identity = _text(carryover_identity, "carryover_identity")

    return ProgressionDocument(
        sport=sport,
        classification=classification,
        season=season,
        dataset_id=dataset_id,
        model_version=model_version,
        source_snapshot=source_snapshot,
        carryover_identity=carryover_identity,
        phase=requested_phase,
        target_week=target_week,
        checkpoints=tuple(checkpoints),
        teams=tuple(teams),
    )


def progression_json(document: ProgressionDocument) -> str:
    """Serialize only safe derived rows and provenance as canonical JSON."""

    if not isinstance(document, ProgressionDocument):
        raise ProgressionContractError("progression_json requires a ProgressionDocument")
    return json.dumps(
        document.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def _default_links(document: ProgressionDocument) -> list[tuple[str, str]]:
    base = f"../rankings/{document.season}_"
    links: list[tuple[str, str]] = [
        ("Season", f"../{document.season}_CFB.html"),
        ("Progression data", f"{document.season}_{document.classification}_progression.json"),
    ]
    for checkpoint in document.checkpoints:
        if not checkpoint.available:
            continue
        if checkpoint.key == PRESEASON:
            suffix = f"PRESEASON_{document.classification}_cors.html"
        elif checkpoint.key == FINAL:
            suffix = f"FINAL_{document.classification}_cors.html"
        else:
            suffix = f"{checkpoint.key}_{document.classification}_cors.html"
        links.append((f"{checkpoint.key} ranking", base + suffix))
    return links


def _links(value: Mapping[str, str] | Sequence[tuple[str, str]] | None, document: ProgressionDocument) -> tuple[tuple[str, str], ...]:
    if value is None:
        pairs = _default_links(document)
    elif isinstance(value, Mapping):
        pairs = list(value.items())
    else:
        pairs = list(value)
    normalized: list[tuple[str, str]] = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, Sequence) or isinstance(pair, (str, bytes)) or len(pair) != 2:
            raise ProgressionContractError(f"link {index} must be a (label, href) pair")
        label = _text(pair[0], f"link {index} label")
        href = _text(pair[1], f"link {index} href")
        lowered = href.lower()
        if lowered.startswith(("javascript:", "data:", "mailto:", "http:", "https:")):
            raise ProgressionContractError(f"link {index} must be an internal relative URL")
        if href.startswith("/") or "\\" in href:
            raise ProgressionContractError(f"link {index} must be an internal relative URL")
        normalized.append((label, href))
    return tuple(normalized)


def _display_number(value: float) -> str:
    # Engine rows are already rounded according to CORS semantics.  ``repr``
    # avoids a second display-rounding policy while staying deterministic.
    return repr(value)


def render_progression_html(
    document: ProgressionDocument,
    links: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    *,
    title: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Render a complete, JavaScript-free static progression page."""

    if not isinstance(document, ProgressionDocument):
        raise ProgressionContractError(
            "render_progression_html requires a ProgressionDocument"
        )
    if title is None:
        title = (
            f"{document.season} CORS ranking progression — "
            f"{document.classification}"
        )
    title = _text(title, "page title")
    if timestamp is not None:
        timestamp = _text(timestamp, "timestamp")
    link_pairs = _links(links, document)
    nav = "\n".join(
        f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'
        for label, href in link_pairs
    )
    header_cells = ["<th scope=\"col\">Team</th>", "<th scope=\"col\">Conference</th>"]
    for checkpoint in document.checkpoints:
        header_cells.extend(
            (
                f'<th scope="col">{html.escape(checkpoint.key)} Points</th>',
                f'<th scope="col">{html.escape(checkpoint.key)} National rank</th>',
            )
        )
    body_rows: list[str] = []
    for team in document.teams:
        cells = [
            f'<th scope="row">{html.escape(team.school)}</th>',
            f'<td>{html.escape(team.conference)}</td>',
        ]
        for checkpoint in document.checkpoints:
            cell = team.cells[checkpoint.key]
            if cell.available:
                cells.extend(
                    (
                        f'<td class="points">{html.escape(_display_number(cell.points))}</td>',
                        f'<td class="rank">{cell.rank}</td>',
                    )
                )
            else:
                reason = html.escape(str(cell.reason), quote=True)
                cells.extend(
                    (
                        f'<td class="unavailable" aria-label="unavailable: {reason}">unavailable</td>',
                        '<td class="unavailable">&mdash;</td>',
                    )
                )
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    provenance = document.provenance
    timestamp_html = (
        f"<p>Last updated: {html.escape(timestamp)}</p>\n"
        if timestamp is not None
        else ""
    )
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        f"<title>{html.escape(title)}</title>\n"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem}"
        "table{border-collapse:collapse;font-variant-numeric:tabular-nums}"
        "th,td{border:1px solid #bbb;padding:.35rem .5rem;text-align:right}"
        "th:first-child,td:first-child,td:nth-child(2){text-align:left}"
        ".unavailable{color:#666;font-style:italic}</style>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        f"<nav>{nav}</nav>\n"
        "<p>CORS points and national rank are shown at each checkpoint.</p>\n"
        f"<p>Phase: {html.escape(document.phase)}; target: {document.target_week}.</p>\n"
        + timestamp_html
        + f"<p class=\"provenance\">Season {provenance['season']} · "
        f"Dataset {html.escape(str(provenance['dataset_id']))} · "
        f"Model {html.escape(str(provenance['model_version']))} · "
        f"Source snapshot {html.escape(str(provenance['source_snapshot']))}</p>\n"
        "<table>\n<thead><tr>"
        + "".join(header_cells)
        + "</tr></thead>\n<tbody>"
        + "".join(body_rows)
        + "</tbody>\n</table>\n"
        "</body>\n</html>\n"
    )


def build_progression_outputs(
    snapshot: SeasonSnapshot,
    rankings: Mapping[Any, Any] | None = None,
    preseason_rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    final_rows: Sequence[Mapping[str, Any]] | None = None,
    phase: str | None = None,
    target_week: int | None = None,
    dataset_id: str | None = None,
    model_version: str = MODEL_VERSION,
    source_snapshot: str | None = None,
    carryover_identity: str | None = None,
    ranking_metadata: Mapping[Any, Mapping[str, Any]] | None = None,
    links: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    title: str | None = None,
    timestamp: str | None = None,
) -> tuple[ProgressionDocument, str, str]:
    """Build a document and its pure HTML/JSON representations."""

    document = build_progression(
        snapshot,
        rankings,
        preseason_rows,
        final_rows=final_rows,
        phase=phase,
        target_week=target_week,
        dataset_id=dataset_id,
        model_version=model_version,
        source_snapshot=source_snapshot,
        carryover_identity=carryover_identity,
        ranking_metadata=ranking_metadata,
    )
    return (
        document,
        render_progression_html(document, links, title=title, timestamp=timestamp),
        progression_json(document),
    )


__all__ = [
    "FINAL",
    "MODEL_VERSION",
    "PRESEASON",
    "ProgressionCell",
    "ProgressionCheckpoint",
    "ProgressionContractError",
    "ProgressionDocument",
    "ProgressionTeam",
    "build_progression",
    "build_progression_outputs",
    "progression_json",
    "render_progression_html",
]
