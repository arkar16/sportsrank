"""Pure conference records, comparisons and qualification derivation.

The public seam here is :func:`derive_conference_reference`.  It consumes a
validated :class:`~cfb.season_snapshot.SeasonSnapshot`, a snapshot-bound
conference evidence supplement, and one already calculated ranking checkpoint.
It returns value objects for a later static adapter.  It does not render HTML,
serialize a forecast candidate, calculate a natural matchup margin, or call a
provider.

The implementation treats conference membership and conference-game status as
different facts.  Membership groups teams for descriptive views; only an
explicit per-game designation can place a game in a qualification record.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
import re
from statistics import median
from typing import Any

try:
    from .conference_sources import (
        ChampionshipRule,
        ConferenceGameDesignation,
        ConferenceMember,
        ConferenceRule,
        ConferenceSourceError,
        ConferenceSupplement,
        ValidatedConferenceSupplement,
        snapshot_content_checksum,
        validate_conference_supplement,
    )
    from .season_source import SourceGame, is_completed, is_explicit_non_played
except ImportError:  # pragma: no cover - supports direct execution from cfb/
    from conference_sources import (
        ChampionshipRule,
        ConferenceGameDesignation,
        ConferenceMember,
        ConferenceRule,
        ConferenceSourceError,
        ConferenceSupplement,
        ValidatedConferenceSupplement,
        snapshot_content_checksum,
        validate_conference_supplement,
    )
    from season_source import SourceGame, is_completed, is_explicit_non_played


_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f<>]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ConferenceReferenceError(ValueError):
    """Raised when a checkpoint cannot satisfy the conference contract."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ConferenceReferenceError(f"{field} must be text")
    value = value.strip()
    if not value or _SAFE_TEXT.fullmatch(value) is None:
        raise ConferenceReferenceError(f"{field} contains unsafe or empty text")
    return value


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ConferenceReferenceError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise ConferenceReferenceError(f"{field} must be an integer")


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ConferenceReferenceError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConferenceReferenceError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ConferenceReferenceError(f"{field} must be a finite number")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConferenceReferenceError(f"{field} must be an ISO timestamp") from exc
    else:
        raise ConferenceReferenceError(f"{field} must be an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConferenceReferenceError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field)


def _game_start_bounds(game: SourceGame) -> tuple[datetime, datetime] | None:
    """Bound start timing without assigning a timezone to a calendar-only date."""

    raw = getattr(game, "date", None)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        instant = _timestamp(raw, "game date")
        return instant, instant
    if not isinstance(raw, str):
        raise ConferenceReferenceError("game date must be an ISO date or timestamp")
    text = raw.strip()
    if not text:
        raise ConferenceReferenceError("game date must be an ISO date or timestamp")
    if "T" in text or " " in text:
        instant = _timestamp(text, "game date")
        return instant, instant
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ConferenceReferenceError("game date must be an ISO date or timestamp") from exc
    midnight = datetime.combine(parsed, time.min, tzinfo=timezone.utc)
    # A local calendar date without timezone can span UTC+14 through UTC-12.
    # Keep that uncertainty; do not manufacture a midnight-UTC kickoff.
    return midnight - timedelta(hours=14), midnight + timedelta(days=1, hours=12)


def _reject_games_at_or_after_cutoff(
    selected_games: Sequence[SourceGame], cutoff: datetime | None
) -> None:
    if cutoff is None:
        return
    for game in selected_games:
        bounds = _game_start_bounds(game)
        if bounds is not None and bounds[0] >= cutoff:
            raise ConferenceReferenceError(
                f"checkpoint includes game {getattr(game, 'provider_id', None)!r} "
                "at or after the cutoff"
            )


def _checkpoint(phase: Any, target_week: Any) -> tuple[str, str, int | None]:
    if not isinstance(phase, str):
        raise ConferenceReferenceError("phase must be preseason, week, or final")
    phase = phase.strip().lower()
    if phase not in {"preseason", "week", "final"}:
        raise ConferenceReferenceError("phase must be preseason, week, or final")
    if phase == "preseason":
        if target_week is not None and _strict_int(target_week, "target_week") != -1:
            raise ConferenceReferenceError("PRESEASON cannot carry a numbered target_week")
        return "PRESEASON", phase, None
    if target_week is None:
        raise ConferenceReferenceError("numbered and FINAL checkpoints require target_week")
    target = _strict_int(target_week, "target_week")
    if target < 0:
        raise ConferenceReferenceError("target_week must be non-negative")
    return ("FINAL" if phase == "final" else f"W{target}"), phase, target


@dataclass(frozen=True)
class CheckpointRanking:
    """One already validated CORS value at the viewed checkpoint."""

    school: str
    cors: float | None
    national_rank: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "school", _text(self.school, "ranking school"))
        if self.cors is None or self.national_rank is None:
            if self.cors is not None or self.national_rank is not None:
                raise ConferenceReferenceError(
                    "missing ranking values must omit both cors and national_rank"
                )
        else:
            object.__setattr__(self, "cors", _finite(self.cors, "ranking cors"))
            rank = _strict_int(self.national_rank, "ranking national_rank")
            if rank < 1:
                raise ConferenceReferenceError("ranking national_rank must be positive")
            object.__setattr__(self, "national_rank", rank)


