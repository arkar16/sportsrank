"""Independent semantic validation for static conference artifacts.

The validator consumes freshly derived :class:`ConferenceReference` values and
checks the candidate files against independently assembled expectations.  It
does not import the conference presentation module or recover expected values
from candidate JSON/HTML.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
import html
import json
import math
from pathlib import Path
import re
from statistics import median
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

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

# Keep this mapping explicit and local to the independent validator.  It is
# deliberately not imported from the presentation adapter.
_CONFERENCE_SLUGS = {
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
_EXPECTED_STYLE = (
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


@dataclass(frozen=True)
class _ValidationContext:
    references: tuple[ConferenceReference, ...]
    by_checkpoint: dict[str, ConferenceReference]
    conference_names: tuple[str, ...]
    slugs: dict[str, str]
    default_checkpoint: str
    season: int


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if not value or _SAFE_TEXT.fullmatch(value) is None:
        raise ValueError(f"{field} is unsafe or empty")
    return value


def _strict_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    return value


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _checksum(value: Any, field: str) -> str:
    value = _text(value, field).lower()
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _sequence(value: Any, field: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a sequence")
    return tuple(value)


def _checkpoint_order(value: str) -> tuple[int, int]:
    if value == "PRESEASON":
        return (0, -1)
    if value == "FINAL":
        return (2, 0)
    match = _CHECKPOINT.fullmatch(value)
    if match is None:
        raise ValueError(f"unsupported checkpoint {value!r}")
    return (1, int(match.group(1)))


def _slug(name: str) -> str:
    try:
        return _CONFERENCE_SLUGS[name]
    except KeyError as exc:
        raise ValueError(f"unknown conference source name {name!r}") from exc


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


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


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
    return "Available" if comparison.available else f"Unavailable ({comparison.reason})"


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


def _validate_reference_inputs(values: Sequence[ConferenceReference]) -> _ValidationContext:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("references must be a sequence")
    references = tuple(values)
    if not references:
        raise ValueError("at least one reference is required")
    by_checkpoint: dict[str, ConferenceReference] = {}
    identity: tuple[Any, ...] | None = None
    populations: tuple[Any, ...] | None = None
    names_by_slug: dict[str, set[str]] = {}
    all_conference_names: set[str] = set()
    for index, reference in enumerate(references):
        field = f"reference {index}"
        if type(reference) is not ConferenceReference:
            raise ValueError(f"{field} must be ConferenceReference")
        checkpoint = _text(reference.checkpoint, f"{field} checkpoint")
        _checkpoint_order(checkpoint)
        if checkpoint in by_checkpoint:
            raise ValueError(f"duplicate checkpoint {checkpoint}")
        by_checkpoint[checkpoint] = reference
        phase = _text(reference.phase, f"{field} phase").lower()
        target = reference.target_week
        if checkpoint == "PRESEASON" and (phase != "preseason" or target is not None):
            raise ValueError(f"{field} PRESEASON identity is inconsistent")
        if checkpoint == "FINAL" and (phase != "final" or type(target) is not int or target < 0):
            raise ValueError(f"{field} FINAL identity is inconsistent")
        if checkpoint not in {"PRESEASON", "FINAL"}:
            week = int(checkpoint[1:])
            if phase != "week" or type(target) is not int or target != week:
                raise ValueError(f"{field} week identity is inconsistent")
        season = _strict_int(reference.season, f"{field} season")
        if season < 1:
            raise ValueError(f"{field} season is invalid")
        current_identity = (
            season,
            _checksum(reference.snapshot_checksum, f"{field} snapshot_checksum"),
            _checksum(reference.supplement_checksum, f"{field} supplement_checksum"),
            _text(reference.content_identity, f"{field} content_identity"),
            _text(reference.dataset_id, f"{field} dataset_id"),
            _text(reference.model_version, f"{field} model_version"),
        )
        if identity is None:
            identity = current_identity
        elif current_identity != identity:
            raise ValueError("checkpoint provenance identity is inconsistent")
        standings = _sequence(reference.standings, f"{field} standings")
        if not standings:
            raise ValueError(f"{field} requires standings")
        conference_members: list[tuple[str, tuple[str, ...]]] = []
        standing_names: set[str] = set()
        for standing_index, standing in enumerate(standings):
            if type(standing) is not ConferenceStandings:
                raise ValueError(f"{field} standings {standing_index} is invalid")
            conference = _text(standing.conference, f"{field} conference")
            slug = _slug(conference)
            names_by_slug.setdefault(slug, set()).add(conference)
            if conference in standing_names:
                raise ValueError(f"{field} contains duplicate standings")
            standing_names.add(conference)
            rows = _sequence(standing.rows, f"{field} {conference} rows")
            if not rows:
                raise ValueError(f"{field} {conference} has no members")
            schools: list[str] = []
            for row_index, row in enumerate(rows):
                if type(row) is not StandingsRow:
                    raise ValueError(f"{field} {conference} row {row_index} is invalid")
                if _text(row.conference, "row conference") != conference:
                    raise ValueError(f"{field} row conference identity disagrees")
                school = _text(row.school, "row team")
                if school in schools:
                    raise ValueError(f"{field} contains duplicate teams")
                schools.append(school)
            conference_members.append((conference, tuple(sorted(schools))))
            all_conference_names.add(conference)
        independent_rows = _sequence(reference.independent_rows, f"{field} independent_rows")
        independent_names: list[str] = []
        for row_index, row in enumerate(independent_rows):
            if type(row) is not StandingsRow:
                raise ValueError(f"{field} independent row {row_index} is invalid")
            if _text(row.conference, "independent row conference") != "FBS Independents":
                raise ValueError(f"{field} independent row has the wrong population")
            name = _text(row.school, "independent team")
            if name in independent_names:
                raise ValueError(f"{field} contains duplicate independent teams")
            independent_names.append(name)
        signature = (tuple(sorted(conference_members)), tuple(sorted(independent_names)))
        if populations is None:
            populations = signature
        elif signature != populations:
            raise ValueError("checkpoint population is inconsistent")
        comparisons = _sequence(reference.comparisons, f"{field} comparisons")
        comparison_names: list[str] = []
        for comparison_index, comparison in enumerate(comparisons):
            if type(comparison) is not RatingComparison:
                raise ValueError(f"{field} comparison {comparison_index} is invalid")
            name = _text(comparison.conference, "comparison conference")
            if name == "FBS Independents":
                raise ValueError("comparisons must exclude FBS Independents")
            if name not in standing_names or name in comparison_names:
                raise ValueError(f"{field} comparisons do not cover conferences exactly")
            comparison_names.append(name)
        if set(comparison_names) != standing_names:
            raise ValueError(f"{field} comparisons do not cover conferences exactly")
        projections = _sequence(reference.projections, f"{field} projections")
        projection_names: list[str] = []
        for projection_index, projection in enumerate(projections):
            if type(projection) is not ChampionshipProjection:
                raise ValueError(f"{field} projection {projection_index} is invalid")
            name = _text(projection.conference, "projection conference")
            if name not in standing_names or name in projection_names:
                raise ValueError(f"{field} projections do not cover conferences exactly")
            projection_names.append(name)
        if set(projection_names) != standing_names:
            raise ValueError(f"{field} projections do not cover conferences exactly")
    assert identity is not None
    assert populations is not None
    for slug, names in names_by_slug.items():
        if len(names) > 1:
            raise ValueError(f"conference aliases collide for {slug}: {sorted(names)}")
    ordered_keys = tuple(sorted(by_checkpoint, key=_checkpoint_order))
    conference_names = tuple(
        sorted(all_conference_names, key=lambda name: (_slug(name), name))
    )
    return _ValidationContext(
        references=tuple(by_checkpoint[key] for key in ordered_keys),
        by_checkpoint=by_checkpoint,
        conference_names=conference_names,
        slugs={name: _slug(name) for name in conference_names},
        default_checkpoint="FINAL" if "FINAL" in by_checkpoint else ordered_keys[-1],
        season=identity[0],
    )


def _summary_rows(reference: ConferenceReference) -> list[list[str]]:
    comparisons = sorted(
        reference.comparisons,
        key=lambda item: (
            item.mean is None,
            -(item.mean if item.mean is not None else 0.0),
            item.conference.casefold(),
            item.conference,
        ),
    )
    rows: list[list[str]] = []
    for comparison in comparisons:
        projection = next(
            item for item in reference.projections if item.conference == comparison.conference
        )
        pairing, status, site, reason = _projection_text(projection)
        values = [
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
        ]
        rows.append(
            [
                comparison.conference,
                *values,
                _comparison_state(comparison),
                pairing,
                status,
                site,
                reason,
            ]
        )
    return rows


def _distribution_rows(rows: Sequence[StandingsRow]) -> list[list[str]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            row.cors is None,
            -(row.cors if row.cors is not None else 0.0),
            row.school.casefold(),
            row.school,
        ),
    )
    return [
        [
            row.school,
            _number(row.cors) if row.cors is not None else "Unavailable",
            str(row.national_rank) if row.national_rank is not None else "Unavailable",
        ]
        for row in ordered
    ]


def _standings_rows(standing: ConferenceStandings) -> list[list[str]]:
    return [
        [
            row.school,
            _record_text(row.conference_record),
            _record_text(row.overall_record),
            _number(row.cors) if row.cors is not None else "Unavailable",
            str(row.national_rank) if row.national_rank is not None else "Unavailable",
            str(row.position) if row.position is not None else "Unavailable",
        ]
        for row in standing.rows
    ]


def _interconference_rows(reference: ConferenceReference) -> list[list[str]]:
    records = sorted(
        reference.interconference,
        key=lambda item: (
            item.conference.casefold(),
            item.opponent_kind,
            item.opponent.casefold(),
            item.opponent,
        ),
    )
    return [
        [
            item.conference,
            item.opponent,
            item.opponent_kind.upper(),
            _record_text(item.regular),
            _record_text(item.postseason),
            _record_text(item.unknown),
            _record_text(item.combined),
        ]
        for item in records
    ]


def _projection_rows(reference: ConferenceReference) -> list[list[str]]:
    projections = sorted(
        reference.projections,
        key=lambda item: (item.conference.casefold(), item.conference),
    )
    return [
        [item.conference, *_projection_text(item)]
        for item in projections
    ]


def _table_headers(table: Any) -> list[str]:
    return [cell.get_text(" ", strip=True) for cell in table.select("thead th")]


def _table_rows(table: Any) -> list[list[str]]:
    return [
        [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)]
        for row in table.select("tbody tr")
    ]


def _table_caption(table: Any) -> str:
    caption = table.find("caption", recursive=False)
    return caption.get_text(" ", strip=True) if caption is not None else ""


def _check_table(
    failures: list[str],
    table: Any,
    *,
    caption: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    label: str,
) -> None:
    if _table_caption(table) != caption:
        failures.append(f"{label} caption differs")
    if _table_headers(table) != list(headers):
        failures.append(f"{label} headers differ")
    actual_rows = _table_rows(table)
    expected_rows = [list(row) for row in rows]
    if len(actual_rows) != len(expected_rows):
        failures.append(f"{label} row count differs")
    for index, (actual, expected) in enumerate(zip(actual_rows, expected_rows)):
        if actual != expected:
            failures.append(f"{label} row {index} differs")


def _read_bytes(site: Path, relative: Path, failures: list[str]) -> bytes | None:
    path = site / relative
    if path.is_symlink():
        failures.append(f"conference artifact is a symlink: {relative.as_posix()}")
        return None
    if not path.is_file():
        failures.append(f"missing conference artifact: {relative.as_posix()}")
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        failures.append(f"conference artifact cannot be read: {relative.as_posix()}: {exc}")
        return None
    try:
        assert_public_bytes(relative.as_posix(), raw)
    except (ValueError, TypeError) as exc:
        failures.append(f"public safety rejected {relative.as_posix()}: {exc}")
    return raw


def _check_json(
    site: Path,
    relative: Path,
    context: _ValidationContext,
    failures: list[str],
) -> None:
    raw = _read_bytes(site, relative, failures)
    if raw is None:
        return
    try:
        actual = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        expected = {
            "schema_version": SCHEMA_VERSION,
            "sport": "cfb",
            "classification": "FBS",
            "season": context.season,
            "default_checkpoint": context.default_checkpoint,
            "provenance_class": RECONSTRUCTION_LABEL,
            "checkpoints": [_reference_payload(item) for item in context.references],
        }
        if _canonical_json(actual) != _canonical_json(expected):
            failures.append("conference JSON disagrees with source-derived values or schema")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        failures.append(f"conference JSON is unavailable or malformed: {exc}")


def _reject_url(value: Any) -> bool:
    text = " ".join(str(value).split())
    lowered = text.lower()
    parsed = urlsplit(text)
    return (
        text.startswith("//")
        or bool(parsed.scheme or parsed.netloc)
        or lowered.startswith(("javascript:", "vbscript:", "data:"))
    )


def _common_html(
    site: Path,
    relative: Path,
    title: str,
    heading: str | None,
    context: _ValidationContext,
    reference: ConferenceReference,
    kind: str,
    slug: str | None,
    failures: list[str],
) -> BeautifulSoup | None:
    raw = _read_bytes(site, relative, failures)
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        failures.append(f"{relative.as_posix()} is not UTF-8: {exc}")
        return None
    document = BeautifulSoup(text, "html.parser")
    if document.find(["script", "iframe", "object", "embed", "base"]):
        failures.append(f"{relative.as_posix()} contains executable or embedded markup")
    for node in document.find_all(True):
        if "hidden" in node.attrs or "inert" in node.attrs:
            failures.append(f"{relative.as_posix()} contains hidden or inert content")
        if str(node.get("aria-hidden", "")).strip().lower() == "true":
            failures.append(f"{relative.as_posix()} contains aria-hidden content")
        for attribute, value in node.attrs.items():
            name = attribute.lower()
            if name.startswith("on") or name == "srcdoc":
                failures.append(f"{relative.as_posix()} contains an executable attribute")
            if name in {"href", "src", "action", "formaction", "xlink:href"}:
                values = value if isinstance(value, list) else (value,)
                if any(_reject_url(item) for item in values):
                    failures.append(f"{relative.as_posix()} contains an unsafe URL")
        if node.name == "meta" and str(node.get("http-equiv", "")).lower() == "refresh":
            failures.append(f"{relative.as_posix()} contains a redirect")
    for table in document.find_all("table"):
        if table.find_parent(["details", "template"]) is not None:
            failures.append(
                f"{relative.as_posix()} places a core table in a disclosure or template"
            )
    if re.search(r"(?i)(?:javascript:|vbscript:|data:|https?://)", text):
        failures.append(f"{relative.as_posix()} contains an external or executable URL")
    title_nodes = document.find_all("title")
    if len(title_nodes) != 1 or title_nodes[0].get_text(" ", strip=True) != title:
        failures.append(f"{relative.as_posix()} title differs")
    headings = document.find_all("h1")
    expected_heading = title if heading is None else heading
    if len(headings) != 1 or headings[0].get_text(" ", strip=True) != expected_heading:
        failures.append(f"{relative.as_posix()} heading differs")
    if not any(
        item.get_text(" ", strip=True) == "Historical reconstruction"
        for item in document.find_all("p")
    ):
        failures.append(f"{relative.as_posix()} reconstruction label is missing")
    viewport = document.find_all("meta", attrs={"name": "viewport"})
    if len(viewport) != 1 or viewport[0].get("content") != "width=device-width, initial-scale=1":
        failures.append(f"{relative.as_posix()} viewport differs")
    style_nodes = document.find_all("style")
    if len(style_nodes) != 1 or style_nodes[0].get_text() != _EXPECTED_STYLE:
        failures.append(f"{relative.as_posix()} stylesheet differs")
    if document.find_all(style=True):
        failures.append(f"{relative.as_posix()} contains inline styles")
    if document.find("link"):
        failures.append(f"{relative.as_posix()} contains external stylesheet markup")
    style_text = style_nodes[0].get_text() if len(style_nodes) == 1 else ""
    for marker in ("overflow:auto", "position:sticky"):
        if marker not in style_text:
            failures.append(f"{relative.as_posix()} responsive table style is missing")
    _check_navigation(document, context, reference, kind, slug, relative, failures)
    _check_provenance(document, reference, relative, failures)
    return document


def _expected_navigation(
    context: _ValidationContext,
    reference: ConferenceReference,
    kind: str,
    slug: str | None,
) -> list[tuple[str, str]]:
    key = reference.checkpoint
    nested = kind == "standings"
    prefix = "../" if nested else "./"
    outer = "../../" if nested else "../"
    links = [
        ("Season", f"{outer}{context.season}_CFB.html"),
        ("Ranking progression", f"{outer}history/{context.season}_FBS_progression.html"),
        ("Conference overview", f"{prefix}{context.season}_FBS_conferences.html"),
        ("Conference data", f"{prefix}{context.season}_FBS_conferences.json"),
        ("Comparison", f"{prefix}{key}_FBS_comparison.html"),
    ]
    if nested and slug is None:
        raise ValueError("standings navigation requires a slug")
    for checkpoint_reference in context.references:
        checkpoint = checkpoint_reference.checkpoint
        target = (
            f"./{checkpoint}_FBS_standings.html"
            if nested
            else f"{prefix}{checkpoint}_FBS_comparison.html"
        )
        links.append((checkpoint, target))
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
        links.append((f"{name} standings", target))
    return links


def _check_navigation(
    document: BeautifulSoup,
    context: _ValidationContext,
    reference: ConferenceReference,
    kind: str,
    slug: str | None,
    relative: Path,
    failures: list[str],
) -> None:
    try:
        expected = _expected_navigation(context, reference, kind, slug)
    except ValueError as exc:
        failures.append(f"{relative.as_posix()} navigation cannot be specified: {exc}")
        return
    navigation = document.find("nav", attrs={"aria-label": "Conference reference navigation"})
    if navigation is None:
        failures.append(f"{relative.as_posix()} conference navigation is missing")
        return
    anchors = navigation.find_all("a")
    actual = [
        (anchor.get_text(" ", strip=True), str(anchor.get("href")))
        for anchor in anchors
    ]
    if actual != expected:
        failures.append(f"{relative.as_posix()} navigation is incomplete or reordered")
    current = [
        (anchor.get_text(" ", strip=True), str(anchor.get("href")))
        for anchor in anchors
        if anchor.get("aria-current") is not None
    ]
    if kind == "overview":
        expected_current = [("Conference overview", f"./{context.season}_FBS_conferences.html")]
    elif kind == "comparison":
        expected_current = [(reference.checkpoint, f"./{reference.checkpoint}_FBS_comparison.html")]
    else:
        expected_current = [(reference.checkpoint, f"./{reference.checkpoint}_FBS_standings.html")]
    if current != expected_current:
        failures.append(f"{relative.as_posix()} aria-current navigation differs")


def _check_provenance(
    document: BeautifulSoup,
    reference: ConferenceReference,
    relative: Path,
    failures: list[str],
) -> None:
    cutoff = reference.cutoff.isoformat() if reference.cutoff is not None else "Unavailable"
    expected = [
        ("Status", RECONSTRUCTION_LABEL),
        ("Checkpoint", reference.checkpoint),
        ("Cutoff", cutoff),
        ("Source snapshot", reference.snapshot_checksum),
        ("Conference supplement", reference.supplement_checksum),
        ("Content identity", reference.content_identity),
        ("Dataset", reference.dataset_id),
        ("Model", reference.model_version),
    ]
    details = document.find_all("details")
    if len(details) != 1:
        failures.append(f"{relative.as_posix()} provenance disclosure count differs")
        return
    summary = details[0].find("summary", recursive=False)
    if summary is None or summary.get_text(" ", strip=True) != "Data provenance":
        failures.append(f"{relative.as_posix()} provenance heading differs")
    definition = details[0].find("dl", recursive=False)
    actual: list[tuple[str, str]] = []
    if definition is not None:
        terms = definition.find_all("dt", recursive=False)
        descriptions = definition.find_all("dd", recursive=False)
        if len(terms) != len(descriptions):
            failures.append(f"{relative.as_posix()} provenance fields are malformed")
        actual = [
            (term.get_text(" ", strip=True), description.get_text(" ", strip=True))
            for term, description in zip(terms, descriptions)
        ]
    if actual != expected:
        failures.append(f"{relative.as_posix()} provenance differs")


def _find_conference_standings(
    reference: ConferenceReference, conference: str
) -> ConferenceStandings:
    for item in reference.standings:
        if item.conference == conference:
            return item
    raise ValueError(f"missing standings for {conference}")


def _find_projection(reference: ConferenceReference, conference: str) -> ChampionshipProjection:
    for item in reference.projections:
        if item.conference == conference:
            return item
    raise ValueError(f"missing projection for {conference}")


def _find_comparison(reference: ConferenceReference, conference: str) -> RatingComparison:
    for item in reference.comparisons:
        if item.conference == conference:
            return item
    raise ValueError(f"missing comparison for {conference}")


def _validate_overview(
    document: BeautifulSoup,
    reference: ConferenceReference,
    failures: list[str],
    relative: Path,
) -> None:
    tables = document.find_all("table")
    if len(tables) != 2:
        failures.append(f"{relative.as_posix()} overview table count differs")
        return
    _check_table(
        failures,
        tables[0],
        caption="Conference strength and projected championship",
        headers=(
            "Conference", "Teams", "Mean CORS", "Median CORS", "Top quarter count",
            "Top quarter mean", "Remainder count", "Remainder mean", "Top gap",
            "Distribution", "Pairing", "Status", "Site", "Reason",
        ),
        rows=_summary_rows(reference),
        label=f"{relative.as_posix()} summary",
    )
    _check_table(
        failures,
        tables[1],
        caption="Championship projection state",
        headers=("Conference", "Pairing or contenders", "Status", "Site treatment", "Reason"),
        rows=_projection_rows(reference),
        label=f"{relative.as_posix()} projections",
    )


def _validate_comparison(
    document: BeautifulSoup,
    context: _ValidationContext,
    reference: ConferenceReference,
    failures: list[str],
    relative: Path,
) -> None:
    expected_sections = list(context.conference_names)
    if reference.independent_rows:
        expected_sections.append("FBS Independents")
    sections = document.find_all("section", recursive=False)
    # Sections are nested in the body, so recursive=False on the document is
    # intentionally not used; only the section headings identify the blocks.
    sections = document.find_all("section")
    actual_section_names = [
        heading.get_text(" ", strip=True)
        for section in sections
        for heading in section.find_all("h2", recursive=False)
    ]
    expected_section_names = [f"{name} CORS distribution" for name in expected_sections]
    if actual_section_names != expected_section_names:
        failures.append(f"{relative.as_posix()} distribution sections differ")
    for section, name in zip(sections, expected_sections):
        heading = section.find("h2", recursive=False)
        state = section.find("p", recursive=False)
        if heading is None or heading.get_text(" ", strip=True) != f"{name} CORS distribution":
            failures.append(f"{relative.as_posix()} {name} distribution heading differs")
        if name == "FBS Independents":
            rows = reference.independent_rows
            caption = "FBS Independent member ratings"
            label = "FBS Independent CORS distribution"
        else:
            comparison = _find_comparison(reference, name)
            rows = _find_conference_standings(reference, name).rows
            caption = f"{name} member ratings"
            label = f"{name} CORS distribution"
            if state is None or state.get_text(" ", strip=True) != _comparison_state(comparison):
                failures.append(f"{relative.as_posix()} {name} distribution state differs")
        table = section.find("table")
        if table is None:
            failures.append(f"{relative.as_posix()} {name} distribution table is missing")
            continue
        _check_table(
            failures,
            table,
            caption=caption,
            headers=("Team", "CORS points", "National CORS rank"),
            rows=_distribution_rows(rows),
            label=f"{relative.as_posix()} {label}",
        )
    tables = document.find_all("table")
    expected_table_count = 1 + len(expected_sections) + 2
    if len(tables) != expected_table_count:
        failures.append(f"{relative.as_posix()} comparison table count differs")
        return
    _check_table(
        failures,
        tables[0],
        caption="Conference strength and projected championship",
        headers=(
            "Conference", "Teams", "Mean CORS", "Median CORS", "Top quarter count",
            "Top quarter mean", "Remainder count", "Remainder mean", "Top gap",
            "Distribution", "Pairing", "Status", "Site", "Reason",
        ),
        rows=_summary_rows(reference),
        label=f"{relative.as_posix()} summary",
    )
    _check_table(
        failures,
        tables[-2],
        caption="Interconference records",
        headers=(
            "Conference", "Opponent", "Type", "Regular season", "Postseason",
            "Unknown phase", "Combined",
        ),
        rows=_interconference_rows(reference),
        label=f"{relative.as_posix()} interconference",
    )
    _check_table(
        failures,
        tables[-1],
        caption="Championship projection state",
        headers=("Conference", "Pairing or contenders", "Status", "Site treatment", "Reason"),
        rows=_projection_rows(reference),
        label=f"{relative.as_posix()} projections",
    )


def _validate_standings_page(
    document: BeautifulSoup,
    reference: ConferenceReference,
    conference: str,
    failures: list[str],
    relative: Path,
) -> None:
    standing = _find_conference_standings(reference, conference)
    projection = _find_projection(reference, conference)
    tables = document.find_all("table")
    if len(tables) != 2:
        failures.append(f"{relative.as_posix()} standings table count differs")
        return
    _check_table(
        failures,
        tables[0],
        caption=f"{conference} standings",
        headers=(
            "Team", "Conference record", "Overall record", "CORS points",
            "National CORS rank", "Position",
        ),
        rows=_standings_rows(standing),
        label=f"{relative.as_posix()} standings",
    )
    _check_table(
        failures,
        tables[1],
        caption="Championship projection",
        headers=("Conference", "Pairing or contenders", "Status", "Site treatment", "Reason"),
        rows=[[conference, *_projection_text(projection)]],
        label=f"{relative.as_posix()} projection",
    )


def _expected_paths(context: _ValidationContext) -> set[Path]:
    root = Path("cfb") / "years" / str(context.season) / "conferences"
    paths = {
        root / f"{context.season}_FBS_conferences.html",
        root / f"{context.season}_FBS_conferences.json",
    }
    for reference in context.references:
        checkpoint = reference.checkpoint
        paths.add(root / f"{checkpoint}_FBS_comparison.html")
        for conference in context.conference_names:
            paths.add(root / context.slugs[conference] / f"{checkpoint}_FBS_standings.html")
    return paths


def _reject_extra_paths(
    site: Path,
    expected: set[Path],
    season: int,
    failures: list[str],
) -> None:
    root = site / Path("cfb") / "years" / str(season) / "conferences"
    if not root.exists():
        return
    expected_posix = {item.as_posix() for item in expected}
    try:
        actual = {
            path.relative_to(site).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        }
    except OSError as exc:
        failures.append(f"conference artifact inventory failed: {exc}")
        return
    for relative in sorted(actual - expected_posix):
        failures.append(f"unexpected conference artifact: {relative}")


def validate_conference_artifacts(
    site: Path,
    references: Sequence[ConferenceReference],
) -> list[str]:
    """Return independent semantic failures for the frozen conference graph.

    ``references`` must be freshly derived, source-bound values supplied by the
    caller.  Candidate JSON and HTML are never used as expected-value inputs.
    """

    failures: list[str] = []
    try:
        context = _validate_reference_inputs(references)
        site = Path(site)
        expected = _expected_paths(context)
        _reject_extra_paths(site, expected, context.season, failures)
        root = Path("cfb") / "years" / str(context.season) / "conferences"
        json_relative = root / f"{context.season}_FBS_conferences.json"
        _check_json(site, json_relative, context, failures)

        overview_reference = context.by_checkpoint[context.default_checkpoint]
        overview_relative = root / f"{context.season}_FBS_conferences.html"
        overview_title = f"{context.season} FBS conference reference"
        document = _common_html(
            site,
            overview_relative,
            overview_title,
            f"{context.season} FBS conference reference — {overview_reference.checkpoint}",
            context,
            overview_reference,
            "overview",
            None,
            failures,
        )
        if document is not None:
            _validate_overview(document, overview_reference, failures, overview_relative)

        for reference in context.references:
            checkpoint = reference.checkpoint
            comparison_relative = root / f"{checkpoint}_FBS_comparison.html"
            comparison_title = f"{checkpoint} conference comparison — {context.season} FBS"
            document = _common_html(
                site,
                comparison_relative,
                comparison_title,
                None,
                context,
                reference,
                "comparison",
                None,
                failures,
            )
            if document is not None:
                _validate_comparison(document, context, reference, failures, comparison_relative)
            for conference in context.conference_names:
                slug = context.slugs[conference]
                standings_relative = root / slug / f"{checkpoint}_FBS_standings.html"
                standings_title = f"{conference} standings — {checkpoint} — {context.season} FBS"
                document = _common_html(
                    site,
                    standings_relative,
                    standings_title,
                    None,
                    context,
                    reference,
                    "standings",
                    slug,
                    failures,
                )
                if document is not None:
                    _validate_standings_page(
                        document,
                        reference,
                        conference,
                        failures,
                        standings_relative,
                    )
    except (OSError, TypeError, ValueError, KeyError, AttributeError) as exc:
        failures.append(f"conference validation could not derive its expected graph: {exc}")
    return failures


__all__ = ["validate_conference_artifacts"]
