"""Static rendering for snapshot-bound conference reference checkpoints.

The conference derivation boundary returns typed values.  This module is the
small, pure adapter that turns a sequence of those values into the frozen
conference routes.  It does not read or write the website, fetch a source, or
derive a forecast estimate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import html
import json
import math
import re
from statistics import median
from typing import Any

try:
    from .conference_reference import (
        ChampionshipProjection,
        ConferenceReference,
        ConferenceStandings,
        InterconferenceRecord,
        RatingComparison,
        Record,
        StandingsRow,
    )
    from .public_safety import assert_public_bytes
except ImportError:  # pragma: no cover - supports direct execution from cfb/
    from conference_reference import (
        ChampionshipProjection,
        ConferenceReference,
        ConferenceStandings,
        InterconferenceRecord,
        RatingComparison,
        Record,
        StandingsRow,
    )
    from public_safety import assert_public_bytes


SCHEMA_VERSION = 1
RECONSTRUCTION_LABEL = "historical reconstruction"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f<>]+$")
_CHECKPOINT = re.compile(r"^W(0|[1-9][0-9]*)$")
_PROJECTION_REASONS = {
    "missing_cutoff",
    "rule_evidence_unavailable",
    "membership_evidence_unavailable",
    "game_designation_evidence_unavailable",
    "no_qualifying_results",
    "unresolved_qualification",
    "unresolved_site",
    "no_championship",
    "ambiguous_game_timing",
    "selection_rule_unavailable",
    "eligibility_evidence_unavailable",
}
_PROJECTION_REASON_LABELS = {
    "missing_cutoff": "Missing cutoff",
    "rule_evidence_unavailable": "Rule evidence unavailable at cutoff",
    "membership_evidence_unavailable": "Membership evidence unavailable at cutoff",
    "game_designation_evidence_unavailable": "Game designation evidence unavailable at cutoff",
    "no_qualifying_results": "No qualifying results",
    "unresolved_qualification": "Qualification unresolved",
    "unresolved_site": "Site treatment unresolved",
    "no_championship": "No championship game",
    "ambiguous_game_timing": "Game timing ambiguous at cutoff",
    "selection_rule_unavailable": "Selection rule unavailable at cutoff",
    "eligibility_evidence_unavailable": "Eligibility evidence unavailable at cutoff",
}

# Source values are deliberately enumerated.  Do not normalize arbitrary
# names into paths: a new source name needs an explicit reviewed mapping.
CONFERENCE_SLUGS: Mapping[str, str] = {
    "ACC": "acc",
    "Atlantic Coast Conference": "acc",
    "American": "american",
    "American Athletic": "american",
    "American Athletic Conference": "american",
    "Big 12": "big-12",
    "Big 12 Conference": "big-12",
    "Big Ten": "big-ten",
    "Big Ten Conference": "big-ten",
    "Conference USA": "conference-usa",
    "MAC": "mac",
    "Mid-American": "mac",
    "Mid-American Conference": "mac",
    "Mountain West": "mountain-west",
    "Mountain West Conference": "mountain-west",
    "Pac-12": "pac-12",
    "Pac-12 Conference": "pac-12",
    "SEC": "sec",
    "Southeastern Conference": "sec",
    "Sun Belt": "sun-belt",
    "Sun Belt Conference": "sun-belt",
}


class ConferenceRenderingError(ValueError):
    """Raised when references cannot be safely rendered as public artifacts."""


def conference_slug(name: str) -> str:
    """Return the reviewed URL slug for one exact source conference name."""

    if not isinstance(name, str) or not name or _SAFE_TEXT.fullmatch(name) is None:
        raise ConferenceRenderingError("conference name is unsafe or empty")
    try:
        return CONFERENCE_SLUGS[name]
    except KeyError as exc:
        raise ConferenceRenderingError(f"unknown conference source name {name!r}") from exc


def _safe_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ConferenceRenderingError(f"{field} must be text")
    value = value.strip()
    if not value or _SAFE_TEXT.fullmatch(value) is None:
        raise ConferenceRenderingError(f"{field} contains unsafe or empty text")
    return value


def _strict_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ConferenceRenderingError(f"{field} must be an integer")
    return value


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ConferenceRenderingError(f"{field} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConferenceRenderingError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ConferenceRenderingError(f"{field} must be finite")
    return parsed


def _checksum(value: Any, field: str) -> str:
    value = _safe_text(value, field).lower()
    if _SHA256.fullmatch(value) is None:
        raise ConferenceRenderingError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ConferenceRenderingError(f"{field} must be a sequence")
    return tuple(value)


def _same_number(actual: Any, expected: float | None, field: str) -> None:
    if expected is None:
        if actual is not None:
            raise ConferenceRenderingError(f"{field} must be unavailable")
        return
    if actual is None or not math.isclose(_finite(actual, field), expected, rel_tol=1e-12, abs_tol=1e-12):
        raise ConferenceRenderingError(f"{field} does not match derived values")


def _checkpoint_order(key: str) -> tuple[int, int]:
    if key == "PRESEASON":
        return (0, -1)
    if key == "FINAL":
        return (2, 0)
    match = _CHECKPOINT.fullmatch(key)
    if match is None:
        raise ConferenceRenderingError(f"unsupported checkpoint {key!r}")
    return (1, int(match.group(1)))


def _validate_checkpoint(ref: ConferenceReference) -> str:
    key = _safe_text(ref.checkpoint, "checkpoint")
    _checkpoint_order(key)
    phase = _safe_text(ref.phase, "reference phase").lower()
    if phase not in {"preseason", "week", "final"}:
        raise ConferenceRenderingError("unsupported reference phase")
    target = ref.target_week
    if key == "PRESEASON":
        if phase != "preseason" or target is not None:
            raise ConferenceRenderingError("PRESEASON identity is inconsistent")
    elif key == "FINAL":
        if phase != "final" or type(target) is not int or target < 0:
            raise ConferenceRenderingError("FINAL identity is inconsistent")
    else:
        match = _CHECKPOINT.fullmatch(key)
        assert match is not None
        week = int(match.group(1))
        if phase != "week" or type(target) is not int or target != week:
            raise ConferenceRenderingError("week identity is inconsistent")
    cutoff = ref.cutoff
    if cutoff is not None:
        if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ConferenceRenderingError("cutoff must be timezone-aware")
    return key


def _validate_record(record: Any, field: str) -> Record:
    if type(record) is not Record:
        raise ConferenceRenderingError(f"{field} must be a Record")
    for name in ("wins", "losses", "ties"):
        value = getattr(record, name)
        if type(value) is not int or value < 0:
            raise ConferenceRenderingError(f"{field} {name} is invalid")
    if record.games != record.wins + record.losses + record.ties:
        raise ConferenceRenderingError(f"{field} games is inconsistent")
    percentage = record.winning_percentage
    if percentage is not None:
        _finite(percentage, f"{field} winning_percentage")
    return record


def _validate_row(row: Any, conference: str, field: str) -> StandingsRow:
    if type(row) is not StandingsRow:
        raise ConferenceRenderingError(f"{field} must be a StandingsRow")
    school = _safe_text(row.school, f"{field} team")
    if _safe_text(row.conference, f"{field} conference") != conference:
        raise ConferenceRenderingError(f"{field} conference identity disagrees")
    _validate_record(row.conference_record, f"{field} conference_record")
    _validate_record(row.overall_record, f"{field} overall_record")
    if row.cors is not None:
        _finite(row.cors, f"{field} cors")
    if row.national_rank is not None:
        rank = _strict_int(row.national_rank, f"{field} national_rank")
        if rank < 1:
            raise ConferenceRenderingError(f"{field} national_rank is invalid")
    if row.position is not None:
        position = _strict_int(row.position, f"{field} position")
        if position < 1:
            raise ConferenceRenderingError(f"{field} position is invalid")
    display_order = _strict_int(row.display_order, f"{field} display_order")
    if display_order < 1:
        raise ConferenceRenderingError(f"{field} display_order is invalid")
    return row


def _validate_standings(standings: Any, field: str) -> tuple[ConferenceStandings, tuple[StandingsRow, ...]]:
    if type(standings) is not ConferenceStandings:
        raise ConferenceRenderingError(f"{field} must be ConferenceStandings")
    conference = _safe_text(standings.conference, f"{field} conference")
    conference_slug(conference)
    rows = _sequence(standings.rows, f"{field} rows")
    if not rows:
        raise ConferenceRenderingError(f"{field} must contain members")
    checked = tuple(_validate_row(row, conference, f"{field} row {index}") for index, row in enumerate(rows))
    schools = [row.school for row in checked]
    if len(schools) != len(set(schools)):
        raise ConferenceRenderingError(f"{field} contains duplicate teams")
    expected_position: dict[str, int | None] = {}
    percentages = {
        row.school: row.conference_record.winning_percentage for row in checked
    }
    for row in checked:
        pct = percentages[row.school]
        expected_position[row.school] = (
            None
            if pct is None
            else 1 + sum(
                1
                for other in percentages.values()
                if other is not None and other > pct
            )
        )
        if row.position != expected_position[row.school]:
            raise ConferenceRenderingError(f"{field} position does not preserve ties")
    expected_order = tuple(
        sorted(
            checked,
            key=lambda row: (
                row.position is None,
                row.position if row.position is not None else 10**9,
                row.school.casefold(),
                row.school,
            ),
        )
    )
    if tuple(row.school for row in checked) != tuple(row.school for row in expected_order):
        raise ConferenceRenderingError(f"{field} display order is not canonical")
    if [row.display_order for row in checked] != list(range(1, len(checked) + 1)):
        raise ConferenceRenderingError(f"{field} display_order is not canonical")
    return standings, checked


def _expected_comparison(rows: Sequence[StandingsRow]) -> dict[str, Any]:
    ratings = [row.cors for row in rows]
    if any(value is None for value in ratings):
        return {
            "available": False,
            "reason": "missing_rating",
            "member_count": len(rows),
            "mean": None,
            "median": None,
            "top_count": None,
            "top_mean": None,
            "remainder_count": None,
            "remainder_mean": None,
            "top_gap": None,
        }
    if not ratings:
        return {
            "available": False,
            "reason": "no_members",
            "member_count": 0,
            "mean": None,
            "median": None,
            "top_count": None,
            "top_mean": None,
            "remainder_count": None,
            "remainder_mean": None,
            "top_gap": None,
        }
    values = sorted((float(value) for value in ratings), reverse=True)
    top_count = math.ceil(len(values) / 4)
    remainder = values[top_count:]
    mean = sum(values) / len(values)
    top_mean = sum(values[:top_count]) / top_count
    remainder_mean = sum(remainder) / len(remainder) if remainder else None
    if remainder_mean is None:
        return {
            "available": False,
            "reason": "empty_remainder",
            "member_count": len(rows),
            "mean": mean,
            "median": float(median(values)),
            "top_count": top_count,
            "top_mean": top_mean,
            "remainder_count": 0,
            "remainder_mean": None,
            "top_gap": None,
        }
    return {
        "available": True,
        "reason": None,
        "member_count": len(rows),
        "mean": mean,
        "median": float(median(values)),
        "top_count": top_count,
        "top_mean": top_mean,
        "remainder_count": len(remainder),
        "remainder_mean": remainder_mean,
        "top_gap": top_mean - remainder_mean,
    }


def _validate_comparison(
    comparison: Any,
    rows: Sequence[StandingsRow],
    field: str,
) -> RatingComparison:
    if type(comparison) is not RatingComparison:
        raise ConferenceRenderingError(f"{field} must be RatingComparison")
    conference = _safe_text(comparison.conference, f"{field} conference")
    if type(comparison.available) is not bool:
        raise ConferenceRenderingError(f"{field} availability is invalid")
    if comparison.reason is not None:
        _safe_text(comparison.reason, f"{field} reason")
    expected = _expected_comparison(rows)
    for name in ("member_count", "top_count", "remainder_count"):
        actual = getattr(comparison, name)
        expected_value = expected[name]
        if actual != expected_value:
            raise ConferenceRenderingError(f"{field} {name} does not match population")
    if comparison.available != expected["available"] or comparison.reason != expected["reason"]:
        raise ConferenceRenderingError(f"{field} availability does not match population")
    for name in ("mean", "median", "top_mean", "remainder_mean", "top_gap"):
        _same_number(getattr(comparison, name), expected[name], f"{field} {name}")
    return comparison


def _validate_interconference(
    values: Any,
    conference_names: set[str],
    has_independents: bool,
    field: str,
) -> tuple[InterconferenceRecord, ...]:
    records = _sequence(values, field)
    checked: list[InterconferenceRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for index, record in enumerate(records):
        item_field = f"{field} row {index}"
        if type(record) is not InterconferenceRecord:
            raise ConferenceRenderingError(f"{item_field} must be InterconferenceRecord")
        conference = _safe_text(record.conference, f"{item_field} conference")
        opponent = _safe_text(record.opponent, f"{item_field} opponent")
        kind = _safe_text(record.opponent_kind, f"{item_field} opponent_kind").lower()
        if conference not in conference_names:
            raise ConferenceRenderingError(f"{item_field} names an unknown conference")
        if kind == "conference":
            if opponent not in conference_names or opponent == conference:
                raise ConferenceRenderingError(f"{item_field} has an invalid conference opponent")
        elif kind == "independent":
            if opponent != "FBS Independents" or not has_independents:
                raise ConferenceRenderingError(f"{item_field} has an invalid independent opponent")
        elif kind == "fcs":
            if opponent != "FCS":
                raise ConferenceRenderingError(f"{item_field} has an invalid FCS opponent")
        else:
            raise ConferenceRenderingError(f"{item_field} has an unsupported opponent kind")
        key = (conference, opponent, kind)
        if key in seen:
            raise ConferenceRenderingError(f"{field} contains duplicate records")
        seen.add(key)
        regular = _validate_record(record.regular, f"{item_field} regular")
        postseason = _validate_record(record.postseason, f"{item_field} postseason")
        unknown = _validate_record(record.unknown, f"{item_field} unknown")
        combined = _validate_record(record.combined, f"{item_field} combined")
        if combined != Record(
            regular.wins + postseason.wins + unknown.wins,
            regular.losses + postseason.losses + unknown.losses,
            regular.ties + postseason.ties + unknown.ties,
        ):
            raise ConferenceRenderingError(f"{item_field} combined record is inconsistent")
        checked.append(record)
    by_key = {(item.conference, item.opponent, item.opponent_kind): item for item in checked}
    for item in checked:
        if item.opponent_kind != "conference":
            continue
        inverse = by_key.get((item.opponent, item.conference, "conference"))
        if inverse is None:
            raise ConferenceRenderingError("conference records are missing an inverse side")
        for left, right in (
            (item.regular, inverse.regular),
            (item.postseason, inverse.postseason),
            (item.unknown, inverse.unknown),
        ):
            if (left.wins, left.losses, left.ties) != (right.losses, right.wins, right.ties):
                raise ConferenceRenderingError("conference records do not reconcile")
    return tuple(checked)


def _validate_projection(
    projection: Any,
    conference_names: set[str],
    members_by_conference: Mapping[str, set[str]],
    field: str,
) -> ChampionshipProjection:
    if type(projection) is not ChampionshipProjection:
        raise ConferenceRenderingError(f"{field} must be ChampionshipProjection")
    conference = _safe_text(projection.conference, f"{field} conference")
    if conference not in conference_names:
        raise ConferenceRenderingError(f"{field} names an unknown conference")
    status = _safe_text(projection.status, f"{field} status").lower()
    if status not in {"projected", "confirmed", "unresolved", "unavailable", "not_applicable"}:
        raise ConferenceRenderingError(f"{field} status is invalid")
    participants = _sequence(projection.participants, f"{field} participants")
    contenders = _sequence(projection.contenders, f"{field} contenders")
    participants = tuple(_safe_text(item, f"{field} participant") for item in participants)
    contenders = tuple(_safe_text(item, f"{field} contender") for item in contenders)
    if len(set(participants)) != len(participants) or len(set(contenders)) != len(contenders):
        raise ConferenceRenderingError(f"{field} contains duplicate teams")
    members = members_by_conference[conference]
    if any(item not in members for item in (*participants, *contenders)):
        raise ConferenceRenderingError(f"{field} names a team outside its conference")
    if status in {"projected", "confirmed"} and len(participants) != 2:
        raise ConferenceRenderingError(f"{field} available status requires two participants")
    if status in {"projected", "confirmed"} and contenders:
        raise ConferenceRenderingError(f"{field} available status cannot carry contenders")
    if status not in {"projected", "confirmed"} and participants:
        raise ConferenceRenderingError(f"{field} unavailable status cannot carry participants")
    site_state = _safe_text(projection.site_state, f"{field} site_state").lower()
    if site_state not in {"neutral", "hosted", "unresolved", "unavailable", "not_applicable"}:
        raise ConferenceRenderingError(f"{field} site_state is invalid")
    if status == "not_applicable" and site_state != "not_applicable":
        raise ConferenceRenderingError(f"{field} not_applicable site state is invalid")
    if projection.host_team is not None:
        host = _safe_text(projection.host_team, f"{field} host_team")
        if host not in participants or site_state != "hosted":
            raise ConferenceRenderingError(f"{field} host identity is inconsistent")
    if projection.neutral_site is not None and type(projection.neutral_site) is not bool:
        raise ConferenceRenderingError(f"{field} neutral_site is invalid")
    if site_state == "neutral" and projection.neutral_site is not True:
        raise ConferenceRenderingError(f"{field} neutral site state is inconsistent")
    if site_state == "hosted" and projection.neutral_site is not False:
        raise ConferenceRenderingError(f"{field} hosted site state is inconsistent")
    if site_state in {"unresolved", "unavailable", "not_applicable"} and projection.host_team is not None:
        raise ConferenceRenderingError(f"{field} unavailable site cannot carry a host")
    if projection.selection_basis is not None:
        _safe_text(projection.selection_basis, f"{field} selection_basis")
    if projection.confirmation_id is not None:
        _safe_text(projection.confirmation_id, f"{field} confirmation_id")
        if status != "confirmed":
            raise ConferenceRenderingError(f"{field} confirmation identity requires confirmed status")
    elif status == "confirmed":
        raise ConferenceRenderingError(f"{field} confirmed status requires confirmation identity")
    reason = projection.reason
    if reason is not None:
        reason = _safe_text(reason, f"{field} reason")
        if reason not in _PROJECTION_REASONS:
            raise ConferenceRenderingError(f"{field} reason is invalid")
    if status in {"unavailable", "unresolved", "not_applicable"} and reason is None:
        raise ConferenceRenderingError(f"{field} unavailable status requires a reason")
    if status in {"projected", "confirmed"} and reason not in {None, "unresolved_site"}:
        raise ConferenceRenderingError(f"{field} available status has an invalid reason")
    return projection


@dataclass(frozen=True)
class _RenderContext:
    references: tuple[ConferenceReference, ...]
    by_checkpoint: Mapping[str, ConferenceReference]
    conference_names: tuple[str, ...]
    slugs: Mapping[str, str]
    default_checkpoint: str
    season: int
    dataset_id: str
    model_version: str
    source_snapshot: str
    supplement_checksum: str
    content_identity: str


def _validate_references(references: Sequence[ConferenceReference]) -> _RenderContext:
    if isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
        raise ConferenceRenderingError("references must be a sequence")
    values = tuple(references)
    if not values:
        raise ConferenceRenderingError("at least one conference checkpoint is required")
    checked_keys: dict[str, ConferenceReference] = {}
    base_identity: tuple[Any, ...] | None = None
    population_signature: tuple[Any, ...] | None = None
    source_names_by_slug: dict[str, set[str]] = {}
    all_conference_names: set[str] = set()
    for index, reference in enumerate(values):
        field = f"reference {index}"
        if type(reference) is not ConferenceReference:
            raise ConferenceRenderingError(f"{field} must be ConferenceReference")
        key = _validate_checkpoint(reference)
        if key in checked_keys:
            raise ConferenceRenderingError(f"duplicate checkpoint {key}")
        checked_keys[key] = reference
        season = _strict_int(reference.season, f"{field} season")
        if season < 1:
            raise ConferenceRenderingError(f"{field} season is invalid")
        source_snapshot = _checksum(reference.snapshot_checksum, f"{field} snapshot_checksum")
        supplement_checksum = _checksum(reference.supplement_checksum, f"{field} supplement_checksum")
        content_identity = _safe_text(reference.content_identity, f"{field} content_identity")
        dataset_id = _safe_text(reference.dataset_id, f"{field} dataset_id")
        model_version = _safe_text(reference.model_version, f"{field} model_version")
        identity = (
            season,
            source_snapshot,
            supplement_checksum,
            content_identity,
            dataset_id,
            model_version,
        )
        if base_identity is None:
            base_identity = identity
        elif identity != base_identity:
            raise ConferenceRenderingError("checkpoint provenance identity is inconsistent")

        standings_values = _sequence(reference.standings, f"{field} standings")
        if not standings_values:
            raise ConferenceRenderingError(f"{field} requires conference standings")
        standings_by_name: dict[str, tuple[StandingsRow, ...]] = {}
        members_by_conference: dict[str, set[str]] = {}
        for standing_index, standing in enumerate(standings_values):
            checked_standing, rows = _validate_standings(
                standing, f"{field} standings {standing_index}"
            )
            conference = checked_standing.conference
            if conference in standings_by_name:
                raise ConferenceRenderingError(f"{field} contains duplicate conference standings")
            standings_by_name[conference] = rows
            members_by_conference[conference] = {row.school for row in rows}
            all_conference_names.add(conference)
            slug = conference_slug(conference)
            source_names_by_slug.setdefault(slug, set()).add(conference)

        independent_rows = _sequence(reference.independent_rows, f"{field} independent_rows")
        independent_checked: list[StandingsRow] = []
        independent_names: set[str] = set()
        for row_index, row in enumerate(independent_rows):
            checked_row = _validate_row(row, "FBS Independents", f"{field} independent row {row_index}")
            if checked_row.school in independent_names:
                raise ConferenceRenderingError(f"{field} contains duplicate independent teams")
            independent_names.add(checked_row.school)
            independent_checked.append(checked_row)
        if any(name in independent_names for names in members_by_conference.values() for name in names):
            raise ConferenceRenderingError(f"{field} repeats a team across populations")

        signature = (
            tuple(
                (
                    name,
                    tuple(
                        sorted(
                            (row.school for row in standings_by_name[name]),
                            key=lambda value: (value.casefold(), value),
                        )
                    ),
                )
                for name in sorted(standings_by_name)
            ),
            tuple(sorted(independent_names)),
        )
        if population_signature is None:
            population_signature = signature
        elif signature != population_signature:
            raise ConferenceRenderingError("checkpoint population is inconsistent")

        comparison_values = _sequence(reference.comparisons, f"{field} comparisons")
        comparisons_by_name: dict[str, RatingComparison] = {}
        for comparison_index, comparison in enumerate(comparison_values):
            if type(comparison) is not RatingComparison:
                raise ConferenceRenderingError(f"{field} comparison {comparison_index} is invalid")
            name = _safe_text(comparison.conference, f"{field} comparison conference")
            if name in comparisons_by_name:
                raise ConferenceRenderingError(f"{field} contains duplicate comparisons")
            if name in standings_by_name:
                comparison_rows = standings_by_name[name]
            elif name == "FBS Independents":
                raise ConferenceRenderingError(
                    f"{field} comparisons must exclude FBS Independents"
                )
            else:
                raise ConferenceRenderingError(f"{field} comparison names an unknown population")
            comparisons_by_name[name] = _validate_comparison(
                comparison, comparison_rows, f"{field} comparison {comparison_index}"
            )
        expected_comparison_names = set(standings_by_name)
        if set(comparisons_by_name) != expected_comparison_names:
            raise ConferenceRenderingError(f"{field} comparisons do not cover populations")

        _validate_interconference(
            reference.interconference,
            set(standings_by_name),
            bool(independent_checked),
            f"{field} interconference",
        )
        projections = _sequence(reference.projections, f"{field} projections")
        projection_names: set[str] = set()
        for projection_index, projection in enumerate(projections):
            name = _safe_text(projection.conference, f"{field} projection conference")
            if name in projection_names:
                raise ConferenceRenderingError(f"{field} contains duplicate projections")
            projection_names.add(name)
            _validate_projection(
                projection,
                set(standings_by_name),
                members_by_conference,
                f"{field} projection {projection_index}",
            )
        if projection_names != set(standings_by_name):
            raise ConferenceRenderingError(f"{field} projections do not cover conferences")

    assert base_identity is not None
    assert population_signature is not None
    for slug, names in source_names_by_slug.items():
        if len(names) > 1:
            raise ConferenceRenderingError(
                f"conference source aliases collide for slug {slug!r}: {sorted(names)}"
            )
    ordered = tuple(sorted(checked_keys, key=_checkpoint_order))
    default_checkpoint = "FINAL" if "FINAL" in checked_keys else ordered[-1]
    conference_names = tuple(sorted(all_conference_names, key=lambda name: (conference_slug(name), name)))
    return _RenderContext(
        references=tuple(checked_keys[key] for key in ordered),
        by_checkpoint=dict(checked_keys),
        conference_names=conference_names,
        slugs={name: conference_slug(name) for name in conference_names},
        default_checkpoint=default_checkpoint,
        season=base_identity[0],
        source_snapshot=base_identity[1],
        supplement_checksum=base_identity[2],
        content_identity=base_identity[3],
        dataset_id=base_identity[4],
        model_version=base_identity[5],
    )


def _number(value: float | int) -> str:
    return repr(float(value)) if isinstance(value, float) else str(value)


def _percentage(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{_number(value * 100)}%"


def _record_text(record: Record) -> str:
    if record.games == 0:
        return "No games"
    return f"{record.wins}-{record.losses}-{record.ties} ({record.games} games; {_percentage(record.winning_percentage)})"


def _comparison_state(comparison: RatingComparison) -> str:
    if comparison.available:
        return "Available"
    return f"Unavailable ({comparison.reason})"


def _projection_text(projection: ChampionshipProjection) -> tuple[str, str, str, str]:
    reason = _PROJECTION_REASON_LABELS.get(projection.reason or "", "")
    if projection.status == "not_applicable":
        return "No championship game", "Not applicable", "Not applicable", reason
    if projection.participants:
        pairing = " vs ".join(projection.participants)
    elif projection.contenders:
        pairing = "Contenders: " + ", ".join(projection.contenders)
    else:
        pairing = "Unavailable"
    site = projection.site_state.replace("_", " ").title()
    if projection.site_state == "hosted" and projection.host_team is not None:
        site = f"Hosted by {projection.host_team}"
    return pairing, projection.status.title(), site, reason


def _href(label: str, href: str, *, aria_current: str | None = None) -> str:
    current = (
        f' aria-current="{html.escape(aria_current, quote=True)}"'
        if aria_current is not None
        else ""
    )
    return f'<a href="{html.escape(href, quote=True)}"{current}>{html.escape(label)}</a>'


def _nav(context: _RenderContext, reference: ConferenceReference, kind: str, slug: str | None = None) -> str:
    key = reference.checkpoint
    nested = kind == "standings"
    prefix = "../" if nested else "./"
    outer = "../../" if nested else "../"
    links: list[str] = [
        _href("Season", f"{outer}{context.season}_CFB.html"),
        _href("Ranking progression", f"{outer}history/{context.season}_FBS_progression.html"),
        _href(
            "Conference overview",
            f"{prefix}{context.season}_FBS_conferences.html",
            aria_current="page" if kind == "overview" else None,
        ),
        _href("Conference data", f"{prefix}{context.season}_FBS_conferences.json"),
        _href(
            "Comparison",
            f"{prefix}{key}_FBS_comparison.html",
        ),
    ]
    links.append("<span>Checkpoints:</span>")
    for checkpoint_reference in context.references:
        checkpoint = checkpoint_reference.checkpoint
        if nested:
            if slug is None:
                raise ConferenceRenderingError("standings navigation requires a conference slug")
            target = f"./{checkpoint}_FBS_standings.html"
        else:
            target = f"{prefix}{checkpoint}_FBS_comparison.html"
        links.append(
            _href(
                checkpoint,
                target,
                aria_current=(
                    "page"
                    if checkpoint == key and kind in {"comparison", "standings"}
                    else None
                ),
            )
        )
    for name in context.conference_names:
        target_slug = context.slugs[name]
        if nested:
            target = (
                f"./{key}_FBS_standings.html"
                if target_slug == slug
                else f"../{target_slug}/{key}_FBS_standings.html"
            )
        else:
            target = f"./{target_slug}/{key}_FBS_standings.html"
        links.append(_href(f"{name} standings", target))
    return "<nav aria-label=\"Conference reference navigation\">" + " ".join(links) + "</nav>"


def _provenance(reference: ConferenceReference) -> str:
    cutoff = reference.cutoff.isoformat() if reference.cutoff is not None else "Unavailable"
    values = (
        ("Status", RECONSTRUCTION_LABEL),
        ("Checkpoint", reference.checkpoint),
        ("Cutoff", cutoff),
        ("Source snapshot", reference.snapshot_checksum),
        ("Conference supplement", reference.supplement_checksum),
        ("Content identity", reference.content_identity),
        ("Dataset", reference.dataset_id),
        ("Model", reference.model_version),
    )
    rows = "".join(
        f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(value))}</dd>"
        for label, value in values
    )
    return f"<details><summary>Data provenance</summary><dl>{rows}</dl></details>"


_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:1rem;color:#17212b;line-height:1.4}"
    "h1{font-size:clamp(1.4rem,4vw,2rem)}h2{font-size:1.2rem}"
    "nav{display:flex;flex-wrap:wrap;gap:.5rem 1rem;margin:.75rem 0 1rem}"
    "nav a{padding:.25rem 0}details{margin:1rem 0}summary{cursor:pointer}"
    "dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1rem;overflow-wrap:anywhere}"
    ".table-scroll{max-width:100%;max-height:70vh;overflow:auto;border:1px solid #bbb;isolation:isolate;margin:1rem 0}"
    ".table-scroll:focus-visible{outline:3px solid #2463a3;outline-offset:2px}"
    "table{border-collapse:separate;border-spacing:0;font-variant-numeric:tabular-nums;min-width:42rem}"
    "caption{text-align:left;font-weight:700;padding:.5rem 0}"
    "th,td{border-right:1px solid #bbb;border-bottom:1px solid #bbb;padding:.5rem;text-align:right;background:#fff;white-space:nowrap}"
    "thead th{position:sticky;top:0;z-index:2;background:#edf2f7}"
    "tbody th{position:sticky;left:0;z-index:1;background:#f5f7fa;text-align:left}"
    "thead th:first-child{left:0;z-index:3;text-align:left}"
    "td:first-child{text-align:left}.state{color:#555;font-style:italic}"
    "@media(max-width:42rem){body{margin:.75rem}th,td{padding:.4rem}.table-scroll{max-height:65vh}}"
)


def _page(title: str, heading: str, nav: str, body: str, provenance: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body>"
        f"<h1>{html.escape(heading)}</h1><p>Historical reconstruction</p>{nav}{body}{provenance}"
        "</body></html>\n"
    )


def _table(caption: str, headers: Sequence[str], rows: Sequence[str], label: str) -> str:
    header_html = "".join(f"<th scope=\"col\">{html.escape(header)}</th>" for header in headers)
    return (
        f"<div class=\"table-scroll\" role=\"region\" aria-label=\"{html.escape(label, quote=True)}\" tabindex=\"0\">"
        f"<table><caption>{html.escape(caption)}</caption><thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _comparison_rows(context: _RenderContext, reference: ConferenceReference) -> tuple[RatingComparison, ...]:
    comparisons = tuple(reference.comparisons)
    return tuple(
        sorted(
            (item for item in comparisons if item.conference != "FBS Independents"),
            key=lambda item: (
                item.mean is None,
                -(item.mean if item.mean is not None else 0.0),
                item.conference.casefold(),
            ),
        )
    )


def _standings_for(reference: ConferenceReference, conference: str) -> ConferenceStandings:
    for standings in reference.standings:
        if standings.conference == conference:
            return standings
    raise ConferenceRenderingError(f"missing standings for {conference!r}")


def _comparison_for(reference: ConferenceReference, conference: str) -> RatingComparison:
    for comparison in reference.comparisons:
        if comparison.conference == conference:
            return comparison
    raise ConferenceRenderingError(f"missing comparison for {conference!r}")


def _projection_for(reference: ConferenceReference, conference: str) -> ChampionshipProjection:
    for projection in reference.projections:
        if projection.conference == conference:
            return projection
    raise ConferenceRenderingError(f"missing projection for {conference!r}")


def _render_summary_table(context: _RenderContext, reference: ConferenceReference) -> str:
    rows: list[str] = []
    for comparison in _comparison_rows(context, reference):
        projection = _projection_for(reference, comparison.conference)
        pairing, status, site, reason = _projection_text(projection)
        values = (
            str(comparison.member_count),
            _number(comparison.mean) if comparison.mean is not None else "Unavailable",
            _number(comparison.median) if comparison.median is not None else "Unavailable",
            _number(comparison.top_count) if comparison.top_count is not None else "Unavailable",
            _number(comparison.top_mean) if comparison.top_mean is not None else "Unavailable",
            _number(comparison.remainder_count)
            if comparison.remainder_count is not None
            else "Unavailable",
            _number(comparison.remainder_mean)
            if comparison.remainder_mean is not None
            else "Unavailable",
            _number(comparison.top_gap) if comparison.top_gap is not None else "Unavailable",
        )
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{html.escape(comparison.conference)}</th>"
            + "".join(f"<td>{html.escape(value)}</td>" for value in values)
            + f"<td>{html.escape(_comparison_state(comparison))}</td>"
            + f"<td>{html.escape(pairing)}</td><td>{html.escape(status)}</td><td>{html.escape(site)}</td>"
            + f"<td>{html.escape(reason)}</td></tr>"
        )
    return _table(
        "Conference strength and projected championship",
        (
            "Conference", "Teams", "Mean CORS", "Median CORS", "Top quarter count",
            "Top quarter mean", "Remainder count", "Remainder mean", "Top gap",
            "Distribution", "Pairing", "Status", "Site", "Reason",
        ),
        rows,
        "Conference comparison",
    )


def _render_distribution_tables(context: _RenderContext, reference: ConferenceReference) -> str:
    sections: list[str] = []
    for conference in context.conference_names:
        standings = _standings_for(reference, conference)
        comparison = _comparison_for(reference, conference)
        rows: list[str] = []
        rows_in_rating_order = tuple(
            sorted(
                standings.rows,
                key=lambda row: (
                    row.cors is None,
                    -(row.cors if row.cors is not None else 0.0),
                    row.school.casefold(),
                    row.school,
                ),
            )
        )
        for row in rows_in_rating_order:
            cors = _number(row.cors) if row.cors is not None else "Unavailable"
            rank = str(row.national_rank) if row.national_rank is not None else "Unavailable"
            rows.append(
                f"<tr><th scope=\"row\">{html.escape(row.school)}</th>"
                f"<td>{html.escape(cors)}</td><td>{html.escape(rank)}</td></tr>"
            )
        sections.append(
            f"<section><h2>{html.escape(conference)} CORS distribution</h2>"
            f"<p>{html.escape(_comparison_state(comparison))}</p>"
            + _table(
                f"{conference} member ratings",
                ("Team", "CORS points", "National CORS rank"),
                rows,
                f"{conference} CORS distribution",
            )
            + "</section>"
        )
    if reference.independent_rows:
        rows = []
        for row in reference.independent_rows:
            rows.append(
                f"<tr><th scope=\"row\">{html.escape(row.school)}</th>"
                f"<td>{html.escape(_number(row.cors) if row.cors is not None else 'Unavailable')}</td>"
                f"<td>{html.escape(str(row.national_rank) if row.national_rank is not None else 'Unavailable')}</td></tr>"
            )
        sections.append(
            "<section><h2>FBS Independents CORS distribution</h2>"
            + _table(
                "FBS Independent member ratings",
                ("Team", "CORS points", "National CORS rank"),
                rows,
                "FBS Independent CORS distribution",
            )
            + "</section>"
        )
    return "".join(sections)


def _render_interconference(reference: ConferenceReference) -> str:
    rows: list[str] = []
    records = sorted(
        reference.interconference,
        key=lambda item: (item.conference.casefold(), item.opponent_kind, item.opponent.casefold()),
    )
    for record in records:
        rows.append(
            f"<tr><th scope=\"row\">{html.escape(record.conference)}</th>"
            f"<td>{html.escape(record.opponent)}</td><td>{html.escape(record.opponent_kind.upper())}</td>"
            f"<td>{html.escape(_record_text(record.regular))}</td>"
            f"<td>{html.escape(_record_text(record.postseason))}</td>"
            f"<td>{html.escape(_record_text(record.unknown))}</td>"
            f"<td>{html.escape(_record_text(record.combined))}</td></tr>"
        )
    return _table(
        "Interconference records",
        (
            "Conference",
            "Opponent",
            "Type",
            "Regular season",
            "Postseason",
            "Unknown phase",
            "Combined",
        ),
        rows,
        "Interconference records",
    )


def _render_projections(reference: ConferenceReference) -> str:
    rows: list[str] = []
    for projection in sorted(reference.projections, key=lambda item: item.conference.casefold()):
        pairing, status, site, reason = _projection_text(projection)
        rows.append(
            f"<tr><th scope=\"row\">{html.escape(projection.conference)}</th>"
            f"<td>{html.escape(pairing)}</td><td>{html.escape(status)}</td><td>{html.escape(site)}</td>"
            f"<td>{html.escape(reason)}</td></tr>"
        )
    return _table(
        "Championship projection state",
        ("Conference", "Pairing or contenders", "Status", "Site treatment", "Reason"),
        rows,
        "Championship projection",
    )


def _render_overview(context: _RenderContext, reference: ConferenceReference) -> str:
    nav = _nav(context, reference, "overview")
    body = (
        f"<p>Checkpoint: <strong>{html.escape(reference.checkpoint)}</strong>. "
        "Conference order uses mean CORS and excludes FBS Independents.</p>"
        + _render_summary_table(context, reference)
        + _render_projections(reference)
    )
    return _page(
        f"{context.season} FBS conference reference",
        f"{context.season} FBS conference reference — {reference.checkpoint}",
        nav,
        body,
        _provenance(reference),
    )


def _render_comparison(context: _RenderContext, reference: ConferenceReference) -> str:
    nav = _nav(context, reference, "comparison")
    body = (
        f"<p>Checkpoint: <strong>{html.escape(reference.checkpoint)}</strong>. "
        "Every member rating remains visible in the distributions below.</p>"
        + _render_summary_table(context, reference)
        + _render_distribution_tables(context, reference)
        + _render_interconference(reference)
        + _render_projections(reference)
    )
    return _page(
        f"{reference.checkpoint} conference comparison — {context.season} FBS",
        f"{reference.checkpoint} conference comparison — {context.season} FBS",
        nav,
        body,
        _provenance(reference),
    )


def _render_standings(context: _RenderContext, reference: ConferenceReference, conference: str) -> str:
    standings = _standings_for(reference, conference)
    projection = _projection_for(reference, conference)
    rows: list[str] = []
    for row in standings.rows:
        cors = _number(row.cors) if row.cors is not None else "Unavailable"
        rank = str(row.national_rank) if row.national_rank is not None else "Unavailable"
        position = str(row.position) if row.position is not None else "Unavailable"
        rows.append(
            f"<tr><th scope=\"row\">{html.escape(row.school)}</th>"
            f"<td>{html.escape(_record_text(row.conference_record))}</td>"
            f"<td>{html.escape(_record_text(row.overall_record))}</td>"
            f"<td>{html.escape(cors)}</td><td>{html.escape(rank)}</td><td>{html.escape(position)}</td></tr>"
        )
    pairing, status, site, reason = _projection_text(projection)
    body = (
        f"<p>Checkpoint: <strong>{html.escape(reference.checkpoint)}</strong>. "
        "Alphabetical order within a tied position does not select a berth.</p>"
        + _table(
            f"{conference} standings",
            ("Team", "Conference record", "Overall record", "CORS points", "National CORS rank", "Position"),
            rows,
            f"{conference} standings",
        )
        + _table(
            "Championship projection",
            ("Conference", "Pairing or contenders", "Status", "Site treatment", "Reason"),
            [
                f"<tr><th scope=\"row\">{html.escape(conference)}</th>"
                f"<td>{html.escape(pairing)}</td><td>{html.escape(status)}</td><td>{html.escape(site)}</td>"
                f"<td>{html.escape(reason)}</td></tr>"
            ],
            f"{conference} championship projection",
        )
    )
    return _page(
        f"{conference} standings — {reference.checkpoint} — {context.season} FBS",
        f"{conference} standings — {reference.checkpoint} — {context.season} FBS",
        _nav(context, reference, "standings", context.slugs[conference]),
        body,
        _provenance(reference),
    )


def _record_payload(record: Record) -> dict[str, Any]:
    return {
        "wins": record.wins,
        "losses": record.losses,
        "ties": record.ties,
        "games": record.games,
        "winning_percentage": record.winning_percentage,
    }


def _row_payload(row: StandingsRow) -> dict[str, Any]:
    return {
        "team": row.school,
        "conference": row.conference,
        "conference_record": _record_payload(row.conference_record),
        "overall_record": _record_payload(row.overall_record),
        "cors": row.cors,
        "national_rank": row.national_rank,
        "position": row.position,
        "display_order": row.display_order,
    }


def _comparison_payload(comparison: RatingComparison) -> dict[str, Any]:
    return {
        "conference": comparison.conference,
        "available": comparison.available,
        "reason": comparison.reason,
        "member_count": comparison.member_count,
        "mean": comparison.mean,
        "median": comparison.median,
        "top_count": comparison.top_count,
        "top_mean": comparison.top_mean,
        "remainder_count": comparison.remainder_count,
        "remainder_mean": comparison.remainder_mean,
        "top_gap": comparison.top_gap,
    }


def _interconference_payload(record: InterconferenceRecord) -> dict[str, Any]:
    return {
        "conference": record.conference,
        "opponent": record.opponent,
        "opponent_kind": record.opponent_kind,
        "regular": _record_payload(record.regular),
        "postseason": _record_payload(record.postseason),
        "unknown": _record_payload(record.unknown),
        "combined": _record_payload(record.combined),
    }


def _projection_payload(projection: ChampionshipProjection) -> dict[str, Any]:
    return {
        "conference": projection.conference,
        "status": projection.status,
        "participants": list(projection.participants),
        "contenders": list(projection.contenders),
        "selection_basis": projection.selection_basis,
        "site_state": projection.site_state,
        "host_team": projection.host_team,
        "neutral_site": projection.neutral_site,
        "confirmation_id": projection.confirmation_id,
        "reason": projection.reason,
    }


def _reference_payload(reference: ConferenceReference) -> dict[str, Any]:
    return {
        "checkpoint": reference.checkpoint,
        "phase": reference.phase,
        "target_week": reference.target_week,
        "cutoff": reference.cutoff.isoformat() if reference.cutoff is not None else None,
        "provenance": {
            "source_snapshot": reference.snapshot_checksum,
            "conference_supplement": reference.supplement_checksum,
            "content_identity": reference.content_identity,
            "dataset_id": reference.dataset_id,
            "model_version": reference.model_version,
            "classification": "FBS",
            "sport": "cfb",
            "provenance_class": RECONSTRUCTION_LABEL,
        },
        "standings": [
            {"conference": item.conference, "rows": [_row_payload(row) for row in item.rows]}
            for item in reference.standings
        ],
        "independent_rows": [_row_payload(row) for row in reference.independent_rows],
        "comparisons": [_comparison_payload(item) for item in reference.comparisons],
        "interconference": [_interconference_payload(item) for item in reference.interconference],
        "projections": [_projection_payload(item) for item in reference.projections],
    }


def conference_json(references: Sequence[ConferenceReference]) -> str:
    """Return canonical safe derived JSON for all rendered checkpoints."""

    context = _validate_references(references)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "sport": "cfb",
        "classification": "FBS",
        "season": context.season,
        "default_checkpoint": context.default_checkpoint,
        "provenance_class": RECONSTRUCTION_LABEL,
        "checkpoints": [_reference_payload(reference) for reference in context.references],
    }
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise ConferenceRenderingError("conference JSON contains an unsafe value") from exc


def render_conference_artifacts(
    references: Sequence[ConferenceReference],
) -> dict[str, bytes]:
    """Return frozen conference route bytes without writing to the website."""

    context = _validate_references(references)
    artifacts: dict[str, bytes] = {}
    root = f"cfb/years/{context.season}/conferences"
    default_reference = context.by_checkpoint[context.default_checkpoint]
    artifacts[f"{root}/{context.season}_FBS_conferences.html"] = _render_overview(
        context, default_reference
    ).encode("utf-8")
    artifacts[f"{root}/{context.season}_FBS_conferences.json"] = conference_json(
        context.references
    ).encode("utf-8")
    for reference in context.references:
        key = reference.checkpoint
        artifacts[f"{root}/{key}_FBS_comparison.html"] = _render_comparison(
            context, reference
        ).encode("utf-8")
        for conference in context.conference_names:
            slug = context.slugs[conference]
            artifacts[f"{root}/{slug}/{key}_FBS_standings.html"] = _render_standings(
                context, reference, conference
            ).encode("utf-8")
    for path, value in artifacts.items():
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/"):
            raise ConferenceRenderingError("renderer produced an unsafe route")
        if not isinstance(value, bytes):
            raise ConferenceRenderingError("renderer produced a non-byte artifact")
        try:
            assert_public_bytes(path, value)
        except (ValueError, TypeError) as exc:
            raise ConferenceRenderingError(f"public safety rejected {path}") from exc
        if path.endswith(".html"):
            text = value.decode("utf-8")
            lowered = text.lower()
            if "<script" in lowered or re.search(r"(?:href|src)=[\"'](?:https?:|javascript:)", lowered):
                raise ConferenceRenderingError(f"HTML artifact contains executable or external content: {path}")
    return artifacts


def build_conference_artifacts(
    references: Sequence[ConferenceReference],
) -> dict[str, bytes]:
    """Canonical builder name for callers assembling a static candidate."""

    return render_conference_artifacts(references)


__all__ = [
    "CONFERENCE_SLUGS",
    "ConferenceRenderingError",
    "RECONSTRUCTION_LABEL",
    "build_conference_artifacts",
    "conference_json",
    "conference_slug",
    "render_conference_artifacts",
]