def ranking_rows_from_engine(rows: Sequence[Mapping[str, Any]]) -> tuple[CheckpointRanking, ...]:
    """Adapt exact ranking-engine row keys to the typed conference seam.

    This is intentionally an explicit adapter rather than a permissive field
    alias layer.  Rows must expose ``school``, ``cors`` and ``rank`` exactly.
    """

    result: list[CheckpointRanking] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ConferenceReferenceError(f"ranking row {index} is not an object")
        required = {"school", "cors", "rank"}
        if set(row) - required or not required.issubset(row):
            raise ConferenceReferenceError(
                f"ranking row {index} must contain exactly school, cors and rank"
            )
        result.append(
            CheckpointRanking(
                school=row["school"],
                cors=row["cors"],
                national_rank=row["rank"],
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class Record:
    wins: int = 0
    losses: int = 0
    ties: int = 0

    def __post_init__(self) -> None:
        for field in ("wins", "losses", "ties"):
            value = _strict_int(getattr(self, field), field)
            if value < 0:
                raise ConferenceReferenceError(f"{field} cannot be negative")
            object.__setattr__(self, field, value)

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def winning_percentage(self) -> float | None:
        if not self.games:
            return None
        return (self.wins + (self.ties * 0.5)) / self.games

    @property
    def win_pct(self) -> float | None:
        return self.winning_percentage

    def to_dict(self) -> dict[str, Any]:
        return {
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "games": self.games,
            "winning_percentage": self.winning_percentage,
        }


@dataclass(frozen=True)
class StandingsRow:
    school: str
    conference: str
    conference_record: Record
    overall_record: Record
    cors: float | None
    national_rank: int | None
    position: int | None
    display_order: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "school", _text(self.school, "standings school"))
        object.__setattr__(self, "conference", _text(self.conference, "standings conference"))
        if not isinstance(self.conference_record, Record) or not isinstance(self.overall_record, Record):
            raise ConferenceReferenceError("standings records must be Record values")
        if self.cors is not None:
            object.__setattr__(self, "cors", _finite(self.cors, "standings cors"))
        if self.national_rank is not None:
            rank = _strict_int(self.national_rank, "standings national_rank")
            if rank < 1:
                raise ConferenceReferenceError("standings national_rank must be positive")
            object.__setattr__(self, "national_rank", rank)
        if self.position is not None:
            position = _strict_int(self.position, "standings position")
            if position < 1:
                raise ConferenceReferenceError("standings position must be positive")
            object.__setattr__(self, "position", position)
        display_order = _strict_int(self.display_order, "display_order")
        if display_order < 1:
            raise ConferenceReferenceError("display_order must be positive")
        object.__setattr__(self, "display_order", display_order)

    def to_dict(self) -> dict[str, Any]:
        return {
            # Public derived vocabulary must not resemble a provider Team row.
            "team": self.school,
            "conference": self.conference,
            "conference_record": self.conference_record.to_dict(),
            "overall_record": self.overall_record.to_dict(),
            "cors": self.cors,
            "national_rank": self.national_rank,
            "position": self.position,
            "display_order": self.display_order,
        }


@dataclass(frozen=True)
class ConferenceStandings:
    conference: str
    rows: tuple[StandingsRow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "conference", _text(self.conference, "conference"))
        if not isinstance(self.rows, tuple):
            object.__setattr__(self, "rows", tuple(self.rows))
        schools = [row.school for row in self.rows]
        if len(schools) != len(set(schools)):
            raise ConferenceReferenceError("conference standings contain duplicate schools")

    def to_dict(self) -> dict[str, Any]:
        return {"conference": self.conference, "rows": [row.to_dict() for row in self.rows]}


@dataclass(frozen=True)
class RatingComparison:
    conference: str
    available: bool
    reason: str | None
    member_count: int
    mean: float | None
    median: float | None
    top_count: int | None
    top_mean: float | None
    remainder_count: int | None
    remainder_mean: float | None
    top_gap: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "conference", _text(self.conference, "comparison conference"))
        count = _strict_int(self.member_count, "comparison member_count")
        if count < 0:
            raise ConferenceReferenceError("comparison member_count cannot be negative")
        object.__setattr__(self, "member_count", count)
        if self.available:
            if self.reason is not None or any(
                value is None
                for value in (
                    self.mean,
                    self.median,
                    self.top_count,
                    self.top_mean,
                    self.remainder_count,
                    self.remainder_mean,
                    self.top_gap,
                )
            ):
                raise ConferenceReferenceError("available comparisons require every statistic")
        elif self.reason is None:
            raise ConferenceReferenceError("unavailable comparisons require a reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "conference": self.conference,
            "available": self.available,
            "reason": self.reason,
            "member_count": self.member_count,
            "mean": self.mean,
            "median": self.median,
            "top_count": self.top_count,
            "top_mean": self.top_mean,
            "remainder_count": self.remainder_count,
            "remainder_mean": self.remainder_mean,
            "top_gap": self.top_gap,
        }


@dataclass(frozen=True)
class InterconferenceRecord:
    conference: str
    opponent: str
    opponent_kind: str
    regular: Record
    postseason: Record
    combined: Record
    unknown: Record = Record()

    def __post_init__(self) -> None:
        object.__setattr__(self, "conference", _text(self.conference, "interconference conference"))
        object.__setattr__(self, "opponent", _text(self.opponent, "interconference opponent"))
        kind = _text(self.opponent_kind, "interconference opponent_kind").lower()
        if kind not in {"conference", "independent", "fcs"}:
            raise ConferenceReferenceError("unsupported interconference opponent kind")
        object.__setattr__(self, "opponent_kind", kind)
        if not all(isinstance(value, Record) for value in (self.regular, self.postseason, self.combined, self.unknown)):
            raise ConferenceReferenceError("interconference records must be Record values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "conference": self.conference,
            "opponent": self.opponent,
            "opponent_kind": self.opponent_kind,
            "regular": self.regular.to_dict(),
            "postseason": self.postseason.to_dict(),
            "combined": self.combined.to_dict(),
            "unknown": self.unknown.to_dict(),
        }


@dataclass(frozen=True)
class ChampionshipProjection:
    conference: str
    status: str
    participants: tuple[str, ...]
    contenders: tuple[str, ...]
    selection_basis: str | None
    site_state: str
    host_team: str | None
    neutral_site: bool | None
    confirmation_id: str | None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "conference", _text(self.conference, "projection conference"))
        status = _text(self.status, "projection status").lower()
        if status not in {"projected", "confirmed", "unresolved", "unavailable", "not_applicable"}:
            raise ConferenceReferenceError("unsupported projection status")
        object.__setattr__(self, "status", status)
        if not isinstance(self.participants, tuple):
            object.__setattr__(self, "participants", tuple(self.participants))
        if not isinstance(self.contenders, tuple):
            object.__setattr__(self, "contenders", tuple(self.contenders))
        for field in ("participants", "contenders"):
            values = tuple(_text(value, f"projection {field} item") for value in getattr(self, field))
            if len(set(values)) != len(values):
                raise ConferenceReferenceError(f"projection {field} contain duplicates")
            object.__setattr__(self, field, values)
        if status in {"projected", "confirmed"} and len(self.participants) != 2:
            raise ConferenceReferenceError("available projection requires two participants")
        if status not in {"projected", "confirmed"} and self.participants:
            raise ConferenceReferenceError("unavailable projection cannot name participants")
        if self.site_state not in {"neutral", "hosted", "unresolved", "unavailable", "not_applicable"}:
            raise ConferenceReferenceError("unsupported projection site_state")
        if self.host_team is not None:
            object.__setattr__(self, "host_team", _text(self.host_team, "projection host_team"))
        if self.neutral_site is not None and not isinstance(self.neutral_site, bool):
            raise ConferenceReferenceError("projection neutral_site must be boolean or None")
        if status == "not_applicable" and self.site_state != "not_applicable":
            raise ConferenceReferenceError("not_applicable projection requires not_applicable site")
        reasons = {
            "missing_cutoff", "rule_evidence_unavailable",
            "membership_evidence_unavailable", "game_designation_evidence_unavailable",
            "selection_rule_unavailable", "eligibility_evidence_unavailable",
            "no_qualifying_results", "unresolved_qualification", "unresolved_site",
            "no_championship",
            "ambiguous_game_timing",
        }
        if self.reason is not None and self.reason not in reasons:
            raise ConferenceReferenceError("unsupported projection reason")
        if status in {"unavailable", "unresolved", "not_applicable"} and self.reason is None:
            raise ConferenceReferenceError("unavailable projection requires an explicit reason")

    @property
    def pairing(self) -> tuple[str, str] | None:
        return self.participants if len(self.participants) == 2 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conference": self.conference,
            "status": self.status,
            "participants": list(self.participants),
            "contenders": list(self.contenders),
            "selection_basis": self.selection_basis,
            "site_state": self.site_state,
            "host_team": self.host_team,
            "neutral_site": self.neutral_site,
            "confirmation_id": self.confirmation_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ConferenceReference:
    season: int
    snapshot_checksum: str
    supplement_checksum: str
    content_identity: str
    dataset_id: str
    model_version: str
    checkpoint: str
    phase: str
    target_week: int | None
    cutoff: datetime | None
    standings: tuple[ConferenceStandings, ...]
    independent_rows: tuple[StandingsRow, ...]
    comparisons: tuple[RatingComparison, ...]
    interconference: tuple[InterconferenceRecord, ...]
    projections: tuple[ChampionshipProjection, ...]

    def __post_init__(self) -> None:
        season = _strict_int(self.season, "reference season")
        if season < 1900:
            raise ConferenceReferenceError("reference season is outside supported range")
        object.__setattr__(self, "season", season)
        for field in ("snapshot_checksum", "supplement_checksum"):
            value = _text(getattr(self, field), field).lower()
            if _SHA256.fullmatch(value) is None:
                raise ConferenceReferenceError(f"{field} must be a SHA-256 digest")
            object.__setattr__(self, field, value)
        object.__setattr__(self, "content_identity", _text(self.content_identity, "content_identity"))
        object.__setattr__(self, "dataset_id", _text(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "model_version", _text(self.model_version, "model_version"))
        object.__setattr__(self, "checkpoint", _text(self.checkpoint, "checkpoint"))
        object.__setattr__(self, "phase", _text(self.phase, "phase").lower())
        if self.phase not in {"preseason", "week", "final"}:
            raise ConferenceReferenceError("unsupported reference phase")
        if self.target_week is not None:
            object.__setattr__(self, "target_week", _strict_int(self.target_week, "target_week"))
        if self.cutoff is not None:
            object.__setattr__(self, "cutoff", _timestamp(self.cutoff, "cutoff"))
        for field, cls in (
            ("standings", ConferenceStandings),
            ("comparisons", RatingComparison),
            ("interconference", InterconferenceRecord),
            ("projections", ChampionshipProjection),
        ):
            value = getattr(self, field)
            if not isinstance(value, tuple):
                value = tuple(value)
                object.__setattr__(self, field, value)
            if any(not isinstance(item, cls) for item in value):
                raise ConferenceReferenceError(f"reference {field} contain unexpected values")
        if not isinstance(self.independent_rows, tuple):
            object.__setattr__(self, "independent_rows", tuple(self.independent_rows))
        if any(not isinstance(row, StandingsRow) for row in self.independent_rows):
            raise ConferenceReferenceError("reference independent_rows contain unexpected values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "snapshot_checksum": self.snapshot_checksum,
            "supplement_checksum": self.supplement_checksum,
            "content_identity": self.content_identity,
            "dataset_id": self.dataset_id,
            "model_version": self.model_version,
            "checkpoint": self.checkpoint,
            "phase": self.phase,
            "target_week": self.target_week,
            "cutoff": self.cutoff.isoformat() if self.cutoff is not None else None,
            "standings": [item.to_dict() for item in self.standings],
            "independent_rows": [item.to_dict() for item in self.independent_rows],
            "comparisons": [item.to_dict() for item in self.comparisons],
            "interconference": [item.to_dict() for item in self.interconference],
            "projections": [item.to_dict() for item in self.projections],
        }


class _MutableRecord:
    __slots__ = ("wins", "losses", "ties")

    def __init__(self) -> None:
        self.wins = self.losses = self.ties = 0

    def add(self, own: int, other: int) -> None:
        if own > other:
            self.wins += 1
        elif own < other:
            self.losses += 1
        else:
            self.ties += 1

    def frozen(self) -> Record:
        return Record(self.wins, self.losses, self.ties)


def _record_map() -> defaultdict[str, _MutableRecord]:
    return defaultdict(_MutableRecord)


def _game_phase(game: SourceGame, designation: ConferenceGameDesignation) -> str:
    raw = getattr(game, "phase", None)
    if isinstance(raw, str) and raw.strip().lower() in {"regular", "postseason"}:
        return raw.strip().lower()
    # A source-qualified title marker is sufficient to keep title games out of
    # qualification records even in retained snapshots whose phase is null.
    if designation.title_game:
        return "postseason"
    # ADR0014 retains unknown chronology. Count the completed result in its
    # own scope and combined totals without inventing a regular-season phase.
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in {"", "unknown"}):
        return "unknown"
    raise ConferenceReferenceError("game has an unsupported phase")


def _validate_checkpoint_games(
    snapshot: Any,
    phase: str,
    target_week: int | None,
) -> tuple[tuple[SourceGame, ...], dict[str, ConferenceGameDesignation]]:
    if phase == "preseason":
        return (), {}
    games = tuple(getattr(snapshot, "games", ()))
    active = tuple(game for game in games if not is_explicit_non_played(game))
    if target_week is None:
        raise ConferenceReferenceError("checkpoint target_week is required")
    if phase == "week":
        boundary = getattr(snapshot, "complete_through_week", -1)
        if target_week > _strict_int(boundary, "snapshot complete_through_week"):
            raise ConferenceReferenceError("checkpoint is beyond the completed snapshot boundary")
    else:
        scheduled_weeks = [
            _strict_int(getattr(game, "week", None), "game week") for game in active
        ]
        if not scheduled_weeks:
            raise ConferenceReferenceError("FINAL requires a scheduled season")
        if target_week != max(scheduled_weeks):
            raise ConferenceReferenceError("FINAL target_week must equal the season boundary")
    selected: list[SourceGame] = []
    for game in active:
        week = _strict_int(getattr(game, "week", None), "game week")
        if week > target_week:
            continue
        if not is_completed(game):
            raise ConferenceReferenceError(
                f"checkpoint includes incomplete game {getattr(game, 'provider_id', None)!r}"
            )
        selected.append(game)
    if phase == "final" and len(selected) != len(active):
        raise ConferenceReferenceError("FINAL requires every scheduled game to be complete")
    return tuple(selected), {}


def _ranking_index(
    rows: Sequence[CheckpointRanking],
    snapshot: Any,
) -> dict[str, CheckpointRanking]:
    if isinstance(rows, (str, bytes)):
        raise ConferenceReferenceError("rankings must be a sequence of CheckpointRanking values")
    values = tuple(rows)
    if any(not isinstance(row, CheckpointRanking) for row in values):
        raise ConferenceReferenceError("rankings must use CheckpointRanking values")
    result: dict[str, CheckpointRanking] = {}
    for row in values:
        if row.school in result:
            raise ConferenceReferenceError(f"duplicate checkpoint ranking for {row.school!r}")
        result[row.school] = row
    expected = {
        getattr(team, "school", None)
        for team in tuple(getattr(snapshot, "teams", ()))
    }
    if None in expected or any(not isinstance(name, str) for name in expected):
        raise ConferenceReferenceError("snapshot contains an invalid team identity")
    if set(result) != expected:
        raise ConferenceReferenceError(
            f"checkpoint rankings do not match snapshot (missing={sorted(expected - set(result))}, "
            f"extra={sorted(set(result) - expected)})"
        )
    return result


def _member_maps(
    supplement: ValidatedConferenceSupplement,
) -> tuple[dict[str, ConferenceMember], dict[str, ConferenceGameDesignation], dict[str, ConferenceRule]]:
    source = supplement.supplement
    members = {item.team: item for item in source.members}
    games = {item.provider_id: item for item in source.games}
    rules = {item.conference: item for item in source.rules}
    return members, games, rules


def _add_game_records(
    selected_games: Sequence[SourceGame],
    designations: Mapping[str, ConferenceGameDesignation],
    members: Mapping[str, ConferenceMember],
) -> tuple[dict[str, Record], dict[str, Record], dict[tuple[str, str, str], dict[str, _MutableRecord]]]:
    overall = _record_map()
    qualification = _record_map()
    inter: dict[tuple[str, str, str], dict[str, _MutableRecord]] = {}
    for game in selected_games:
        provider_id = getattr(game, "provider_id", None)
        designation = designations.get(provider_id)
        if designation is None:
            raise ConferenceReferenceError(f"selected game {provider_id!r} lacks a designation")
        home = getattr(game, "home_team", None)
        away = getattr(game, "away_team", None)
        if (home, away) != (designation.home_team, designation.away_team):
            raise ConferenceReferenceError(f"game {provider_id!r} identity changed after validation")
        home_points = getattr(game, "home_points", None)
        away_points = getattr(game, "away_points", None)
        if not isinstance(home_points, int) or isinstance(home_points, bool):
            raise ConferenceReferenceError(f"game {provider_id!r} has an invalid home score")
        if not isinstance(away_points, int) or isinstance(away_points, bool):
            raise ConferenceReferenceError(f"game {provider_id!r} has an invalid away score")
        if home in members:
            overall[home].add(home_points, away_points)
        if away in members:
            overall[away].add(away_points, home_points)
        if (
            designation.conference_game
            and designation.counts_for_standings
            and not designation.title_game
        ):
            qualification[home].add(home_points, away_points)
            qualification[away].add(away_points, home_points)

        home_member = members.get(home)
        away_member = members.get(away)
        home_conf = home_member.conference if home_member and not home_member.independent else None
        away_conf = away_member.conference if away_member and not away_member.independent else None
        home_class = str(getattr(game, "home_classification", "")).upper()
        away_class = str(getattr(game, "away_classification", "")).upper()
        if home_conf and away_conf and home_conf != away_conf:
            pairs = (
                (home_conf, away_conf, home_points, away_points),
                (away_conf, home_conf, away_points, home_points),
            )
            opponent_kind = "conference"
            for conference, opponent, own, other in pairs:
                phase = _game_phase(game, designation)
                bucket = inter.setdefault((conference, opponent, opponent_kind), {"regular": _MutableRecord(), "postseason": _MutableRecord(), "unknown": _MutableRecord()})
                bucket[phase].add(own, other)
        elif home_conf and away_member and away_member.independent:
            phase = _game_phase(game, designation)
            bucket = inter.setdefault((home_conf, "FBS Independents", "independent"), {"regular": _MutableRecord(), "postseason": _MutableRecord(), "unknown": _MutableRecord()})
            bucket[phase].add(home_points, away_points)
        elif away_conf and home_member and home_member.independent:
            phase = _game_phase(game, designation)
            bucket = inter.setdefault((away_conf, "FBS Independents", "independent"), {"regular": _MutableRecord(), "postseason": _MutableRecord(), "unknown": _MutableRecord()})
            bucket[phase].add(away_points, home_points)
        elif home_conf and not away_member and away_class == "FCS":
            phase = _game_phase(game, designation)
            bucket = inter.setdefault((home_conf, "FCS", "fcs"), {"regular": _MutableRecord(), "postseason": _MutableRecord(), "unknown": _MutableRecord()})
            bucket[phase].add(home_points, away_points)
        elif away_conf and not home_member and home_class == "FCS":
            phase = _game_phase(game, designation)
            bucket = inter.setdefault((away_conf, "FCS", "fcs"), {"regular": _MutableRecord(), "postseason": _MutableRecord(), "unknown": _MutableRecord()})
            bucket[phase].add(away_points, home_points)
    frozen_overall = {team: record.frozen() for team, record in overall.items()}
    frozen_qualification = {team: record.frozen() for team, record in qualification.items()}
    return frozen_overall, frozen_qualification, inter


def _combined(regular: Record, postseason: Record, unknown: Record) -> Record:
    return Record(
        wins=regular.wins + postseason.wins + unknown.wins,
        losses=regular.losses + postseason.losses + unknown.losses,
        ties=regular.ties + postseason.ties + unknown.ties,
    )


def _standings_for(
    conference: str,
    members: Mapping[str, ConferenceMember],
    overall: Mapping[str, Record],
    qualification: Mapping[str, Record],
    rankings: Mapping[str, CheckpointRanking],
) -> ConferenceStandings:
    rows: list[StandingsRow] = []
    team_names = sorted(
        team for team, member in members.items() if member.conference == conference
    )
    pct_by_team = {
        team: qualification.get(team, Record()).winning_percentage for team in team_names
    }
    for team in team_names:
        pct = pct_by_team[team]
        if pct is None:
            position = None
        else:
            position = 1 + sum(
                1 for other in team_names if pct_by_team[other] is not None and pct_by_team[other] > pct
            )
        ranking = rankings[team]
        rows.append(
            StandingsRow(
                school=team,
                conference=conference,
                conference_record=qualification.get(team, Record()),
                overall_record=overall.get(team, Record()),
                cors=ranking.cors,
                national_rank=ranking.national_rank,
                position=position,
                display_order=1,
            )
        )
    rows.sort(key=lambda row: (row.position is None, row.position or 10**9, row.school))
    rows = [
        StandingsRow(
            school=row.school,
            conference=row.conference,
            conference_record=row.conference_record,
            overall_record=row.overall_record,
            cors=row.cors,
            national_rank=row.national_rank,
            position=row.position,
            display_order=index,
        )
        for index, row in enumerate(rows, 1)
    ]
    return ConferenceStandings(conference=conference, rows=tuple(rows))


def _standings_independents(
    members: Mapping[str, ConferenceMember],
    overall: Mapping[str, Record],
    rankings: Mapping[str, CheckpointRanking],
) -> tuple[StandingsRow, ...]:
    rows: list[StandingsRow] = []
    for index, team in enumerate(sorted(team for team, member in members.items() if member.independent), 1):
        ranking = rankings[team]
        rows.append(
            StandingsRow(
                school=team,
                conference="FBS Independents",
                conference_record=Record(),
                overall_record=overall.get(team, Record()),
                cors=ranking.cors,
                national_rank=ranking.national_rank,
                position=None,
                display_order=index,
            )
        )
    return tuple(rows)


def _comparison(conference: str, rows: Sequence[StandingsRow]) -> RatingComparison:
    count = len(rows)
    ratings = [row.cors for row in rows]
    if any(value is None for value in ratings):
        return RatingComparison(
            conference=conference,
            available=False,
            reason="missing_rating",
            member_count=count,
            mean=None,
            median=None,
            top_count=None,
            top_mean=None,
            remainder_count=None,
            remainder_mean=None,
            top_gap=None,
        )
    if not ratings:
        return RatingComparison(
            conference=conference,
            available=False,
            reason="no_members",
            member_count=0,
            mean=None,
            median=None,
            top_count=None,
            top_mean=None,
            remainder_count=None,
            remainder_mean=None,
            top_gap=None,
        )
    values = sorted((float(value) for value in ratings if value is not None), reverse=True)
    top_count = math.ceil(len(values) / 4)
    remainder = values[top_count:]
    top_mean = sum(values[:top_count]) / top_count
    remainder_mean = sum(remainder) / len(remainder) if remainder else None
    gap = top_mean - remainder_mean if remainder_mean is not None else None
    if remainder_mean is None or gap is None:
        return RatingComparison(
            conference=conference,
            available=False,
            reason="empty_remainder",
            member_count=len(values),
            mean=sum(values) / len(values),
            median=float(median(values)),
            top_count=top_count,
            top_mean=top_mean,
            remainder_count=0,
            remainder_mean=None,
            top_gap=None,
        )
    return RatingComparison(
        conference=conference,
        available=True,
        reason=None,
        member_count=len(values),
        mean=sum(values) / len(values),
        median=float(median(values)),
        top_count=top_count,
        top_mean=top_mean,
        remainder_count=len(remainder),
        remainder_mean=remainder_mean,
        top_gap=gap,
    )


def _eligible_teams(rule: ConferenceRule) -> dict[str, bool | None]:
    return {row.team: row.eligible for row in rule.championship.eligibility}


def _evidence_available_at_cutoff(
    supplement: ValidatedConferenceSupplement,
    evidence_ids: Sequence[str],
    cutoff: datetime | None,
) -> bool:
    """Require rule evidence to be effective and known by the viewed cutoff."""

    if cutoff is None:
        return False
    evidence = {item.evidence_id: item for item in supplement.supplement.evidence}
    for evidence_id in evidence_ids:
        item = evidence.get(evidence_id)
        if item is None:
            return False
        known_at = item.known_at_bound
        if known_at is None or known_at > cutoff:
            return False
        if item.effective_from > cutoff.date():
            return False
        if item.effective_to is not None and item.effective_to < cutoff.date():
            return False
    return True


def _projection(
    conference: str,
    rule: ConferenceRule,
    standings: ConferenceStandings,
    supplement: ValidatedConferenceSupplement,
    cutoff: datetime | None,
    selected_games: Sequence[SourceGame],
) -> ChampionshipProjection:
    championship = rule.championship
    if cutoff is None:
        return ChampionshipProjection(
            conference=conference,
            status="unavailable",
            participants=(),
            contenders=(),
            selection_basis=None,
            site_state="unavailable",
            host_team=None,
            neutral_site=None,
            confirmation_id=None,
            reason="missing_cutoff",
        )
    required_evidence_ids = set(rule.evidence_ids)
    required_evidence_ids.update(championship.evidence_ids)
    required_evidence_ids.update(championship.site.evidence_ids)
    for eligibility in championship.eligibility:
        required_evidence_ids.update(eligibility.evidence_ids)
    for division in championship.divisions:
        required_evidence_ids.update(division.evidence_ids)
    if not _evidence_available_at_cutoff(
        supplement, tuple(sorted(required_evidence_ids)), cutoff
    ):
        return ChampionshipProjection(
            conference=conference,
            status="unavailable",
            participants=(),
            contenders=(),
            selection_basis=None,
            site_state="unavailable",
            host_team=None,
            neutral_site=None,
            confirmation_id=None,
            reason="rule_evidence_unavailable",
        )
    if championship.selection == "unknown":
        return ChampionshipProjection(
            conference=conference, status="unavailable", participants=(),
            contenders=(), selection_basis=None, site_state="unavailable",
            host_team=None, neutral_site=None, confirmation_id=None,
            reason="selection_rule_unavailable",
        )
    if any(row.eligible is None for row in championship.eligibility):
        return ChampionshipProjection(
            conference=conference, status="unavailable", participants=(),
            contenders=(), selection_basis=None, site_state="unavailable",
            host_team=None, neutral_site=None, confirmation_id=None,
            reason="eligibility_evidence_unavailable",
        )
    if championship.selection == "none":
        return ChampionshipProjection(
            conference=conference,
            status="not_applicable",
            participants=(),
            contenders=(),
            selection_basis=None,
            site_state="not_applicable",
            host_team=None,
            neutral_site=None,
            confirmation_id=None,
            reason="no_championship",
        )
    # Descriptive records can be reconstructed from later evidence. A pregame
    # pairing cannot use that evidence until it was known: both the population
    # and every included/excluded game feeding its qualification record matter.
    conference_members = {
        member.team: member for member in supplement.supplement.members
        if member.conference == conference
    }
    membership_ids = {
        evidence_id for member in conference_members.values()
        for evidence_id in member.evidence_ids
    }
    designation_index = {
        item.provider_id: item for item in supplement.supplement.games
    }
    designation_ids = {
        evidence_id for game in selected_games
        if game.home_team in conference_members or game.away_team in conference_members
        for evidence_id in designation_index[game.provider_id].evidence_ids
    }
    for ids, reason in (
        (membership_ids, "membership_evidence_unavailable"),
        (designation_ids, "game_designation_evidence_unavailable"),
    ):
        if not _evidence_available_at_cutoff(supplement, tuple(sorted(ids)), cutoff):
            return ChampionshipProjection(
                conference=conference, status="unavailable", participants=(),
                contenders=(), selection_basis=None, site_state="unavailable",
                host_team=None, neutral_site=None, confirmation_id=None, reason=reason,
            )
    for game in selected_games:
        if game.home_team not in conference_members and game.away_team not in conference_members:
            continue
        bounds = _game_start_bounds(game)
        if bounds is None or bounds[1] >= cutoff:
            return ChampionshipProjection(
                conference=conference, status="unavailable", participants=(),
                contenders=(), selection_basis=None, site_state="unavailable",
                host_team=None, neutral_site=None, confirmation_id=None,
                reason="ambiguous_game_timing",
            )
    eligible = _eligible_teams(rule)
    rows = [row for row in standings.rows if eligible.get(row.school, False)]
    if any(row.conference_record.winning_percentage is None for row in rows) or not rows:
        return ChampionshipProjection(
            conference=conference,
            status="unavailable",
            participants=(),
            contenders=(),
            selection_basis=None,
            site_state="unavailable",
            host_team=None,
            neutral_site=None,
            confirmation_id=None,
            reason="no_qualifying_results",
        )

    selected: tuple[str, ...] | None = None
    contenders: tuple[str, ...] = ()
    if championship.selection == "top_two":
        ordered = sorted(rows, key=lambda row: (-float(row.conference_record.winning_percentage), row.school))
        first_pct = ordered[0].conference_record.winning_percentage
        first = [row.school for row in ordered if row.conference_record.winning_percentage == first_pct]
        if len(first) > 2:
            contenders = tuple(first)
        elif len(first) == 2:
            selected = tuple(sorted(first))
        else:
            second_pct = ordered[1].conference_record.winning_percentage if len(ordered) > 1 else None
            second = [row.school for row in ordered if row.conference_record.winning_percentage == second_pct]
            if second_pct is None or len(second) > 1:
                contenders = tuple(first + second)
            else:
                selected = (first[0], second[0])
    else:
        leaders: list[str] = []
        for division in championship.divisions:
            division_rows = [row for row in rows if row.school in division.members]
            if not division_rows:
                contenders = tuple(sorted(division.members))
                break
            best_pct = max(float(row.conference_record.winning_percentage) for row in division_rows)
            best = [row.school for row in division_rows if row.conference_record.winning_percentage == best_pct]
            if len(best) != 1:
                contenders = tuple(sorted(set(contenders).union(best)))
                break
            leaders.extend(best)
        if not contenders and len(leaders) == 2:
            selected = tuple(leaders)
        elif not contenders:
            contenders = tuple(sorted(leaders))

    confirmation_candidates = [
        confirmation
        for confirmation in supplement.supplement.confirmations
        if confirmation.conference == conference
        and (
            cutoff is not None
            and confirmation.known_at_bound is not None
            and confirmation.known_at_bound <= cutoff
        )
        and _evidence_available_at_cutoff(
            supplement, confirmation.evidence_ids, cutoff
        )
    ]
    confirmation_site_mode: str | None = None
    confirmation_host_team: str | None = None
    if confirmation_candidates:
        participant_sites: dict[tuple[str, ...], set[tuple[str | None, str | None]]] = defaultdict(set)
        for confirmation in confirmation_candidates:
            participant_sites[confirmation.participants].add(
                (confirmation.site_mode, confirmation.host_team)
            )
        conflicting_participants = {
            participants
            for participants, sites in participant_sites.items()
            if len(sites) > 1
        }
        if conflicting_participants:
            selected = None
            contenders = tuple(
                sorted({team for participants in conflicting_participants for team in participants})
            )
            selection_basis = None
            confirmation_id = None
        else:
            def signature(item: Any) -> tuple[tuple[str, ...], str | None, str | None]:
                return item.participants, item.site_mode, item.host_team

            grouped: dict[
                tuple[tuple[str, ...], str | None, str | None], list[Any]
            ] = defaultdict(list)
            for item in confirmation_candidates:
                grouped[signature(item)].append(item)

            confirmation: Any | None = None
            if len(grouped) == 1:
                # If all confirmations assert the same signature, their
                # chronology does not change the product fact. Prefer a
                # provably latest record when one exists; otherwise choose a
                # stable identity without pretending an overlapping interval
                # has an exact order.
                group = next(iter(grouped.values()))
                provable = [
                    item
                    for item in group
                    if item.known_at_lower_bound is not None
                    and all(
                        item.known_at_lower_bound > other.known_at_bound
                        for other in group
                        if other is not item
                    )
                ]
                if len(provable) == 1:
                    confirmation = provable[0]
                else:
                    confirmation = min(group, key=lambda item: item.confirmation_id)
            else:
                # A confirmation can supersede a different signature only
                # when its earliest possible time is later than every
                # confirmation of that competing signature.  Compare each
                # candidate independently: an older record carrying the same
                # signature must not prevent a later record from winning.
                # Overlapping date/local-clock intervals cannot establish
                # which participant/site assertion superseded the other.
                provable_groups: dict[
                    tuple[tuple[str, ...], str | None, str | None], Any
                ] = {}
                for current_signature, group in grouped.items():
                    provable = [
                        item
                        for item in group
                        if item.known_at_lower_bound is not None
                        and all(
                            item.known_at_lower_bound > other.known_at_bound
                            for other_signature, other_group in grouped.items()
                            if other_signature != current_signature
                            for other in other_group
                        )
                    ]
                    if provable:
                        provable_groups[current_signature] = max(
                            provable,
                            key=lambda item: (
                                item.known_at_lower_bound,
                                item.known_at_bound,
                                item.confirmation_id,
                            ),
                        )
                if len(provable_groups) == 1:
                    confirmation = next(iter(provable_groups.values()))

            if confirmation is not None:
                if all(eligible.get(team, False) for team in confirmation.participants):
                    selected = confirmation.participants
                    contenders = ()
                    selection_basis = "official_confirmation"
                    confirmation_id = confirmation.confirmation_id
                    confirmation_site_mode = confirmation.site_mode
                    confirmation_host_team = confirmation.host_team
                else:
                    selected = None
                    contenders = confirmation.participants
                    selection_basis = None
                    confirmation_id = None
            else:
                selected = None
                contenders = tuple(
                    sorted({team for item in confirmation_candidates for team in item.participants})
                )
                selection_basis = None
                confirmation_id = None
    else:
        selection_basis = "standings" if selected else None
        confirmation_id = None

    if selected is None:
        return ChampionshipProjection(
            conference=conference,
            status="unresolved" if contenders else "unavailable",
            participants=(),
            contenders=contenders,
            selection_basis=None,
            site_state="unresolved" if contenders else "unavailable",
            host_team=None,
            neutral_site=None,
            confirmation_id=None,
            reason="unresolved_qualification",
        )

    site = championship.site
    site_mode = confirmation_site_mode or site.mode
    if site_mode == "neutral":
        site_state, host_team, neutral = "neutral", None, True
    elif site_mode == "fixed_hosted":
        fixed_host = confirmation_host_team or site.fixed_host
        if fixed_host not in selected:
            site_state, host_team, neutral = "unresolved", None, None
        else:
            site_state, host_team, neutral = "hosted", fixed_host, False
    elif site_mode == "seed_hosted":
        positions = {row.school: row.position for row in standings.rows}
        first_position = positions.get(selected[0])
        second_position = positions.get(selected[1])
        if first_position is None or second_position is None or first_position == second_position:
            site_state, host_team, neutral = "unresolved", None, None
        else:
            host_team = selected[0] if first_position < second_position else selected[1]
            site_state, neutral = "hosted", False
    else:
        site_state, host_team, neutral = "unresolved", None, None
    status = "confirmed" if confirmation_id is not None else "projected"
    return ChampionshipProjection(
        conference=conference,
        status=status,
        participants=selected,
        contenders=(),
        selection_basis=selection_basis,
        site_state=site_state,
        host_team=host_team,
        neutral_site=neutral,
        confirmation_id=confirmation_id,
        reason="unresolved_site" if site_state == "unresolved" else None,
    )


def derive_conference_reference(
    snapshot: Any,
    supplement: ValidatedConferenceSupplement | ConferenceSupplement,
    rankings: Sequence[CheckpointRanking],
    *,
    phase: str,
    target_week: int | None,
    cutoff: datetime | None,
    dataset_id: str,
    model_version: str,
) -> ConferenceReference:
    """Derive one checkpoint of standings and conference comparisons.

    ``rankings`` must already be calculated and independently validated by the
    ranking engine.  This function validates roster identity and finite values
    but never recalculates CORS or a matchup margin.
    """

    if isinstance(supplement, ConferenceSupplement):
        try:
            supplement = validate_conference_supplement(supplement, snapshot)
        except ConferenceSourceError as exc:
            raise ConferenceReferenceError("supplement validation failed") from exc
    elif isinstance(supplement, ValidatedConferenceSupplement):
        try:
            candidate = supplement.supplement
            if not isinstance(candidate, ConferenceSupplement):
                raise ConferenceReferenceError(
                    "validated supplement wrapper contains an invalid supplement"
                )
            rebound = validate_conference_supplement(candidate, snapshot)
            if supplement.supplement_checksum != rebound.supplement_checksum:
                raise ConferenceReferenceError(
                    "validated supplement wrapper checksum does not match content"
                )
            if supplement.snapshot_checksum != rebound.snapshot_checksum:
                raise ConferenceReferenceError(
                    "validated supplement wrapper snapshot binding does not match content"
                )
            supplement = rebound
        except ConferenceSourceError as exc:
            raise ConferenceReferenceError("supplement validation failed") from exc
    else:
        raise ConferenceReferenceError("supplement must be snapshot-bound and validated")
    snapshot_checksum = _text(getattr(snapshot, "checksum", None), "snapshot checksum").lower()
    if not _SHA256.fullmatch(snapshot_checksum):
        raise ConferenceReferenceError("snapshot checksum must be a SHA-256 digest")
    try:
        recomputed_snapshot_checksum = snapshot_content_checksum(snapshot)
    except ConferenceSourceError as exc:
        raise ConferenceReferenceError("snapshot content cannot be checksummed") from exc
    if snapshot_checksum != recomputed_snapshot_checksum:
        raise ConferenceReferenceError("snapshot stored checksum does not match its content")
    if recomputed_snapshot_checksum != supplement.snapshot_checksum:
        raise ConferenceReferenceError("validated supplement is bound to another snapshot content")
    if snapshot_checksum != supplement.snapshot_checksum:
        raise ConferenceReferenceError("validated supplement is bound to another snapshot")
    checkpoint, normalized_phase, normalized_target = _checkpoint(phase, target_week)
    cutoff_value = _optional_timestamp(cutoff, "cutoff")
    dataset_id = _text(dataset_id, "dataset_id")
    model_version = _text(model_version, "model_version")
    rankings_index = _ranking_index(rankings, snapshot)
    selected_games, _ = _validate_checkpoint_games(snapshot, normalized_phase, normalized_target)
    _reject_games_at_or_after_cutoff(selected_games, cutoff_value)
    members, designations, rules = _member_maps(supplement)

    overall, qualification, inter_mutable = _add_game_records(
        selected_games, designations, members
    )
    conference_names = sorted(
        {member.conference for member in members.values() if member.conference}
    )
    standings = tuple(
        _standings_for(name, members, overall, qualification, rankings_index)
        for name in conference_names
    )
    independent_rows = _standings_independents(members, overall, rankings_index)
    comparisons = tuple(
        _comparison(item.conference, item.rows) for item in standings
    )

    # Emit zero-game pairings as explicit records so a missing denominator is
    # distinguishable from an omitted comparison.  Their winning percentage is
    # ``None`` through Record.winning_percentage, never zero.
    for conference in conference_names:
        for opponent in conference_names:
            if opponent != conference:
                inter_mutable.setdefault(
                    (conference, opponent, "conference"),
                    {"regular": _MutableRecord(), "postseason": _MutableRecord(), "unknown": _MutableRecord()},
                )
        if any(member.independent for member in members.values()):
            inter_mutable.setdefault(
                (conference, "FBS Independents", "independent"),
                {"regular": _MutableRecord(), "postseason": _MutableRecord(), "unknown": _MutableRecord()},
            )
        inter_mutable.setdefault(
            (conference, "FCS", "fcs"),
            {"regular": _MutableRecord(), "postseason": _MutableRecord(), "unknown": _MutableRecord()},
        )

    interconference: list[InterconferenceRecord] = []
    for conference, opponent, kind in sorted(inter_mutable):
        bucket = inter_mutable[(conference, opponent, kind)]
        regular = bucket["regular"].frozen()
        postseason = bucket["postseason"].frozen()
        unknown = bucket["unknown"].frozen()
        interconference.append(
            InterconferenceRecord(
                conference=conference,
                opponent=opponent,
                opponent_kind=kind,
                regular=regular,
                postseason=postseason,
                combined=_combined(regular, postseason, unknown),
                unknown=unknown,
            )
        )

    projections = tuple(
        _projection(item.conference, item, next(row for row in standings if row.conference == item.conference), supplement, cutoff_value, selected_games)
        for item in sorted(rules.values(), key=lambda value: value.conference)
    )
    return ConferenceReference(
        season=_strict_int(getattr(snapshot, "year", None), "snapshot year"),
        snapshot_checksum=snapshot_checksum,
        supplement_checksum=supplement.supplement_checksum,
        content_identity=supplement.content_identity,
        dataset_id=dataset_id,
        model_version=model_version,
        checkpoint=checkpoint,
        phase=normalized_phase,
        target_week=normalized_target,
        cutoff=cutoff_value,
        standings=standings,
        independent_rows=independent_rows,
        comparisons=comparisons,
        interconference=tuple(interconference),
        projections=projections,
    )


__all__ = [
    "ChampionshipProjection",
    "CheckpointRanking",
    "ConferenceReference",
    "ConferenceReferenceError",
    "ConferenceStandings",
    "InterconferenceRecord",
    "RatingComparison",
    "Record",
    "StandingsRow",
    "derive_conference_reference",
    "ranking_rows_from_engine",
]
