"""Offline static Release generation, validation, and local promotion.

This module deliberately has no CFBD or Firebase dependency.  A caller fetches
one :class:`SeasonSnapshot` (or loads it through ``load_cached``), then passes
that immutable value to :func:`build_release`.  The candidate is rendered in
``<release>/site`` and is only made public through the explicit
:func:`promote_release` gate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urldefrag, urlparse
from io import StringIO

from bs4 import BeautifulSoup
import pandas as pd

try:
    from .carryover_registry import reconcile_previous_final
    from .ranking_engine import (
        HFA,
        MODEL_VERSION,
        PreviousFinal,
        completed_games,
        final_ranking,
        load_previous_final_model,
        preseason_ranking,
        records_for_week,
        scheduled_games,
        scheduled_season_end_week,
        season_is_complete,
        season_rankings,
        spreads_for_week,
        validate_previous_final,
    )
    from .season_snapshot import (
        SeasonSnapshot,
        _checksum as _snapshot_checksum,
        _complete_through as _authoritative_complete_through,
        _legacy_checksum as _legacy_snapshot_checksum,
    )
    from .season_source import SourceGame, SourceTeam, is_explicit_non_played
    from .week_calendar import calendar_provenance, canonical_week
except ImportError:  # Direct execution from the cfb directory.
    from carryover_registry import reconcile_previous_final
    from ranking_engine import (
        HFA,
        MODEL_VERSION,
        PreviousFinal,
        completed_games,
        final_ranking,
        load_previous_final_model,
        preseason_ranking,
        records_for_week,
        scheduled_games,
        scheduled_season_end_week,
        season_is_complete,
        season_rankings,
        spreads_for_week,
        validate_previous_final,
    )
    from season_snapshot import (
        SeasonSnapshot,
        _checksum as _snapshot_checksum,
        _complete_through as _authoritative_complete_through,
        _legacy_checksum as _legacy_snapshot_checksum,
    )
    from season_source import SourceGame, SourceTeam, is_explicit_non_played
    from week_calendar import calendar_provenance, canonical_week


LAST_UPDATED_RE = re.compile(r"Last updated:\s*([^<\n]+)")
PHASES = frozenset({"preseason", "week", "final"})
RELEASE_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        mode = "wb" if isinstance(content, bytes) else "w"
        with os.fdopen(fd, mode, encoding=None if mode == "wb" else "utf-8") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _site_for(candidate: str | Path | "Release") -> Path:
    if isinstance(candidate, Release):
        return candidate.site
    path = Path(candidate)
    if (path / "site").is_dir():
        return path / "site"
    return path


def _canonical_release_id(value: Any) -> str | None:
    if not isinstance(value, str) or not RELEASE_ID_RE.fullmatch(value):
        return None
    if value in {".", "..", "site"}:
        return None
    return value


def _observable_candidate_root(candidate: str | Path | "Release") -> Path | None:
    """Return the immutable candidate root when the caller still has one.

    Promoted sites intentionally return ``None``: their public directory name
    (normally ``website``) is not the original Release ID. The copied
    ``release_root_name`` remains the stable pre-promotion binding.
    """

    if isinstance(candidate, Release):
        return candidate.root
    path = Path(candidate)
    if (path / "site").is_dir():
        return path
    if path.name == "site":
        return path.parent
    return None


def _row_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str] | None = None) -> str:
    if columns is None:
        columns = tuple(rows[0].keys()) if rows else ()
    head = "".join(f"<th>{html.escape(str(column))}</th>" for column in columns)
    body_parts: list[str] = []
    for row in rows:
        cells = "".join(
            f"<td>{html.escape(_format_value(row.get(column)))}</td>"
            for column in columns
        )
        body_parts.append(f"<tr>{cells}</tr>")
    return (
        '<table border="1" class="dataframe">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body_parts)}</tbody></table>"
    )


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _history_kind_from_filename(filename: str) -> str | None:
    name = Path(str(filename)).name.lower()
    if name.startswith("nc_"):
        return "National champion"
    if name.startswith("wt_"):
        return "Worst team"
    return None


def _history_field_name(value: Any) -> str | None:
    normalized = re.sub(r"[^a-z0-9]", "", str(value).strip().lower())
    return {
        "year": "year",
        "school": "school",
        "conference": "conference",
        "record": "record",
        "win": "win_pct",
        "winpct": "win_pct",
        "winpercent": "win_pct",
        "winpercentage": "win_pct",
        "cors": "cors",
        "kind": "kind",
    }.get(normalized)


def _canonical_history_row(row: Mapping[str, Any], filename: str | None = None) -> dict[str, Any]:
    """Normalize current and legacy history headers into one row shape."""

    canonical: dict[str, Any] = {}
    for raw_name, value in row.items():
        field_name = _history_field_name(raw_name)
        if field_name is None or field_name in canonical:
            continue
        try:
            if bool(pd.isna(value)):
                value = None
        except (TypeError, ValueError):
            # History cells are scalar in the supported HTML schemas.  Leave
            # any non-scalar extension untouched for the ordinary comparator.
            pass
        canonical[field_name] = value
    if not canonical.get("kind") and filename:
        derived_kind = _history_kind_from_filename(filename)
        if derived_kind:
            canonical["kind"] = derived_kind
    return canonical


def _history_identity(row: Mapping[str, Any], filename: str | None = None) -> tuple[tuple[int, str], dict[str, Any]]:
    """Return the canonical ``(season, school)`` identity and row values."""

    canonical = _canonical_history_row(row, filename)
    raw_year = canonical.get("year")
    year_text = re.sub(r"[^0-9]", "", str(raw_year or ""))
    return (int(year_text), str(canonical.get("school", ""))), canonical


def _history_fields_match(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    fields: Sequence[str],
) -> bool:
    """Compare supplied canonical history fields without dropping aliases."""

    for field_name in fields:
        if field_name not in expected:
            continue
        if field_name not in actual or not _same_value(actual[field_name], expected[field_name]):
            return False
    return True


def _page(title: str, timestamp: str, body: str, links: Sequence[tuple[str, str]] = ()) -> str:
    navigation = "".join(
        f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a> | '
        for label, href in links
    )
    return (
        "<!doctype html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n"
        "</head>\n<body>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        f"<p>{navigation}</p>\n"
        f"<p>Last updated: {html.escape(timestamp)}</p>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


def _snapshot_payload(snapshot: SeasonSnapshot) -> dict[str, Any]:
    return {
        "sport": snapshot.sport,
        "classification": snapshot.classification,
        "year": snapshot.year,
        "teams": [asdict(team) for team in snapshot.teams],
        "games": [asdict(game) for game in snapshot.games],
        "metadata": dict(snapshot.metadata),
        "checksum": snapshot.checksum,
    }


def _snapshot_from_payload(value: Mapping[str, Any]) -> SeasonSnapshot:
    from types import MappingProxyType

    metadata_value = value.get("metadata")
    if isinstance(metadata_value, Mapping) and metadata_value:
        metadata = dict(metadata_value)
    else:
        # Cached v3 snapshots store these fields at the document root, while
        # Release archives keep the public nested metadata object.  Normalize
        # both shapes before independently recomputing their checksum.
        metadata = {
            key: value[key]
            for key in (
                "schema_version",
                "teams_fetched_at",
                "games_fetched_at",
                "complete_through_week",
            )
            if key in value
        }

    return SeasonSnapshot(
        sport=str(value.get("sport", "cfb")),
        classification=str(value["classification"]),
        year=int(value["year"]),
        teams=tuple(SourceTeam(**team) for team in value.get("teams", [])),
        games=tuple(SourceGame(**game) for game in value.get("games", [])),
        metadata=MappingProxyType(metadata),
        checksum=str(value["checksum"]),
    )


def _snapshot_uses_provider_metadata(snapshot: SeasonSnapshot) -> bool:
    """Return whether the snapshot carries the v3 raw provider week field."""

    return any(game.provider_week is not None for game in snapshot.games)


def _validate_snapshot_week_provenance(snapshot: SeasonSnapshot) -> bool:
    """Validate every raw provider week against the canonical calendar.

    A snapshot made from the repository's canonical fixtures has no raw
    provider week and remains compatible.  Once any game carries provider
    metadata, every game must carry the complete raw tuple so a partial CFBD
    response cannot quietly produce a mixed mapping.
    """

    provider_games = [game for game in snapshot.games if game.provider_week is not None]
    if not provider_games:
        return False
    if len(provider_games) != len(snapshot.games):
        raise ValueError("snapshot provider metadata is mixed across games")
    for game in snapshot.games:
        raw_week = game.provider_week
        if isinstance(raw_week, bool) or not isinstance(raw_week, int) or raw_week < 0:
            raise ValueError("snapshot provider_week must be a non-negative integer")
        if not isinstance(game.provider_id, str) or not game.provider_id.strip():
            raise ValueError("snapshot provider metadata is missing provider_id")
        if not isinstance(game.date, str) or not game.date.strip():
            raise ValueError("snapshot provider metadata is missing date")
        try:
            expected_week = canonical_week(snapshot.year, raw_week, game.date)
        except ValueError as exc:
            raise ValueError("snapshot provider calendar metadata is invalid") from exc
        if int(game.week) != int(expected_week):
            raise ValueError("snapshot canonical week does not match provider metadata")
    return True


def _snapshot_week_calendar(snapshot: SeasonSnapshot) -> dict[str, object] | None:
    """Return derived calendar evidence for provider-backed snapshots."""

    if not _validate_snapshot_week_provenance(snapshot):
        return None
    return calendar_provenance(snapshot.year)


def _previous_model(
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None,
) -> PreviousFinal:
    if isinstance(previous_final, PreviousFinal):
        return previous_final
    if isinstance(previous_final, (str, Path)):
        return load_previous_final_model(previous_final)
    return PreviousFinal(cors=dict(previous_final or {}), wins_vs_expected={})


def _resolve_previous_final(
    year: int,
    published_site: str | Path | None,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None,
    classification: str,
    expected_teams: set[str],
) -> tuple[PreviousFinal, dict[str, Any]]:
    evidence: dict[str, Any] = {
        "prior_season": year - 1 if year > 1897 else None,
        "classification": classification.upper(),
        "entrants": [],
        "identity_repairs": [],
    }
    if published_site is not None and year > 1897:
        root = _site_for(published_site)
        candidate = root / "cfb" / "years" / str(year - 1) / "rankings" / f"{year - 1}_FINAL_{classification.upper()}_cors.html"
        if candidate.exists():
            derived = load_previous_final_model(candidate)
            reconciliation = reconcile_previous_final(
                year, classification, derived.cors, derived.wins_vs_expected, expected_teams
            )
            evidence.update(reconciliation.evidence())
            derived = PreviousFinal(
                reconciliation.cors,
                reconciliation.wins_vs_expected,
                year=derived.year,
                classification=derived.classification,
            )
            if previous_final is not None:
                supplied = _previous_model(previous_final)
                schools = set(derived.cors)
                supplied_wve = {school: float(supplied.wins_vs_expected.get(school, 0.0)) for school in schools}
                derived_wve = {school: float(derived.wins_vs_expected.get(school, 0.0)) for school in schools}
                if dict(supplied.cors) != dict(derived.cors) or supplied_wve != derived_wve:
                    raise ValueError("supplied previous_final does not match the actual base FINAL")
            return derived, evidence
        if previous_final is not None:
            raise ValueError(
                f"required preceding FINAL is missing: {candidate.relative_to(root)}"
            )
    if previous_final is not None:
        return _previous_model(previous_final), evidence
    return PreviousFinal(cors={}, wins_vs_expected={}), evidence


@dataclass(frozen=True)
class Release:
    """A generated candidate Release and its machine-readable manifest."""

    release_id: str
    root: Path
    site: Path
    manifest_path: Path
    metadata_path: Path
    target_week: int
    owned_artifacts: tuple[str, ...]
    scheduled_end_week: int = 0
    season_complete: bool = False
    phase: str = "week"
    base_site: Path | None = None

    @property
    def path(self) -> Path:
        return self.site


@dataclass(frozen=True)
class ValidationFailure:
    code: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        prefix = f"{self.code}: "
        return prefix + self.message + (f" ({self.path})" if self.path else "")


@dataclass
class ValidationReport:
    valid: bool
    failures: list[ValidationFailure] = field(default_factory=list)
    legacy_failures: list[ValidationFailure] = field(default_factory=list)
    checked_artifacts: list[str] = field(default_factory=list)
    site: Path | None = None

    @property
    def ok(self) -> bool:
        return self.valid and not self.failures

    @property
    def is_valid(self) -> bool:
        return self.ok

    @property
    def errors(self) -> list[ValidationFailure]:
        return self.failures

    def raise_for_failure(self) -> "ValidationReport":
        if not self.ok:
            detail = "; ".join(str(failure) for failure in self.failures[:8])
            raise ReleaseValidationError(detail or "release validation failed", self)
        return self


class ReleaseValidationError(RuntimeError):
    def __init__(self, message: str, report: ValidationReport | None = None):
        super().__init__(message)
        self.report = report


class ReleaseValidator:
    """Object facade for callers that keep a validator dependency."""

    def validate(self, candidate: str | Path | Release, *, published_site: str | Path | None = None) -> ValidationReport:
        return validate_release(candidate, published_site=published_site)

    def validate_or_raise(self, candidate: str | Path | Release, *, published_site: str | Path | None = None) -> ValidationReport:
        return self.validate(candidate, published_site=published_site).raise_for_failure()


@dataclass(frozen=True)
class PromotionResult:
    changed: bool
    target: Path
    backup: Path | None = None
    reason: str = "promoted"
    added: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()


def _public_files(root: Path) -> dict[str, str]:
    """Return the byte identity of every public file below ``root``."""

    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _valid_published_site(value: str | Path | None) -> Path:
    if value is None:
        raise ValueError("published_site is required for an immutable full-site overlay")
    site = _site_for(value)
    if not site.is_dir() or not any(site.rglob("*")):
        raise ValueError("published_site must be an existing non-empty directory")
    return site


def _phase_target(snapshot: SeasonSnapshot, phase: str | None, target_week: int | None) -> tuple[str, int]:
    if phase is None:
        raise ValueError("phase is required: preseason, week, or final")
    phase = str(phase).lower()
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {sorted(PHASES)}")
    if phase == "preseason":
        if target_week not in (None, -1):
            raise ValueError("preseason does not accept a numbered target_week")
        return phase, -1
    if target_week is None:
        target_week = snapshot.complete_through_week
    target_week = int(target_week)
    if target_week < 0:
        raise ValueError("week/final target_week must be non-negative")
    if target_week > snapshot.complete_through_week:
        raise ValueError("target_week cannot exceed snapshot complete_through_week")
    if phase == "final" and (not season_is_complete(snapshot) or target_week < scheduled_season_end_week(snapshot)):
        raise ValueError("final requires a complete historical snapshot through its scheduled end")
    return phase, target_week


def _expected_artifacts(snapshot: SeasonSnapshot, phase: str, target_week: int) -> set[str]:
    """Derive the owned graph from run inputs and canonical URL rules."""

    year = snapshot.year
    cls = snapshot.classification.upper()
    base = f"cfb/years/{year}"
    paths = {
        "cfb/cfb.html",
        f"cfb/history/nc_{cls}_CFB_output.html",
        f"cfb/history/wt_{cls}_CFB_output.html",
        f"cfb/years/history/nc_{cls}_CFB_output.html",
        f"cfb/years/history/wt_{cls}_CFB_output.html",
        f"{base}/{year}_CFB.html",
        f"{base}/data/snapshot.json",
        _snapshot_archive_relative(snapshot),
        f"{base}/metadata.json",
        "release.json",
    }
    if phase == "preseason":
        paths.update({
            f"{base}/rankings/{year}_PRESEASON_{cls}_cors.html",
            f"{base}/data/slate/{year}_{cls}_slate.html",
            f"{base}/data/slate/weekly_slate/{year}_W0_{cls}_slate.html",
            f"{base}/spread/{year}_W0_{cls}_spread.html",
        })
        return paths
    # Every numbered checkpoint carries the season-level PRESEASON forecast
    # used to seed and independently validate Week 0 spreads.
    paths.add(f"{base}/rankings/{year}_PRESEASON_{cls}_cors.html")
    if phase == "final":
        # A historical FINAL overlay publishes the carryover checkpoint and
        # the forecast used to seed scored Week 0 as part of the same graph.
        paths.update({
            f"{base}/rankings/{year}_PRESEASON_{cls}_cors.html",
            f"{base}/spread/{year}_W0_{cls}_spread.html",
        })
    paths.update({
        f"{base}/data/results/{year}_{cls}_results.html",
        f"{base}/data/slate/{year}_{cls}_slate.html",
    })
    for week in range(target_week + 1):
        paths.update({
            f"{base}/rankings/{year}_W{week}_{cls}_cors.html",
            f"{base}/data/records/{year}_W{week}_{cls}_records.html",
            f"{base}/data/results/weekly_results/{year}_W{week}_{cls}_results.html",
            f"{base}/data/slate/weekly_slate/{year}_W{week}_{cls}_slate.html",
        })
        paths.update({
            f"{base}/spread/{year}_W{week}_{cls}_spread.html",
            f"{base}/spread/{year}_W{week}_{cls}_spread_results.html",
        })
    if phase == "week":
        next_week = _next_scheduled_week(snapshot, target_week)
        if next_week is not None:
            paths.update({
                f"{base}/spread/{year}_W{next_week}_{cls}_spread.html",
                f"{base}/data/slate/weekly_slate/{year}_W{next_week}_{cls}_slate.html",
            })
    if phase == "final":
        paths.add(f"{base}/rankings/{year}_FINAL_{cls}_cors.html")
    return paths


def _next_scheduled_week(snapshot: SeasonSnapshot, target_week: int) -> int | None:
    """Return the first active scheduled week after a numbered checkpoint."""

    return min(
        (
            int(game.week)
            for game in snapshot.games
            if int(game.week) > int(target_week)
            and not is_explicit_non_played(game)
        ),
        default=None,
    )


def _canonical_snapshot_checksum(snapshot: SeasonSnapshot) -> str:
    state = {
        "schema_version": snapshot.metadata.get("schema_version", 2),
        "sport": snapshot.sport,
        "classification": snapshot.classification,
        "year": snapshot.year,
        "teams_fetched_at": snapshot.metadata.get("teams_fetched_at"),
        "games_fetched_at": snapshot.metadata.get("games_fetched_at"),
        "complete_through_week": snapshot.metadata.get("complete_through_week", -1),
    }
    if int(state["schema_version"] or 1) == 1:
        return _legacy_snapshot_checksum(snapshot.year, snapshot.classification, snapshot.teams, snapshot.games)
    return _snapshot_checksum(state, snapshot.teams, snapshot.games)


def _snapshot_archive_relative(snapshot: SeasonSnapshot) -> str:
    """Return the immutable, content-addressed archive path for a run input.

    ``data/snapshot.json`` remains the compatible current-checkpoint path.  A
    cumulative Release also seals every checkpoint under its snapshot digest,
    so a later same-season run cannot overwrite the evidence used by an older
    run.
    """

    checksum = str(snapshot.checksum)
    if not checksum or "/" in checksum or "\\" in checksum or checksum in {".", ".."}:
        raise ValueError("snapshot checksum cannot form a safe archive path")
    return f"cfb/years/{snapshot.year}/data/snapshots/{checksum}.json"


def _checkpoint_order(phase: str, target_week: int) -> tuple[int, int]:
    """Order checkpoints within one Season for cumulative run validation."""

    normalized = str(phase).lower()
    if normalized == "preseason":
        return (0, -1)
    if normalized == "week":
        return (1, int(target_week))
    if normalized == "final":
        return (2, int(target_week))
    raise ValueError(f"unknown checkpoint phase: {phase}")


def _prior_final_with_evidence(
    snapshot: SeasonSnapshot, tree: Path
) -> tuple[PreviousFinal, dict[str, Any]]:
    evidence: dict[str, Any] = {
        "prior_season": snapshot.year - 1 if snapshot.year > 1897 else None,
        "classification": snapshot.classification.upper(),
        "entrants": [],
        "identity_repairs": [],
    }
    if snapshot.year == 1897:
        return validate_previous_final(snapshot, None, require=False), evidence
    path = (
        tree / "cfb" / "years" / str(snapshot.year - 1) / "rankings"
        / f"{snapshot.year - 1}_FINAL_{snapshot.classification.upper()}_cors.html"
    )
    if not path.is_file():
        raise ValueError(f"required preceding FINAL is missing: {path.relative_to(tree)}")
    loaded = load_previous_final_model(path)
    reconciliation = reconcile_previous_final(
        snapshot.year,
        snapshot.classification,
        loaded.cors,
        loaded.wins_vs_expected,
        {team.school for team in snapshot.teams},
    )
    evidence.update(reconciliation.evidence())
    return validate_previous_final(
        snapshot,
        PreviousFinal(
            reconciliation.cors,
            reconciliation.wins_vs_expected,
            year=loaded.year,
            classification=loaded.classification,
        ),
    ), evidence


def _prior_final_from_tree(snapshot: SeasonSnapshot, tree: Path) -> PreviousFinal:
    return _prior_final_with_evidence(snapshot, tree)[0]


def grade_ats(
    *,
    home_team: str,
    away_team: str,
    home_points: float | int | None,
    away_points: float | int | None,
    home_margin_line: float | int | None,
) -> dict[str, Any]:
    """Grade a home-oriented model line without losing side orientation.

    ``home_margin_line`` is the predicted home score minus away score.  A
    missing/non-finite line or score is explicitly ungraded; equality is a
    push, including a tied game on a zero line.
    """

    values = (home_points, away_points, home_margin_line)
    try:
        home_score, away_score, line = (float(value) for value in values)
    except (TypeError, ValueError):
        return {"favorite": None, "underdog": None, "ats_result": "ungraded", "ats_correct": None}
    if not all(math.isfinite(value) for value in (home_score, away_score, line)):
        return {"favorite": None, "underdog": None, "ats_result": "ungraded", "ats_correct": None}
    favorite = home_team if line > 0 else away_team if line < 0 else None
    underdog = away_team if line > 0 else home_team if line < 0 else None
    actual = home_score - away_score
    if actual == line:
        result = "push"
        correct: bool | None = None
    elif actual > line:
        result = "home"
        # A pick'em line names no favorite.  Preserve the actual winning side
        # for display, but leave every zero-line pick ungraded.
        correct = None if line == 0 else line >= 0
    else:
        result = "away"
        correct = None if line == 0 else line <= 0
    return {"favorite": favorite, "underdog": underdog, "ats_result": result, "ats_correct": correct}


class ReleaseBuilder:
    """Render one complete candidate tree from one immutable snapshot."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        release_id: str | None = None,
        model_version: str = MODEL_VERSION,
        code_revision: str = "working-tree",
        timestamp: str | None = None,
        published_site: str | Path | None = None,
        clone_published: bool = True,
        hfa: float = HFA,
    ) -> None:
        requested = Path(output_root)
        self.release_id = release_id or requested.name
        if _canonical_release_id(self.release_id) is None:
            raise ValueError("release_id must be a canonical 1-128 character path component")
        if requested.name == "site":
            self.root = requested.parent
            self.site = requested
        elif requested.name == self.release_id:
            self.root = requested
            self.site = requested / "site"
        else:
            self.root = requested / self.release_id
            self.site = self.root / "site"
        if self.root.name != self.release_id:
            raise ValueError("release_id must equal the immutable Release root directory name")
        self.model_version = model_version
        self.code_revision = code_revision
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.published_site = _valid_published_site(published_site)
        if clone_published is False:
            raise ValueError("clone_published=False is not supported; Releases are full-site overlays")
        self.clone_published = True
        self.hfa = float(hfa)

    def build(
        self,
        snapshot: SeasonSnapshot,
        *,
        target_week: int | None = None,
        phase: str | None = None,
        previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    ) -> Release:
        if self.model_version != MODEL_VERSION:
            raise ValueError(f"Unsupported CORS model version: {self.model_version}")
        snapshot_week_calendar = _snapshot_week_calendar(snapshot)
        immediate_base_digest = _tree_digest(self.published_site)
        inherited_runs: list[dict[str, Any]] = []
        inherited_owned: set[str] = set()
        origin_base_digest = immediate_base_digest
        inherited_manifest_path = self.published_site / "manifest.json"
        if inherited_manifest_path.is_file():
            try:
                inherited_manifest = json.loads(inherited_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("Published Site has an unreadable Release manifest") from exc
            if inherited_manifest.get("manifest_version") == 2:
                inherited_runs = [dict(run) for run in inherited_manifest.get("runs", [])]
                inherited_owned = {
                    str(path).replace(os.sep, "/")
                    for path in inherited_manifest.get("owned_artifacts", [])
                }
                origin_base_digest = str(inherited_manifest.get("base_tree_sha256", ""))
                if not origin_base_digest:
                    raise ValueError("Published Site Release manifest has no original base identity")
        phase, target_week = _phase_target(snapshot, phase, target_week)
        complete_through_week = _authoritative_complete_through(snapshot.games)
        scheduled_end = scheduled_season_end_week(snapshot)
        season_complete = phase == "final"
        final_relative = (
            Path("cfb")
            / "years"
            / str(snapshot.year)
            / "rankings"
            / f"{snapshot.year}_FINAL_{snapshot.classification.upper()}_cors.html"
        )
        inherited_final = bool(
            (self.published_site / final_relative).exists()
        )
        previous, carryover_provenance = _resolve_previous_final(
            snapshot.year,
            self.published_site,
            previous_final,
            snapshot.classification,
            {team.school for team in snapshot.teams},
        )

        if self.root.exists():
            raise FileExistsError(f"release directory already exists: {self.root}")

        # An inherited same-year FINAL remains addressable in a cloned
        # Published Site, even when this candidate is an earlier checkpoint.
        # It is not added to ``owned`` or ``required_artifacts`` and therefore
        # cannot be mistaken for a newly generated result.
        previous = validate_previous_final(
            snapshot,
            previous if snapshot.year > 1897 else previous_final,
            require=snapshot.year > 1897,
        )

        # Every scored checkpoint needs the immutable PRESEASON forecast to
        # grade Week 0 ATS results.  Numbered checkpoints render and own that
        # forecast so the graph cannot silently depend on an inherited page.
        preseason_rows: list[dict[str, Any]] = preseason_ranking(
            snapshot, previous, model_version=self.model_version
        )
        if phase == "preseason":
            rankings = {0: preseason_rows or []}
        else:
            rankings = season_rankings(snapshot, target_week, previous, model_version=self.model_version)
            if phase == "final":
                rankings[target_week] = final_ranking(snapshot, previous, model_version=self.model_version)
        self.root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.published_site, self.site)
        classification = snapshot.classification.upper()
        year = snapshot.year
        owned: list[str] = []

        def write(relative: str, content: str | bytes) -> None:
            path = self.site / relative
            _atomic_write(path, content)
            owned.append(relative.replace(os.sep, "/"))

        base = Path("cfb") / "years" / str(year)
        ranking_columns = (
            "rank", "school", "conference", "record", "win_pct", "cors",
            "mov", "sos", "expected_wins", "wins_vs_expected", "wins", "losses", "ties",
        )
        record_columns = ("school", "conference", "record", "win_pct", "wins", "losses", "ties")
        result_columns = (
            "week", "home_team", "home_division", "home_score", "away_team",
            "away_division", "away_score", "neutral_site",
        )
        slate_columns = ("week", "home_team", "home_division", "away_team", "away_division", "neutral_site")
        spread_columns = (
            "week", "home_team", "away_team", "neutral_site", "home_cors",
            "away_cors", "spread_value", "spread",
        )
        spread_result_columns = (
            "week", "home_team", "away_team", "spread", "spread_value",
            "actual_margin", "favorite", "underdog", "ats_result", "ats_correct",
        )

        def game_row(game: SourceGame) -> dict[str, Any]:
            return {
                "week": int(game.week),
                "home_team": game.home_team,
                "home_division": game.home_classification,
                "home_score": game.home_points,
                "away_team": game.away_team,
                "away_division": game.away_classification,
                "away_score": game.away_points,
                "neutral_site": bool(game.neutral_site),
            }

        # Rankings, records, results, and slates are all derived from the same
        # snapshot and use stable numeric/string formatting.
        for week in (() if phase == "preseason" else range(target_week + 1)):
            rows = rankings[week]
            title = f"CORS {self.model_version} - {year} W{week} Rankings - {classification} CFB"
            links: list[tuple[str, str]] = [("Season", f"../{year}_CFB.html")]
            if week:
                links.append(("Previous", f"{year}_W{week - 1}_{classification}_cors.html"))
            if week < target_week:
                links.append(("Next", f"{year}_W{week + 1}_{classification}_cors.html"))
            if season_complete:
                links.append(("Final", f"{year}_FINAL_{classification}_cors.html"))
            write(
                str(base / "rankings" / f"{year}_W{week}_{classification}_cors.html"),
                _page(title, self.timestamp, _row_table(rows, ranking_columns), links),
            )

            records = records_for_week(snapshot, week)
            record_title = f"CORS {self.model_version} - {year} W{week} Records - {classification} CFB"
            write(
                str(base / "data" / "records" / f"{year}_W{week}_{classification}_records.html"),
                _page(record_title, self.timestamp, _row_table(records, record_columns), [("Season", f"../../{year}_CFB.html")]),
            )
            completed = tuple(game for game in snapshot.games if int(game.week) == week and game in completed_games(snapshot, target_week))
            result_rows = [game_row(game) for game in completed]
            result_title = f"CORS {self.model_version} - {year} W{week} Results - {classification} CFB"
            write(
                str(base / "data" / "results" / "weekly_results" / f"{year}_W{week}_{classification}_results.html"),
                _page(result_title, self.timestamp, _row_table(result_rows, result_columns), [("Season", f"../../../{year}_CFB.html")]),
            )
            slate_games = tuple(game for game in scheduled_games(snapshot, target_week) if int(game.week) == week)
            slate_rows = [game_row(game) for game in slate_games]
            slate_title = f"CORS {self.model_version} - {year} W{week} Slate - {classification} CFB"
            write(
                str(base / "data" / "slate" / "weekly_slate" / f"{year}_W{week}_{classification}_slate.html"),
                _page(slate_title, self.timestamp, _row_table(slate_rows, slate_columns), [("Season", f"../../../{year}_CFB.html")]),
            )

        if phase in {"preseason", "week", "final"}:
            assert preseason_rows is not None
            preseason_relative = base / "rankings" / f"{year}_PRESEASON_{classification}_cors.html"
            if phase == "week" and (self.site / preseason_relative).is_file():
                # A numbered overlay preserves an already-published
                # PRESEASON artifact byte-for-byte, while still claiming and
                # validating it as part of the checkpoint graph.  A missing
                # artifact is rendered below from the current snapshot.
                owned.append(preseason_relative.as_posix())
            else:
                write(
                    str(preseason_relative),
                    _page(
                        f"CORS {self.model_version} - {year} Preseason Rankings - {classification} CFB",
                        self.timestamp,
                        _row_table(preseason_rows, ranking_columns),
                        (("Season", f"../{year}_CFB.html"),),
                    ),
                )
        if season_complete:
            final_title = f"CORS {self.model_version} - {year} Final Rankings - {classification} CFB"
            write(
                str(base / "rankings" / f"{year}_FINAL_{classification}_cors.html"),
                _page(final_title, self.timestamp, _row_table(rankings[target_week], ranking_columns), [("Season", f"../{year}_CFB.html")]),
            )

        all_completed = tuple() if phase == "preseason" else tuple(completed_games(snapshot, target_week))
        if phase != "preseason":
            all_results = [game_row(game) for game in all_completed]
            write(
                str(base / "data" / "results" / f"{year}_{classification}_results.html"),
                _page(
                    f"CORS {self.model_version} - {year} Results - {classification} CFB",
                    self.timestamp,
                    _row_table(all_results, result_columns),
                    [("Season", f"../../{year}_CFB.html")],
                ),
            )
        slate_through = 0 if phase == "preseason" else target_week
        all_slate = [game_row(game) for game in scheduled_games(snapshot, slate_through)]
        write(
            str(base / "data" / "slate" / f"{year}_{classification}_slate.html"),
            _page(
                f"CORS {self.model_version} - {year} Slate - {classification} CFB",
                self.timestamp,
                _row_table(all_slate, slate_columns),
                [("Season", f"../../{year}_CFB.html")],
            ),
        )

        # Spreads use the preceding completed ranking.  Week 0 uses the
        # distinct PRESEASON rows, while a next-week forecast uses the target
        # ranking and never reads a future result or ranking.
        next_week = _next_scheduled_week(snapshot, target_week) if phase == "week" else None
        if phase == "preseason":
            spread_weeks = (0,)
        elif phase == "final":
            spread_weeks = (0, *range(1, target_week + 1))
        else:
            spread_weeks = (0, *range(1, target_week + 1)) + ((next_week,) if next_week is not None else ())
        for week in spread_weeks:
            if week == 0:
                assert preseason_rows is not None
                spread_ranking = preseason_rows
            elif next_week is not None and week == next_week and week > target_week:
                spread_ranking = rankings[target_week]
            else:
                spread_ranking = rankings[week - 1]
            spread_rows = spreads_for_week(snapshot, week, spread_ranking, hfa=self.hfa)
            spread_title = f"CORS {self.model_version} - {year} W{week} Spread - {classification} CFB"
            spread_relative = base / "spread" / f"{year}_W{week}_{classification}_spread.html"
            write(
                str(spread_relative),
                _page(spread_title, self.timestamp, _row_table(spread_rows, spread_columns), [("Season", f"../{year}_CFB.html")]),
            )
            completed_week = tuple(game for game in all_completed if int(game.week) == week)
            spread_by_pair = {(row["home_team"], row["away_team"]): row for row in spread_rows}
            result_rows: list[dict[str, Any]] = []
            for game in completed_week:
                spread = spread_by_pair.get((game.home_team, game.away_team))
                if spread is None:
                    continue
                actual_margin = float(game.home_points) - float(game.away_points)
                predicted_margin = float(spread["spread_value"])
                result_rows.append(
                    {
                        "week": week,
                        "home_team": game.home_team,
                        "away_team": game.away_team,
                        "spread": spread["spread"],
                        "spread_value": predicted_margin,
                        "actual_margin": actual_margin,
                        **grade_ats(
                            home_team=game.home_team,
                            away_team=game.away_team,
                            home_points=game.home_points,
                            away_points=game.away_points,
                            home_margin_line=predicted_margin,
                        ),
                    }
                )
            if phase != "preseason" and week <= target_week:
                write(
                    str(base / "spread" / f"{year}_W{week}_{classification}_spread_results.html"),
                    _page(
                        f"CORS {self.model_version} - {year} W{week} Spread Results - {classification} CFB",
                        self.timestamp,
                        _row_table(result_rows, spread_result_columns),
                        [("Season", f"../{year}_CFB.html")],
                    ),
                )
        if phase == "preseason":
            week_zero = [game_row(game) for game in scheduled_games(snapshot, 0) if int(game.week) == 0]
            write(
                str(base / "data" / "slate" / "weekly_slate" / f"{year}_W0_{classification}_slate.html"),
                _page(
                    f"CORS {self.model_version} - {year} W0 Slate - {classification} CFB",
                    self.timestamp,
                    _row_table(week_zero, slate_columns),
                    (("Season", f"../../../{year}_CFB.html"),),
                ),
            )
        elif phase == "week" and next_week is not None:
            next_slate = [
                game_row(game)
                for game in snapshot.games
                if int(game.week) == next_week and not is_explicit_non_played(game)
            ]
            write(
                str(base / "data" / "slate" / "weekly_slate" / f"{year}_W{next_week}_{classification}_slate.html"),
                _page(
                    f"CORS {self.model_version} - {year} W{next_week} Slate - {classification} CFB",
                    self.timestamp,
                    _row_table(next_slate, slate_columns),
                    (("Season", f"../../../{year}_CFB.html"),),
                ),
            )

        # History and navigation are generated in the same pass, so links in
        # the owned graph always resolve without depending on the old website.
        inherited_navigation: list[dict[str, str]] = []
        if self.clone_published:
            inherited_home = self.site / "cfb" / "cfb.html"
            if inherited_home.exists():
                try:
                    inherited_document = BeautifulSoup(inherited_home.read_text(encoding="utf-8", errors="replace"), "html.parser")
                    for anchor in inherited_document.find_all("a", href=True):
                        href = str(anchor["href"])
                        if href.startswith(("http:", "https:", "mailto:", "javascript:", "#")):
                            continue
                        inherited_href = href.split("#", 1)[0]
                        inherited_target = (
                            self.site / inherited_href.lstrip("/")
                            if inherited_href.startswith("/")
                            else inherited_home.parent / inherited_href
                        ).resolve()
                        try:
                            inherited_target.relative_to(self.site.resolve())
                        except ValueError:
                            continue
                        if not inherited_target.exists():
                            # Keep known legacy defects in legacy_failures, not
                            # in the owned navigation graph.
                            continue
                        inherited_navigation.append({"label": anchor.get_text(strip=True), "href": href})
                except OSError:
                    raise
        existing_navigation = {(item["href"], item["label"]): item for item in inherited_navigation}
        merged_navigation = list(existing_navigation.values())
        links: list[tuple[str, str]] = [("CORS home", "../../cfb.html")]
        if season_complete:
            links.append(("Final", f"rankings/{year}_FINAL_{classification}_cors.html"))
        if phase in {"preseason", "week", "final"}:
            links.extend((
                ("Preseason ranking", f"rankings/{year}_PRESEASON_{classification}_cors.html"),
                ("W0 slate", f"data/slate/weekly_slate/{year}_W0_{classification}_slate.html"),
                ("W0 spread", f"spread/{year}_W0_{classification}_spread.html"),
            ))
        for week in (() if phase == "preseason" else range(target_week + 1)):
            links.extend(
                (
                    (f"W{week} ranking", f"rankings/{year}_W{week}_{classification}_cors.html"),
                    (f"W{week} records", f"data/records/{year}_W{week}_{classification}_records.html"),
                    (f"W{week} results", f"data/results/weekly_results/{year}_W{week}_{classification}_results.html"),
                    (f"W{week} slate", f"data/slate/weekly_slate/{year}_W{week}_{classification}_slate.html"),
                )
            )
            if phase != "preseason":
                links.extend(
                    (
                        (f"W{week} spread", f"spread/{year}_W{week}_{classification}_spread.html"),
                        (f"W{week} spread results", f"spread/{year}_W{week}_{classification}_spread_results.html"),
                    )
                )
        if phase == "week" and next_week is not None:
            links.extend((
                (f"W{next_week} slate", f"data/slate/weekly_slate/{year}_W{next_week}_{classification}_slate.html"),
                (f"W{next_week} spread", f"spread/{year}_W{next_week}_{classification}_spread.html"),
            ))
        write(
            str(base / f"{year}_CFB.html"),
            _page(
                f"{year} CORS {self.model_version} CFB Results",
                self.timestamp,
                "<p>" + "<br>".join(
                    f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'
                    for label, href in links
                ) + "</p>",
                (("CORS home", "../../cfb.html"),),
            ),
        )

        history_rows = []
        if season_complete:
            champion = rankings[target_week][0] if rankings[target_week] else {}
            worst = rankings[target_week][-1] if rankings[target_week] else {}
            for label, row in (("National champion", champion), ("Worst team", worst)):
                if row:
                    # Keep the ranking's validated win percentage in the
                    # durable outcome row.  Older published history often
                    # supplies this as ``Win%``; retaining it here lets an
                    # overlay compare that inherited value exactly.
                    history_rows.append({
                        "year": year,
                        "school": row["school"],
                        "conference": row["conference"],
                        "record": row["record"],
                        "win_pct": row["win_pct"],
                        "cors": row["cors"],
                        "kind": label,
                    })
        # ``years/history`` is the long-standing public location; the shorter
        # ``cfb/history`` alias keeps integrations that adopted the recovery
        # layout working.  Both are generated from the same merged rows.
        history_bases = (Path("cfb") / "history", Path("cfb") / "years" / "history")
        history_base = history_bases[0]
        inherited_history: dict[str, list[dict[str, Any]]] = {}
        if self.clone_published:
            # A candidate overlay must not replace the historical index with
            # only this season's two rows.  Read inherited history before the
            # owned page is rewritten and carry every row forward.
            for filename, kind in (
                (f"nc_{classification}_CFB_output.html", "National champion"),
                (f"wt_{classification}_CFB_output.html", "Worst team"),
            ):
                inherited_path = next(
                    (self.site / candidate_base / filename for candidate_base in (history_bases[0], history_bases[1]) if (self.site / candidate_base / filename).exists()),
                    None,
                )
                if inherited_path is None:
                    continue
                try:
                    inherited_tables = pd.read_html(StringIO(inherited_path.read_text(encoding="utf-8", errors="replace")))
                except (OSError, ValueError):
                    continue
                if not inherited_tables:
                    continue
                inherited: list[dict[str, Any]] = []
                for row in inherited_tables[0].where(pd.notna(inherited_tables[0]), None).to_dict(orient="records"):
                    canonical = _canonical_history_row(row, filename)
                    school = canonical.get("school")
                    if school is None:
                        continue
                    raw_year = canonical.get("year")
                    year_text = re.sub(r"[^0-9]", "", str(raw_year or ""))
                    if not year_text:
                        continue
                    inherited_row = {
                        "year": int(year_text),
                        "school": str(school),
                        "conference": str(canonical.get("conference", "")),
                        "record": str(canonical.get("record", "")),
                        "cors": canonical.get("cors", 0.0),
                        "kind": kind,
                    }
                    if "win_pct" in canonical:
                        # Keep the parsed numeric value as supplied.  Do not
                        # recompute or stringify legacy Win% data.
                        inherited_row["win_pct"] = canonical["win_pct"]
                    inherited.append(inherited_row)
                    history_rows.append(inherited_row)
                inherited_history[filename] = inherited
        for filename, title, kind in (
            (f"nc_{classification}_CFB_output.html", "National Champions", "National champion"),
            (f"wt_{classification}_CFB_output.html", "Worst Teams", "Worst team"),
        ):
            subset_by_outcome: dict[tuple[int, str], dict[str, Any]] = {}
            for row in history_rows:
                if row["kind"] == kind:
                    # The current ranking is appended first and therefore
                    # wins if an overlay corrects an existing season.  Key by
                    # season and outcome kind so a changed champion or worst
                    # team replaces the old row rather than leaving both.
                    subset_by_outcome.setdefault((int(row["year"]), kind), row)
            subset = sorted(subset_by_outcome.values(), key=lambda row: (int(row["year"]), str(row["school"])))
            history_columns = ("year", "school", "conference", "record", "win_pct", "cors", "kind") if any(
                "win_pct" in row for row in subset
            ) else ("year", "school", "conference", "record", "cors", "kind")
            for output_history_base in history_bases:
                history_link = (
                    f"../years/{year}/{year}_CFB.html"
                    if output_history_base == history_bases[0]
                    else f"../{year}/{year}_CFB.html"
                )
                write(
                    str(output_history_base / filename),
                    _page(
                        f"CORS {self.model_version} - {title} - {classification} CFB",
                        self.timestamp,
                        _row_table(subset, history_columns),
                        (("Season", history_link),),
                    ),
                )

        home_links = [(item["label"], item["href"]) for item in merged_navigation]
        home_links.extend(
            (
                (str(year), f"years/{year}/{year}_CFB.html"),
                ("National champions", f"years/history/nc_{classification}_CFB_output.html"),
                ("Worst teams", f"years/history/wt_{classification}_CFB_output.html"),
            )
        )
        seen_home_hrefs: set[str] = set()
        deduped_home_links: list[tuple[str, str]] = []
        for label, href in home_links:
            if href in seen_home_hrefs:
                continue
            seen_home_hrefs.add(href)
            deduped_home_links.append((label, href))
        write(
            "cfb/cfb.html",
            _page(
                f"CORS {self.model_version} CFB",
                self.timestamp,
                "<p>" + "<br>".join(
                    f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>'
                    for label, href in deduped_home_links
                ) + "</p>",
            ),
        )

        # Machine-readable source and season metadata are part of the owned
        # graph and carry enough information for offline revalidation.
        snapshot_payload = _json(_snapshot_payload(snapshot))
        write(str(base / "data" / "snapshot.json"), snapshot_payload)
        # Keep a content-addressed copy for this run.  The canonical current
        # path above is intentionally overwritten by each newer checkpoint;
        # this archive is immutable provenance for every cumulative run.
        snapshot_archive_relative = _snapshot_archive_relative(snapshot)
        write(snapshot_archive_relative, snapshot_payload)
        metadata = {
            "release_id": self.release_id,
            "release_root_name": self.root.name,
            "sport": snapshot.sport,
            "classification": classification,
            "season": year,
            "year": year,
            "week": target_week,
            "target_week": target_week,
            "model_version": self.model_version,
            "code_revision": self.code_revision,
            "source_snapshot": snapshot.checksum,
            "snapshot_checksum": snapshot.checksum,
            "last_updated": self.timestamp,
            "created_at": self.timestamp,
            "cors_tie_break": "cors desc, wins desc, losses asc, school asc",
            "previous_final": {
                "cors": dict(sorted(previous.cors.items())),
                "wins_vs_expected": dict(sorted(previous.wins_vs_expected.items())),
            },
            "inherited_history": inherited_history,
            "inherited_navigation": inherited_navigation,
            "complete_through_week": complete_through_week,
            "scheduled_end_week": scheduled_end,
            "season_complete": season_complete,
            "inherited_final": inherited_final,
            "phase": phase,
            "base_tree_sha256": origin_base_digest,
            "immediate_base_tree_sha256": immediate_base_digest,
        }
        run_evidence = {
            "sport": snapshot.sport,
            "classification": classification,
            "season": year,
            "phase": phase,
            "target_week": target_week,
            "last_updated": self.timestamp,
            "snapshot_path": f"{base.as_posix()}/data/snapshot.json",
            "snapshot_archive_path": snapshot_archive_relative,
            "snapshot_archive_checksum": snapshot.checksum,
            "source_snapshot": snapshot.checksum,
            "carryover": carryover_provenance,
        }
        if snapshot_week_calendar is not None:
            run_evidence["week_calendar"] = snapshot_week_calendar
        metadata["runs"] = inherited_runs + [run_evidence]
        write(str(base / "metadata.json"), _json(metadata))
        cumulative_owned = inherited_owned | set(owned) | {"release.json"}
        metadata["required_artifacts"] = sorted(cumulative_owned | {"manifest.json"})
        write("release.json", _json({**metadata, "owned_artifacts": sorted(cumulative_owned)}))
        cumulative_owned.add("release.json")
        checksums = {relative: _sha256(self.site / relative) for relative in sorted(cumulative_owned)}
        manifest = {
            **metadata,
            "owned_artifacts": sorted(cumulative_owned),
            "artifact_checksums": checksums,
            "manifest_version": 2,
        }
        manifest["manifest_checksum"] = hashlib.sha256(_json(manifest).encode("utf-8")).hexdigest()
        manifest_path = self.site / "manifest.json"
        _atomic_write(manifest_path, _json(manifest))
        return Release(
            release_id=self.release_id,
            root=self.root,
            site=self.site,
            manifest_path=manifest_path,
            metadata_path=self.site / "release.json",
            target_week=target_week,
            owned_artifacts=tuple(sorted(cumulative_owned)),
            scheduled_end_week=scheduled_end,
            season_complete=season_complete,
            phase=phase,
            base_site=self.published_site,
        )


def build_release(
    snapshot: SeasonSnapshot,
    output_root: str | Path,
    *,
    release_id: str | None = None,
    target_week: int | None = None,
    phase: str | None = None,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    model_version: str = MODEL_VERSION,
    code_revision: str = "working-tree",
    timestamp: str | None = None,
    published_site: str | Path | None = None,
    clone_published: bool = True,
    hfa: float = HFA,
) -> Release:
    """Build a candidate Release into ``<output>/<id>/site``."""

    return ReleaseBuilder(
        output_root,
        release_id=release_id,
        model_version=model_version,
        code_revision=code_revision,
        timestamp=timestamp,
        published_site=published_site,
        clone_published=clone_published,
        hfa=hfa,
    ).build(snapshot, target_week=target_week, phase=phase, previous_final=previous_final)


def _parse_table(path: Path) -> list[dict[str, Any]]:
    try:
        tables = pd.read_html(StringIO(path.read_text(encoding="utf-8", errors="replace")))
    except (ValueError, OSError) as exc:
        raise ValueError(f"no readable table in {path}") from exc
    if not tables:
        return []
    frame = tables[0]
    frame = frame.loc[:, [column for column in frame.columns if not str(column).startswith("Unnamed:")]]
    return frame.where(pd.notna(frame), None).to_dict(orient="records")


def _failure(failures: list[ValidationFailure], code: str, message: str, path: Path | None = None) -> None:
    failures.append(ValidationFailure(code, message, str(path) if path else None))


def _validate_release(
    candidate: str | Path | Release,
    strict: bool = True,
    published_site: str | Path | None = None,
) -> ValidationReport:
    """Validate owned artifacts and return structured failures.

    Inherited files from a cloned Published Site are scanned separately and
    reported as ``legacy_failures``.  They never silently satisfy an owned
    artifact requirement and never invalidate an otherwise complete candidate.
    """

    site = _site_for(candidate)
    base_value = published_site
    if base_value is None and isinstance(candidate, Release):
        base_value = candidate.base_site
    failures: list[ValidationFailure] = []
    legacy_failures: list[ValidationFailure] = []
    report = ValidationReport(True, failures, legacy_failures, [], site)
    try:
        base = _valid_published_site(base_value)
    except ValueError as exc:
        _failure(failures, "base.required", str(exc))
        report.valid = False
        return report
    manifest_path = site / "manifest.json"
    release_path = site / "release.json"
    if not manifest_path.exists():
        _failure(failures, "metadata.missing", "manifest.json is required", manifest_path)
        report.valid = False
        return report
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _failure(failures, "metadata.invalid", str(exc), manifest_path)
        report.valid = False
        return report
    required_fields = (
        "release_id",
        "release_root_name",
        "sport",
        "classification",
        "season",
        "target_week",
        "model_version",
        "code_revision",
        "source_snapshot",
        "last_updated",
        "complete_through_week",
        "scheduled_end_week",
        "season_complete",
        "inherited_final",
        "phase",
        "base_tree_sha256",
        "owned_artifacts",
        "artifact_checksums",
        "manifest_checksum",
    )
    for field_name in required_fields:
        value = manifest.get(field_name)
        if field_name == "season_complete":
            missing = field_name not in manifest or not isinstance(value, bool)
        elif field_name == "inherited_final":
            missing = field_name not in manifest or not isinstance(value, bool)
        else:
            missing = value is None or value == ""
        if field_name in {"owned_artifacts", "artifact_checksums"} and not value:
            missing = True
        if missing:
            _failure(failures, "metadata.field", f"missing manifest field {field_name}", manifest_path)
    release_id = _canonical_release_id(manifest.get("release_id"))
    sealed_root_name = _canonical_release_id(manifest.get("release_root_name"))
    if release_id is None:
        _failure(failures, "release.id", "release_id is not canonical", manifest_path)
    if sealed_root_name is None:
        _failure(failures, "release.root", "release_root_name is not canonical", manifest_path)
    if release_id is not None and sealed_root_name is not None and release_id != sealed_root_name:
        _failure(failures, "release.id", "release_id disagrees with its sealed root binding", manifest_path)
    observable_root = _observable_candidate_root(candidate)
    if observable_root is not None and release_id is not None:
        if observable_root.name != release_id:
            _failure(failures, "release.id", "release_id disagrees with the actual immutable candidate root", observable_root)
        if isinstance(candidate, Release) and candidate.release_id != release_id:
            _failure(failures, "release.id", "release_id disagrees with the constructed Release value", observable_root)
    stored_manifest_checksum = manifest.get("manifest_checksum")
    if stored_manifest_checksum:
        unsigned_manifest = dict(manifest)
        unsigned_manifest.pop("manifest_checksum", None)
        expected_manifest_checksum = hashlib.sha256(_json(unsigned_manifest).encode("utf-8")).hexdigest()
        if stored_manifest_checksum != expected_manifest_checksum:
            _failure(failures, "metadata.checksum", "manifest checksum verification failed", manifest_path)
    if manifest.get("model_version") != MODEL_VERSION:
        _failure(failures, "model.version", f"expected {MODEL_VERSION}, got {manifest.get('model_version')}", manifest_path)
    run_contexts: list[tuple[dict[str, Any], SeasonSnapshot]] = []
    cumulative_expected: set[str] = set()
    raw_runs = manifest.get("runs")
    if manifest.get("manifest_version") != 2 or not isinstance(raw_runs, list) or not raw_runs:
        _failure(failures, "runs.missing", "manifest v2 requires non-empty cumulative run evidence", manifest_path)
        raw_runs = []
    for index, raw_run in enumerate(raw_runs):
        if not isinstance(raw_run, dict):
            _failure(failures, "runs.invalid", f"run {index} is not an object", manifest_path)
            continue
        try:
            run = dict(raw_run)
            run_year = int(run["season"])
            run_phase = str(run["phase"]).lower()
            run_target = int(run["target_week"])
            canonical_path = f"cfb/years/{run_year}/data/snapshot.json"
            if run.get("snapshot_path") != canonical_path:
                raise ValueError("snapshot_path is not canonical for the run season")
            archive_path_value = run.get("snapshot_archive_path")
            if not isinstance(archive_path_value, str):
                raise ValueError("snapshot_archive_path is required for every cumulative run")
            archive_relative = Path(archive_path_value)
            if archive_relative.is_absolute() or ".." in archive_relative.parts:
                raise ValueError("snapshot_archive_path escapes the Release")
            archive_payload = json.loads(
                (site / archive_relative).read_text(encoding="utf-8")
            )
            run_snapshot = _snapshot_from_payload(archive_payload)
            expected_week_calendar = _snapshot_week_calendar(run_snapshot)
            if run.get("week_calendar") != expected_week_calendar:
                raise ValueError("run week_calendar provenance disagrees with canonical registry")
            if _canonical_snapshot_checksum(run_snapshot) != run_snapshot.checksum:
                raise ValueError("archived snapshot checksum does not match canonical contents")
            if run_snapshot.checksum != run.get("source_snapshot"):
                raise ValueError("run source_snapshot does not match archived snapshot")
            if run.get("snapshot_archive_checksum") != run_snapshot.checksum:
                raise ValueError("snapshot_archive_checksum does not match archived snapshot")
            if archive_path_value != _snapshot_archive_relative(run_snapshot):
                raise ValueError("snapshot_archive_path is not content-addressed")
            if run_snapshot.year != run_year or run_snapshot.sport != run.get("sport") or run_snapshot.classification.upper() != str(run.get("classification", "")).upper():
                raise ValueError("run identity does not match embedded snapshot")
            run_timestamp = run.get("last_updated")
            if not isinstance(run_timestamp, str):
                raise ValueError("run last_updated is required")
            parsed_run_timestamp = datetime.fromisoformat(run_timestamp.replace("Z", "+00:00"))
            if parsed_run_timestamp.tzinfo is None:
                raise ValueError("run last_updated must be timezone-aware")
            normalized_phase, normalized_target = _phase_target(run_snapshot, run_phase, run_target)
            if (normalized_phase, normalized_target) != (run_phase, run_target):
                raise ValueError("run target is inconsistent")
            cumulative_expected.update(_expected_artifacts(run_snapshot, run_phase, run_target))
            run_contexts.append((run, run_snapshot))
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
            _failure(failures, "runs.invalid", f"run {index}: {exc}", manifest_path)
    checkpoint_fingerprints = [
        (
            snapshot.year,
            str(run.get("phase", "")).lower(),
            int(run.get("target_week", 0)),
            snapshot.checksum,
        )
        for run, snapshot in run_contexts
    ]
    if len(checkpoint_fingerprints) != len(set(checkpoint_fingerprints)):
        _failure(
            failures,
            "runs.duplicate",
            "exact cumulative run checkpoints must be unique",
            manifest_path,
        )
    for index, (run, run_snapshot) in enumerate(run_contexts):
        try:
            _, expected_carryover = _prior_final_with_evidence(run_snapshot, site)
            if run.get("carryover") != expected_carryover:
                _failure(
                    failures,
                    "carryover.provenance",
                    f"run {index} entrant and identity-repair evidence disagrees with actual team sets and registry",
                    manifest_path,
                )
        except (OSError, ValueError) as exc:
            _failure(failures, "carryover.provenance", f"run {index}: {exc}", manifest_path)
    for previous_context, current_context in zip(run_contexts, run_contexts[1:]):
        previous_run, previous_snapshot = previous_context
        current_run, current_snapshot = current_context
        if current_snapshot.year == previous_snapshot.year:
            try:
                previous_order = _checkpoint_order(
                    str(previous_run["phase"]), int(previous_run["target_week"])
                )
                current_order = _checkpoint_order(
                    str(current_run["phase"]), int(current_run["target_week"])
                )
            except (KeyError, TypeError, ValueError):
                continue
            if current_order < previous_order:
                _failure(
                    failures,
                    "runs.duplicate",
                    "same-season checkpoints cannot reverse",
                    manifest_path,
                )
            elif current_order == previous_order and current_snapshot.checksum == previous_snapshot.checksum:
                _failure(
                    failures,
                    "runs.duplicate",
                    "same-season checkpoint correction must use a different source snapshot",
                    manifest_path,
                )
        elif current_snapshot.year != previous_snapshot.year + 1 or str(previous_run.get("phase", "")).lower() != "final":
            _failure(
                failures,
                "runs.chain",
                "cross-season runs must be consecutive and every predecessor must be FINAL",
                manifest_path,
            )
    if run_contexts:
        last_run, _ = run_contexts[-1]
        for manifest_key, run_key in (("sport", "sport"), ("classification", "classification"), ("season", "season"), ("phase", "phase"), ("target_week", "target_week"), ("last_updated", "last_updated"), ("source_snapshot", "source_snapshot")):
            if str(manifest.get(manifest_key)).upper() != str(last_run.get(run_key)).upper():
                _failure(failures, "runs.current", f"manifest {manifest_key} disagrees with final run", manifest_path)
    try:
        snapshot_value = json.loads((site / "cfb" / "years" / str(manifest["season"]) / "data" / "snapshot.json").read_text(encoding="utf-8"))
        snapshot = _snapshot_from_payload(snapshot_value)
    except Exception as exc:
        snapshot = None
        _failure(failures, "snapshot.invalid", str(exc))
    if snapshot is not None:
        # ``snapshot.json`` is an owned provenance artifact.  The checksum
        # stored in that file cannot be trusted by itself: a caller may edit
        # a team/game field and then reseal the artifact and Release
        # manifests.  Reconstruct the exact SeasonSnapshot state used by the
        # cache service and independently recompute its canonical digest.
        snapshot_state = {
            "schema_version": snapshot.metadata.get("schema_version", 2),
            "sport": snapshot.sport,
            "classification": snapshot.classification,
            "year": snapshot.year,
            "teams_fetched_at": snapshot.metadata.get("teams_fetched_at"),
            "games_fetched_at": snapshot.metadata.get("games_fetched_at"),
            "complete_through_week": snapshot.metadata.get(
                "complete_through_week", -1
            ),
        }
        try:
            current_week_calendar = _snapshot_week_calendar(snapshot)
            if run_contexts and run_contexts[-1][0].get("week_calendar") != current_week_calendar:
                _failure(
                    failures,
                    "snapshot.mapping",
                    "current snapshot week_calendar disagrees with its latest run",
                    manifest_path,
                )
        except ValueError as exc:
            current_week_calendar = None
            _failure(failures, "snapshot.mapping", str(exc), manifest_path)
        schema_version = int(snapshot_state["schema_version"] or 1)
        if schema_version == 1:
            canonical_snapshot_checksum = _legacy_snapshot_checksum(
                snapshot.year,
                snapshot.classification,
                snapshot.teams,
                snapshot.games,
            )
        else:
            canonical_snapshot_checksum = _snapshot_checksum(
                snapshot_state,
                snapshot.teams,
                snapshot.games,
            )
        if snapshot.checksum != canonical_snapshot_checksum:
            _failure(
                failures,
                "snapshot.checksum",
                "snapshot checksum does not match canonical snapshot contents",
                site / "cfb" / "years" / str(manifest["season"]) / "data" / "snapshot.json",
            )
        if snapshot.checksum != manifest.get("source_snapshot"):
            _failure(failures, "snapshot.checksum", "snapshot checksum does not match manifest")
        if run_contexts and snapshot.checksum != run_contexts[-1][1].checksum:
            _failure(
                failures,
                "runs.current",
                "current canonical snapshot does not match the latest archived run",
                site / "cfb" / "years" / str(manifest["season"]) / "data" / "snapshot.json",
            )
        derived_scheduled_end = scheduled_season_end_week(snapshot)
        # Recompute from the embedded game dispositions.  The persisted
        # metadata and snapshot checksum can both be resealed by a caller;
        # they are evidence only, never the completion authority.
        derived_complete_through = _authoritative_complete_through(snapshot.games)
        stored_complete_through = snapshot.metadata.get("complete_through_week")
        try:
            stored_complete_value = int(stored_complete_through)
        except (TypeError, ValueError, OverflowError):
            stored_complete_value = None
        if stored_complete_value != derived_complete_through:
            _failure(
                failures,
                "snapshot.completion",
                "snapshot complete_through_week disagrees with game dispositions",
                manifest_path,
            )
        phase = str(manifest.get("phase", ""))
        try:
            manifest_target_week = int(manifest.get("target_week"))
        except (TypeError, ValueError):
            manifest_target_week = None
        derived_season_complete = phase == "final" and season_is_complete(snapshot)
        try:
            manifest_scheduled_end = int(manifest.get("scheduled_end_week"))
        except (TypeError, ValueError):
            manifest_scheduled_end = None
        if manifest_scheduled_end != derived_scheduled_end:
            _failure(
                failures,
                "season.boundary",
                "manifest scheduled_end_week disagrees with snapshot games",
            )
        try:
            manifest_complete_through = int(manifest.get("complete_through_week"))
        except (TypeError, ValueError):
            manifest_complete_through = None
        if manifest_complete_through != derived_complete_through:
            _failure(
                failures,
                "season.boundary",
                "manifest complete_through_week disagrees with snapshot",
            )
        if phase not in PHASES:
            _failure(failures, "season.phase", "manifest phase is invalid")
        elif phase == "preseason" and manifest_target_week != -1:
            _failure(failures, "season.target", "preseason target_week must be -1")
        elif phase != "preseason" and (manifest_target_week is None or manifest_target_week < 0):
            _failure(failures, "season.target", "week/final target_week must be non-negative")
        elif phase != "preseason" and (
            manifest_target_week is None
            or manifest_target_week > derived_complete_through
        ):
            _failure(
                failures,
                "season.target",
                "manifest target_week exceeds completed snapshot boundary",
            )
        if manifest.get("season_complete") != derived_season_complete:
            _failure(
                failures,
                "season.completion",
                "manifest season_complete disagrees with snapshot completion boundary",
            )
        if snapshot.classification.upper() != str(manifest.get("classification", "")).upper():
            _failure(failures, "snapshot.identity", "snapshot classification mismatch")
        if snapshot.sport.lower() != str(manifest.get("sport", "")).lower():
            _failure(failures, "snapshot.identity", "snapshot sport mismatch")
        if snapshot.year != int(manifest.get("season", snapshot.year)):
            _failure(failures, "snapshot.identity", "snapshot season mismatch")
        schools = [team.school for team in snapshot.teams]
        if len(schools) != len(set(schools)):
            _failure(failures, "teams.duplicate", "snapshot contains duplicate FBS teams")
        expected_schools = set(schools)
    else:
        expected_schools = set()
    owned = {str(value).replace(os.sep, "/") for value in manifest.get("owned_artifacts", [])}
    checksums = manifest.get("artifact_checksums", {})
    if not isinstance(checksums, dict):
        checksums = {}
        _failure(failures, "artifact.checksums", "artifact_checksums must be an object", manifest_path)
    elif set(checksums) != owned:
        for relative in sorted(set(checksums) - owned):
            _failure(failures, "artifact.checksum_extra", "checksum covers a non-owned path", site / relative)
    expected_owned: set[str] = set(cumulative_expected)
    if snapshot is not None and not expected_owned:
        try:
            expected_owned = _expected_artifacts(snapshot, str(manifest.get("phase", "")), int(manifest.get("target_week")))
        except (TypeError, ValueError) as exc:
            _failure(failures, "artifact.graph", str(exc))
    missing_expected = expected_owned - {path.relative_to(site).as_posix() for path in site.rglob("*") if path.is_file()}
    for relative in sorted(missing_expected):
        _failure(failures, "artifact.required", "independently derived artifact is missing", site / relative)
    if owned != expected_owned:
        for relative in sorted(expected_owned - owned):
            _failure(failures, "artifact.ownership_missing", "derived owned artifact absent from manifest", site / relative)
        for relative in sorted(owned - expected_owned):
            _failure(failures, "artifact.ownership_extra", "manifest claims a path outside the derived graph", site / relative)

    base_files = _public_files(base)
    candidate_files = _public_files(site)
    supplied_base_digest = _tree_digest(base)
    is_identical_noop_base = supplied_base_digest == _tree_digest(site)
    if supplied_base_digest not in {manifest.get("base_tree_sha256"), manifest.get("immediate_base_tree_sha256")} and not is_identical_noop_base:
        _failure(failures, "base.identity", "manifest is not bound to the supplied Published Site", base)
    deleted_paths = set(base_files) - set(candidate_files)
    changed_paths = {relative for relative in set(base_files) & set(candidate_files) if base_files[relative] != candidate_files[relative]}
    added_paths = set(candidate_files) - set(base_files)
    for relative in sorted(deleted_paths):
        _failure(failures, "overlay.deleted", "candidate deleted a Published Site path", site / relative)
    allowed_mutations = expected_owned | {"manifest.json"}
    for relative in sorted((changed_paths | added_paths) - allowed_mutations):
        _failure(failures, "overlay.unowned", "candidate changed or added a path outside run ownership", site / relative)
    # A later checkpoint may legitimately regenerate paths first emitted by an
    # earlier checkpoint (for example W0 artifacts in a W1 overlay).  Derive
    # ownership from chronological run evidence so each overlapping path is
    # checked against the latest run's sealed snapshot only.
    latest_owner: dict[str, int] = {}
    latest_final_owner: dict[int, int] = {}
    for index, (run, run_snapshot) in enumerate(run_contexts):
        try:
            for relative in _expected_artifacts(
                run_snapshot, str(run["phase"]), int(run["target_week"])
            ):
                latest_owner[relative] = index
            if str(run["phase"]).lower() == "final":
                latest_final_owner[run_snapshot.year] = index
        except (KeyError, TypeError, ValueError):
            continue
    for index, (run, run_snapshot) in enumerate(run_contexts):
        _validate_run_exact(
            site,
            run_snapshot,
            str(run["phase"]),
            int(run["target_week"]),
            failures,
            owned_paths={path for path, owner in latest_owner.items() if owner == index},
            validate_history=latest_final_owner.get(run_snapshot.year) == index,
        )
    # A corrected FINAL may legitimately replace the prior outcome row for its
    # own season.  Permit that replacement only after the latest FINAL archive
    # independently reconciles both public history aliases to its ranking.
    replaceable_history_keys: set[tuple[str, int]] = set()
    for season, owner_index in latest_final_owner.items():
        try:
            final_snapshot = run_contexts[owner_index][1]
            prior = _prior_final_from_tree(final_snapshot, site)
            final_rows = final_ranking(final_snapshot, prior)
        except (IndexError, OSError, ValueError, TypeError):
            continue
        expected_history = {
            f"nc_{final_snapshot.classification.upper()}_CFB_output.html": final_rows[0] if final_rows else None,
            f"wt_{final_snapshot.classification.upper()}_CFB_output.html": final_rows[-1] if final_rows else None,
        }
        for filename, expected_row in expected_history.items():
            if expected_row is None:
                continue
            aliases_valid = True
            for history_root in (site / "cfb" / "history", site / "cfb" / "years" / "history"):
                path = history_root / filename
                try:
                    rows = _parse_table(path)
                    current = []
                    for row in rows:
                        identity, canonical = _history_identity(row, filename)
                        if identity[0] == season:
                            current.append(canonical)
                    expected_history_fields = ("school", "conference", "record", "win_pct", "cors")
                    if len(current) != 1 or not _history_fields_match(
                        current[0], expected_row, expected_history_fields
                    ):
                        aliases_valid = False
                except (OSError, ValueError, TypeError):
                    aliases_valid = False
            if aliases_valid:
                replaceable_history_keys.add((filename, season))
    for relative in sorted(owned):
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            _failure(failures, "artifact.path", "owned artifact path escapes the Release", manifest_path)
            continue
        path = site / relative
        report.checked_artifacts.append(relative)
        if not path.exists():
            _failure(failures, "artifact.missing", "owned artifact is missing", path)
        elif relative not in checksums:
            _failure(
                failures,
                "artifact.checksum_missing",
                "owned artifact has no checksum coverage",
                path,
            )
        elif _sha256(path) != checksums[relative]:
            _failure(failures, "artifact.checksum", "artifact checksum mismatch", path)
    for relative in manifest.get("required_artifacts", []) or []:
        relative_path = Path(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            _failure(failures, "artifact.path", "required artifact path escapes the Release", manifest_path)
            continue
        path = site / str(relative)
        if not path.exists():
            _failure(failures, "artifact.required", "required artifact is missing", path)
        elif str(relative) != "manifest.json" and str(relative) not in checksums:
            _failure(
                failures,
                "artifact.checksum_missing",
                "required artifact has no checksum coverage",
                path,
            )
    if not release_path.exists():
        _failure(failures, "metadata.missing", "release.json is required", release_path)
    else:
        try:
            release_metadata = json.loads(release_path.read_text(encoding="utf-8"))
            for key in ("release_id", "release_root_name", "sport", "classification", "season", "phase", "target_week", "source_snapshot", "base_tree_sha256", "immediate_base_tree_sha256", "runs", "owned_artifacts", "required_artifacts"):
                if release_metadata.get(key) != manifest.get(key):
                    _failure(failures, "metadata.reconcile", f"release.json {key} disagrees with manifest", release_path)
        except (OSError, json.JSONDecodeError) as exc:
            _failure(failures, "metadata.invalid", str(exc), release_path)

    classification = str(manifest.get("classification", "FBS")).upper()
    year = int(manifest.get("season", 0) or 0)
    target_week = int(manifest.get("target_week", 0))
    phase = str(manifest.get("phase", ""))
    season_complete = manifest.get("season_complete") is True
    final_relative = f"cfb/years/{year}/rankings/{year}_FINAL_{classification}_cors.html"
    final_path = site / final_relative
    owned_final = final_relative in owned
    required_final = final_relative in {
        str(value).replace(os.sep, "/")
        for value in (manifest.get("required_artifacts", []) or [])
    }
    if season_complete:
        if not owned_final:
            _failure(failures, "final.ownership", "complete season must own its FINAL ranking", final_path)
        if not required_final:
            _failure(failures, "final.required", "complete season must require its FINAL ranking", final_path)
        if not final_path.exists():
            _failure(failures, "artifact.ranking", "complete season FINAL ranking is missing", final_path)
    else:
        if owned_final or required_final:
            _failure(failures, "final.ownership", "in-progress season cannot own or require a FINAL ranking", final_path)
        if final_path.exists() and manifest.get("inherited_final") is not True:
            _failure(failures, "final.inconsistent", "in-progress season must not contain a FINAL ranking", final_path)
    if phase == "preseason":
        ranking_paths = []
        ranking_week_pairs = []
    else:
        ranking_paths = [site / "cfb" / "years" / str(year) / "rankings" / f"{year}_W{week}_{classification}_cors.html" for week in range(target_week + 1)]
        ranking_week_pairs = [(week, ranking_paths[week]) for week in range(target_week + 1)]
    ranking_rows: dict[int, list[dict[str, Any]]] = {}
    for week, path in ranking_week_pairs:
        if not path.exists():
            _failure(failures, "artifact.ranking", "required ranking artifact is missing", path)
            continue
        try:
            rows = _parse_table(path)
        except ValueError as exc:
            _failure(failures, "html.table", str(exc), path)
            continue
        ranking_rows[week] = rows
        _validate_ranking_rows(rows, expected_schools, failures, path)
    preseason_path = site / "cfb" / "years" / str(year) / "rankings" / f"{year}_PRESEASON_{classification}_cors.html"
    preseason_ranking_rows: list[dict[str, Any]] | None = None
    if phase in {"preseason", "week", "final"}:
        if not preseason_path.exists():
            _failure(failures, "artifact.ranking", "required PRESEASON ranking artifact is missing", preseason_path)
        else:
            try:
                preseason_ranking_rows = _parse_table(preseason_path)
                _validate_ranking_rows(preseason_ranking_rows, expected_schools, failures, preseason_path)
            except ValueError as exc:
                _failure(failures, "html.table", str(exc), preseason_path)
    final_rows: list[dict[str, Any]] | None = None
    if season_complete and final_path.exists():
        try:
            final_rows = _parse_table(final_path)
            _validate_ranking_rows(final_rows, expected_schools, failures, final_path)
        except ValueError as exc:
            _failure(failures, "html.table", str(exc), final_path)

    if snapshot is not None:
        try:
            prior = _prior_final_from_tree(snapshot, site)
            recorded_previous = manifest.get("previous_final", {})
            prior_wve = {school: float(prior.wins_vs_expected.get(school, 0.0)) for school in prior.cors}
            recorded_wve = {school: float((recorded_previous.get("wins_vs_expected", {}) or {}).get(school, 0.0)) for school in prior.cors}
            if dict(prior.cors) != dict(recorded_previous.get("cors", {})) or prior_wve != recorded_wve:
                _failure(failures, "carryover.provenance", "recorded carryover disagrees with actual preceding FINAL", manifest_path)
            expected_preseason = None
            if phase in {"preseason", "week", "final"}:
                expected_preseason = preseason_ranking(snapshot, prior)
                if preseason_ranking_rows is not None:
                    _compare_numeric_rows(
                        preseason_ranking_rows,
                        expected_preseason,
                        ("cors", "mov", "sos", "expected_wins", "wins_vs_expected"),
                        failures,
                        preseason_path,
                    )
            if phase == "preseason":
                expected_rankings = {0: expected_preseason or []}
            else:
                expected_rankings = season_rankings(snapshot, target_week, prior)
                if phase == "final":
                    expected_rankings[target_week] = final_ranking(snapshot, prior)
            for week, expected in expected_rankings.items():
                actual = ranking_rows.get(week)
                if actual is None:
                    continue
                ranking_path = ranking_paths[week]
                _compare_numeric_rows(actual, expected, ("cors", "mov", "sos", "expected_wins", "wins_vs_expected"), failures, ranking_path)
                if phase != "preseason":
                    _validate_records(snapshot, week, actual, failures, ranking_path)
            if final_rows is not None:
                expected_final = expected_rankings.get(target_week)
                if expected_final is not None:
                    _compare_numeric_rows(
                        final_rows,
                        expected_final,
                        ("cors", "mov", "sos", "expected_wins", "wins_vs_expected"),
                        failures,
                        final_path,
                    )
                    _validate_records(snapshot, target_week, final_rows, failures, final_path)
        except Exception as exc:
            _failure(failures, "calculation.error", str(exc))
        if phase != "preseason":
            _validate_boundaries(snapshot, target_week, site, classification, failures, expected_owned)
            _validate_spreads(snapshot, target_week, site, classification, expected_schools, failures)
        else:
            _validate_spreads(snapshot, 0, site, classification, expected_schools, failures)

    strict_paths = changed_paths | added_paths
    owned_html = [site / relative for relative in expected_owned & strict_paths if relative.lower().endswith((".html", ".htm"))]
    expected_timestamp = str(manifest.get("last_updated", ""))
    try:
        parsed_timestamp = datetime.fromisoformat(expected_timestamp.replace("Z", "+00:00"))
        if parsed_timestamp.tzinfo is None:
            raise ValueError
    except ValueError:
        _failure(failures, "metadata.timestamp", "last_updated must be a timezone-aware ISO timestamp", manifest_path)
    for path in owned_html:
        try:
            content = path.read_text(encoding="utf-8")
            document = BeautifulSoup(content, "html.parser")
            if document.html is None or document.head is None or document.body is None:
                _failure(failures, "html.structure", "HTML must contain html/head/body", path)
            timestamp_match = LAST_UPDATED_RE.search(content)
            if timestamp_match is None:
                _failure(failures, "html.timestamp", "public page is missing Last updated", path)
            relative = path.relative_to(site).as_posix()
            owner_index = latest_owner.get(relative)
            page_timestamp = expected_timestamp
            if owner_index is not None:
                owner_run, owner_snapshot = run_contexts[owner_index]
                page_timestamp = str(owner_run.get("last_updated", ""))
                # Numbered overlays preserve a previously rendered PRESEASON
                # page when it already exists.  Its visible timestamp belongs
                # to the preceding same-season checkpoint, while its rows are
                # still checked against the current snapshot below.
                preseason_relative = (
                    f"cfb/years/{owner_snapshot.year}/rankings/"
                    f"{owner_snapshot.year}_PRESEASON_{owner_snapshot.classification.upper()}_cors.html"
                )
                if (
                    relative == preseason_relative
                    and str(owner_run.get("phase", "")).lower() == "week"
                ):
                    for previous_run, previous_snapshot in reversed(run_contexts[:owner_index]):
                        if previous_snapshot.year == owner_snapshot.year:
                            previous_timestamp = previous_run.get("last_updated")
                            if isinstance(previous_timestamp, str):
                                page_timestamp = previous_timestamp
                                break
            if timestamp_match is not None and html.unescape(timestamp_match.group(1)).strip() != page_timestamp:
                _failure(failures, "html.timestamp", "visible Last updated does not match release metadata", path)
            for anchor in document.find_all("a", href=True):
                _validate_link(site, path, str(anchor["href"]), failures)
        except OSError as exc:
            _failure(failures, "html.read", str(exc), path)

    # Overlay candidates record the identities inherited from the Published
    # Site.  Require every one to remain present so an apparently valid new
    # season cannot silently erase prior national-champion/worst-team rows.
    for filename, required_rows in (manifest.get("inherited_history", {}) or {}).items():
        if not required_rows:
            continue
        for history_root in (site / "cfb" / "history", site / "cfb" / "years" / "history"):
            path = history_root / filename
            try:
                actual_rows = _parse_table(path)
                for required in required_rows:
                    required_year = int(required["year"])
                    if (filename, required_year) in replaceable_history_keys:
                        continue
                    identity = (required_year, str(required["school"]))
                    matches = []
                    for row in actual_rows:
                        actual_identity, canonical = _history_identity(row, filename)
                        if actual_identity == identity:
                            matches.append(canonical)
                    inherited_history_fields = ("conference", "record", "win_pct", "cors", "kind")
                    if len(matches) != 1 or not _history_fields_match(
                        matches[0], required, inherited_history_fields
                    ):
                        _failure(failures, "history.loss", "inherited history row is missing or changed", path)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                _failure(failures, "history.invalid", str(exc), path)

    # A history outcome is meaningful only for a genuinely complete season.
    # Check the current-season row independently of the manifest's ownership
    # list so a resealed candidate cannot smuggle a premature champion/worst
    # result through as an inherited artifact.
    for filename in (
        f"nc_{classification}_CFB_output.html",
        f"wt_{classification}_CFB_output.html",
    ):
        path = site / "cfb" / "history" / filename
        if not path.exists():
            _failure(failures, "history.missing", "history artifact is required", path)
            continue
        try:
            history_rows = _parse_table(path)
            current_rows = {
                _history_identity(row, filename)[0][0]
                for row in history_rows
            }
            inherited_rows = (manifest.get("inherited_history", {}) or {}).get(filename, [])
            inherited_current_outcome = any(
                int(row.get("year", -1)) == year for row in inherited_rows
            )
            has_current_outcome = year in current_rows
            if season_complete and not has_current_outcome:
                _failure(failures, "history.current_missing", "complete season has no current-year history outcome", path)
            if not season_complete and has_current_outcome and not inherited_current_outcome:
                _failure(failures, "history.in_progress", "in-progress season has a premature history outcome", path)
        except (OSError, ValueError, TypeError) as exc:
            _failure(failures, "history.invalid", str(exc), path)

    _scan_legacy(site, expected_owned, legacy_failures)
    _validate_history_against_base(site, base, failures, replaceable_history_keys)
    report.valid = not failures
    return report


def validate_release(
    candidate: str | Path | Release,
    strict: bool = True,
    published_site: str | Path | None = None,
) -> ValidationReport:
    """Validate a candidate and surface malformed metadata as a report.

    Manifest values come from an external artifact.  Numeric conversion
    failures must remain visible to callers as validation failures rather than
    escaping as ``ValueError``/``TypeError``/``OverflowError`` exceptions.
    """

    try:
        return _validate_release(candidate, strict=strict, published_site=published_site)
    except (OSError, TypeError, ValueError, OverflowError) as exc:
        try:
            site = _site_for(candidate)
        except (OSError, TypeError, ValueError):
            site = None
        manifest_path = site / "manifest.json" if site is not None else None
        failure = ValidationFailure(
            "metadata.invalid",
            f"release validation could not complete: {type(exc).__name__}: {exc}",
            str(manifest_path) if manifest_path is not None else None,
        )
        return ValidationReport(
            valid=False,
            failures=[failure],
            site=site,
        )


def _validate_history_against_base(
    site: Path,
    base: Path,
    failures: list[ValidationFailure],
    replaceable_history_keys: set[tuple[str, int]] | None = None,
) -> None:
    """Compare overlay history with an explicitly supplied base tree."""

    base = _site_for(base)
    replaceable_history_keys = replaceable_history_keys or set()
    for filename in ("nc_FBS_CFB_output.html", "wt_FBS_CFB_output.html"):
        for history_root in (Path("cfb") / "history", Path("cfb") / "years" / "history"):
            base_path = base / history_root / filename
            candidate_path = site / history_root / filename
            if not base_path.exists():
                continue
            try:
                base_rows = _parse_table(base_path)
                candidate_rows = _parse_table(candidate_path)
            except (OSError, ValueError) as exc:
                _failure(failures, "history.invalid", str(exc), candidate_path)
                continue
            for row in base_rows:
                identity, canonical_base = _history_identity(row, filename)
                year = identity[0]
                if (filename, year) in replaceable_history_keys:
                    continue
                matches = []
                for candidate in candidate_rows:
                    candidate_identity, canonical_candidate = _history_identity(candidate, filename)
                    if candidate_identity == identity:
                        matches.append(canonical_candidate)
                base_history_fields = ("conference", "record", "win_pct", "cors", "kind")
                if len(matches) != 1 or not _history_fields_match(
                    matches[0], canonical_base, base_history_fields
                ):
                    _failure(failures, "history.loss", "base history row is missing or changed", candidate_path)


def _validate_ranking_rows(rows: Sequence[Mapping[str, Any]], teams: set[str], failures: list[ValidationFailure], path: Path) -> None:
    if {str(row.get("school")) for row in rows} != teams or len(rows) != len(teams):
        _failure(failures, "teams.complete", "ranking must contain every expected team exactly once", path)
    ranks = [row.get("rank") for row in rows]
    try:
        numeric_ranks = [int(value) for value in ranks]
        if numeric_ranks != list(range(1, len(rows) + 1)):
            _failure(failures, "ranks.contiguous", "ranking ranks must be unique and contiguous", path)
    except (TypeError, ValueError):
        _failure(failures, "ranks.invalid", "ranking ranks must be numeric", path)
    for row in rows:
        for field_name in ("cors", "mov", "sos", "expected_wins", "wins_vs_expected", "win_pct"):
            try:
                if not math.isfinite(float(row.get(field_name))):
                    raise ValueError
            except (TypeError, ValueError):
                _failure(failures, "numeric.finite", f"{field_name} must be finite", path)
    expected_order = sorted(rows, key=lambda row: (-float(row.get("cors", 0)), -int(row.get("wins", 0)), int(row.get("losses", 0)), str(row.get("school", ""))))
    if [row.get("school") for row in rows] != [row.get("school") for row in expected_order]:
        _failure(failures, "ranking.order", "ranking order violates CORS tie-break", path)


def _compare_numeric_rows(actual: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]], fields: Sequence[str], failures: list[ValidationFailure], path: Path) -> None:
    expected_by_school = {str(row["school"]): row for row in expected}
    for row in actual:
        school = str(row.get("school"))
        expected_row = expected_by_school.get(school)
        if expected_row is None:
            continue
        for field_name in fields:
            try:
                if abs(float(row.get(field_name)) - float(expected_row.get(field_name))) > 1e-9:
                    _failure(failures, "ranking.value", f"{school} {field_name} disagrees with deterministic engine", path)
            except (TypeError, ValueError):
                pass


def _same_value(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual is None or (isinstance(actual, float) and math.isnan(actual))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            value = float(actual)
            return math.isfinite(value) and abs(value - float(expected)) <= 1e-9
        except (TypeError, ValueError):
            return False
    if isinstance(expected, bool):
        return actual is expected or str(actual).lower() == str(expected).lower()
    return str(actual) == str(expected)


def _validate_exact_rows(
    path: Path,
    expected: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    failures: list[ValidationFailure],
    code: str,
) -> None:
    if not path.is_file():
        _failure(failures, "artifact.required", "derived artifact is missing", path)
        return
    try:
        actual = _parse_table(path)
    except ValueError as exc:
        _failure(failures, "html.table", str(exc), path)
        return
    if len(actual) != len(expected):
        _failure(failures, code, f"row count {len(actual)} != expected {len(expected)}", path)
        return
    for index, (actual_row, expected_row) in enumerate(zip(actual, expected)):
        for field_name in fields:
            if not _same_value(actual_row.get(field_name), expected_row.get(field_name)):
                _failure(failures, code, f"row {index} field {field_name} disagrees with snapshot-derived value", path)


def _game_row_for_validation(game: SourceGame) -> dict[str, Any]:
    return {
        "week": int(game.week),
        "home_team": game.home_team,
        "home_division": game.home_classification,
        "home_score": game.home_points,
        "away_team": game.away_team,
        "away_division": game.away_classification,
        "away_score": game.away_points,
        "neutral_site": bool(game.neutral_site),
    }


def _validate_run_exact(
    site: Path,
    snapshot: SeasonSnapshot,
    phase: str,
    target_week: int,
    failures: list[ValidationFailure],
    owned_paths: set[str] | None = None,
    validate_history: bool = True,
) -> None:
    """Validate one cumulative run from snapshot + canonical calculation seams."""

    year = snapshot.year
    cls = snapshot.classification.upper()
    root = site / "cfb" / "years" / str(year)

    def validate_exact_rows(
        path: Path,
        expected: Sequence[Mapping[str, Any]],
        fields: Sequence[str],
        code: str,
    ) -> None:
        if owned_paths is not None and path.relative_to(site).as_posix() not in owned_paths:
            return
        _validate_exact_rows(path, expected, fields, failures, code)

    try:
        preseason_rows: list[dict[str, Any]] | None = None
        preseason_rows = preseason_ranking(snapshot, _prior_final_from_tree(snapshot, site))
        prior = _prior_final_from_tree(snapshot, site)
        if phase == "preseason":
            rankings = {0: preseason_ranking(snapshot, prior)}
        else:
            rankings = season_rankings(snapshot, target_week, prior)
            if phase == "final":
                rankings[target_week] = final_ranking(snapshot, prior)
    except Exception as exc:
        _failure(failures, "carryover.provenance", str(exc), root)
        return

    ranking_fields = ("rank", "school", "conference", "record", "win_pct", "cors", "mov", "sos", "expected_wins", "wins_vs_expected", "wins", "losses", "ties")
    if phase == "preseason":
        validate_exact_rows(root / "rankings" / f"{year}_PRESEASON_{cls}_cors.html", rankings[0], ranking_fields, "ranking.value")
        weeks: tuple[int, ...] = ()
    else:
        if phase in {"week", "final"}:
            validate_exact_rows(
                root / "rankings" / f"{year}_PRESEASON_{cls}_cors.html",
                preseason_rows or [],
                ranking_fields,
                "ranking.value",
            )
        weeks = tuple(range(target_week + 1))
        for week in weeks:
            validate_exact_rows(root / "rankings" / f"{year}_W{week}_{cls}_cors.html", rankings[week], ranking_fields, "ranking.value")
            validate_exact_rows(
                root / "data" / "records" / f"{year}_W{week}_{cls}_records.html",
                records_for_week(snapshot, week),
                ("school", "conference", "record", "win_pct", "wins", "losses", "ties"),
                "records.reconcile",
            )
            completed = [_game_row_for_validation(game) for game in completed_games(snapshot, target_week) if int(game.week) == week]
            validate_exact_rows(
                root / "data" / "results" / "weekly_results" / f"{year}_W{week}_{cls}_results.html",
                completed,
                ("week", "home_team", "home_division", "home_score", "away_team", "away_division", "away_score", "neutral_site"),
                "results.reconcile",
            )
            slate = [_game_row_for_validation(game) for game in scheduled_games(snapshot, target_week) if int(game.week) == week]
            validate_exact_rows(
                root / "data" / "slate" / "weekly_slate" / f"{year}_W{week}_{cls}_slate.html",
                slate,
                ("week", "home_team", "home_division", "away_team", "away_division", "neutral_site"),
                "slate.reconcile",
            )
        all_completed = [_game_row_for_validation(game) for game in completed_games(snapshot, target_week)]
        validate_exact_rows(root / "data" / "results" / f"{year}_{cls}_results.html", all_completed, ("week", "home_team", "home_division", "home_score", "away_team", "away_division", "away_score", "neutral_site"), "results.reconcile")
        if phase == "final":
            validate_exact_rows(
                root / "rankings" / f"{year}_FINAL_{cls}_cors.html",
                rankings[target_week],
                ranking_fields,
                "ranking.value",
            )

    slate_target = 0 if phase == "preseason" else target_week
    all_slate = [_game_row_for_validation(game) for game in scheduled_games(snapshot, slate_target)]
    validate_exact_rows(root / "data" / "slate" / f"{year}_{cls}_slate.html", all_slate, ("week", "home_team", "home_division", "away_team", "away_division", "neutral_site"), "slate.reconcile")
    if phase == "preseason":
        week_zero_slate = [row for row in all_slate if int(row["week"]) == 0]
        validate_exact_rows(root / "data" / "slate" / "weekly_slate" / f"{year}_W0_{cls}_slate.html", week_zero_slate, ("week", "home_team", "home_division", "away_team", "away_division", "neutral_site"), "slate.reconcile")
    elif phase == "week":
        next_week = _next_scheduled_week(snapshot, target_week)
        if next_week is not None:
            next_slate = [
                _game_row_for_validation(game)
                for game in snapshot.games
                if int(game.week) == next_week and not is_explicit_non_played(game)
            ]
            validate_exact_rows(
                root / "data" / "slate" / "weekly_slate" / f"{year}_W{next_week}_{cls}_slate.html",
                next_slate,
                ("week", "home_team", "home_division", "away_team", "away_division", "neutral_site"),
                "slate.reconcile",
            )

    next_week = _next_scheduled_week(snapshot, target_week) if phase == "week" else None
    if phase == "preseason":
        spread_weeks = (0,)
    elif phase == "final":
        spread_weeks = (0, *range(1, target_week + 1))
    else:
        spread_weeks = (0, *range(1, target_week + 1)) + ((next_week,) if next_week is not None else ())
    for week in spread_weeks:
        if week == 0:
            prior_rows = preseason_rows or []
        elif next_week is not None and week == next_week and week > target_week:
            prior_rows = rankings[target_week]
        else:
            prior_rows = rankings[week - 1]
        expected_spreads = spreads_for_week(snapshot, week, prior_rows)
        spread_path = root / "spread" / f"{year}_W{week}_{cls}_spread.html"
        spread_fields = ("week", "home_team", "away_team", "neutral_site", "home_cors", "away_cors", "spread_value", "spread")
        validate_exact_rows(spread_path, expected_spreads, spread_fields, "spread.reconcile")
        if phase == "preseason" or week > target_week:
            continue
        by_pair = {(row["home_team"], row["away_team"]): row for row in expected_spreads}
        expected_results: list[dict[str, Any]] = []
        for game in completed_games(snapshot, target_week):
            if int(game.week) != week or (game.home_team, game.away_team) not in by_pair:
                continue
            spread = by_pair[(game.home_team, game.away_team)]
            expected_results.append({
                "week": week,
                "home_team": game.home_team,
                "away_team": game.away_team,
                "spread": spread["spread"],
                "spread_value": spread["spread_value"],
                "actual_margin": float(game.home_points) - float(game.away_points),
                **grade_ats(home_team=game.home_team, away_team=game.away_team, home_points=game.home_points, away_points=game.away_points, home_margin_line=spread["spread_value"]),
            })
        validate_exact_rows(
            root / "spread" / f"{year}_W{week}_{cls}_spread_results.html",
            expected_results,
            ("week", "home_team", "away_team", "spread", "spread_value", "actual_margin", "favorite", "underdog", "ats_result", "ats_correct"),
            "spread_result.reconcile",
        )

    if phase == "final" and validate_history and rankings[target_week]:
        expected_history = {
            f"nc_{cls}_CFB_output.html": rankings[target_week][0],
            f"wt_{cls}_CFB_output.html": rankings[target_week][-1],
        }
        for filename, expected_row in expected_history.items():
            for history_root in (site / "cfb" / "history", site / "cfb" / "years" / "history"):
                path = history_root / filename
                try:
                    rows = _parse_table(path)
                    current = []
                    for row in rows:
                        identity, canonical = _history_identity(row, filename)
                        if identity[0] == year:
                            current.append(canonical)
                    expected_fields = ("school", "conference", "record", "win_pct", "cors")
                    if len(current) != 1 or not _history_fields_match(
                        current[0], expected_row, expected_fields
                    ):
                        _failure(failures, "history.outcome", "current champion/worst does not match FINAL ranking", path)
                except (OSError, ValueError, TypeError):
                    _failure(failures, "history.invalid", "history table is unreadable", path)


def _validate_records(snapshot: SeasonSnapshot, week: int, rows: Sequence[Mapping[str, Any]], failures: list[ValidationFailure], path: Path) -> None:
    expected = {row["school"]: row for row in records_for_week(snapshot, week)}
    for row in rows:
        school = str(row.get("school"))
        value = expected.get(school)
        if value is None:
            continue
        if str(row.get("record")) != value["record"] or int(row.get("wins", 0)) != value["wins"] or int(row.get("losses", 0)) != value["losses"]:
            _failure(failures, "records.reconcile", f"{school} record does not reconcile with completed games", path)


def _validate_boundaries(
    snapshot: SeasonSnapshot,
    target_week: int,
    site: Path,
    classification: str,
    failures: list[ValidationFailure],
    owned: set[str] | None = None,
) -> None:
    completed_set = {(game.week, game.home_team, game.away_team) for game in completed_games(snapshot, target_week)}
    results_root = site / "cfb" / "years" / str(snapshot.year) / "data" / "results" / "weekly_results"
    for path in results_root.glob("*.html"):
        if owned is not None and path.relative_to(site).as_posix() not in owned:
            continue
        try:
            rows = _parse_table(path)
        except ValueError:
            continue
        for row in rows:
            key = (int(row.get("week", -1)), str(row.get("home_team")), str(row.get("away_team")))
            if key not in completed_set:
                _failure(failures, "games.boundary", "results include future or incomplete game", path)


def _validate_spreads(snapshot: SeasonSnapshot, target_week: int, site: Path, classification: str, teams: set[str], failures: list[ValidationFailure]) -> None:
    root = site / "cfb" / "years" / str(snapshot.year) / "spread"
    for week in range(target_week + 1):
        path = root / f"{snapshot.year}_W{week}_{classification}_spread.html"
        if not path.exists():
            _failure(failures, "artifact.spread", "required spread artifact is missing", path)
            continue
        try:
            rows = _parse_table(path)
        except ValueError as exc:
            _failure(failures, "spread.table", str(exc), path)
            continue
        for row in rows:
            if str(row.get("home_team")) not in teams or str(row.get("away_team")) not in teams:
                _failure(failures, "spread.team", "spread references a team outside the ranking set", path)
            try:
                if not math.isfinite(float(row.get("spread_value"))):
                    raise ValueError
            except (TypeError, ValueError):
                _failure(failures, "spread.numeric", "spread value must be finite", path)


def _validate_link(site: Path, source: Path, href: str, failures: list[ValidationFailure]) -> None:
    value = urldefrag(href.strip())[0]
    if not value or value.startswith(("#", "http:", "https:", "mailto:", "javascript:")):
        return
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return
    target = (site / value.lstrip("/")) if value.startswith("/") else (source.parent / value)
    try:
        target = target.resolve()
        site_root = site.resolve()
        target.relative_to(site_root)
    except ValueError:
        _failure(failures, "link.escape", "internal link escapes the Release", source)
        return
    if not target.exists():
        _failure(failures, "link.missing", f"internal link does not resolve: {href}", source)


def _scan_legacy(site: Path, owned: set[str], failures: list[ValidationFailure]) -> None:
    for path in site.rglob("*.html"):
        relative = path.relative_to(site).as_posix()
        if relative in owned:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if LAST_UPDATED_RE.search(content) is None:
                failures.append(ValidationFailure("legacy.timestamp", "inherited page is missing Last updated", relative))
            document = BeautifulSoup(content, "html.parser")
            for anchor in document.find_all("a", href=True):
                value = urldefrag(str(anchor["href"]))[0]
                if value.startswith(("http:", "https:", "mailto:", "javascript:", "#")) or not value:
                    continue
                target = ((site / value.lstrip("/")) if value.startswith("/") else (path.parent / value)).resolve()
                try:
                    target.relative_to(site.resolve())
                except ValueError:
                    continue
                if not target.exists():
                    failures.append(ValidationFailure("legacy.link.missing", f"inherited link does not resolve: {value}", relative))
        except OSError:
            failures.append(ValidationFailure("legacy.read", "could not inspect inherited page", relative))


def promote_release(candidate: str | Path | Release, published_site: str | Path) -> PromotionResult:
    """Validate and atomically promote a candidate site locally.

    The target is untouched on validation failure.  A byte-identical target is
    reported as a no-op, avoiding a deploy signal.  Successful replacements
    retain a sibling last-known-good backup.
    """

    target = _valid_published_site(published_site)
    report = validate_release(candidate, published_site=target)
    report.raise_for_failure()
    source = report.site
    assert source is not None
    source_files = _public_files(source)
    target_files = _public_files(target)
    added = tuple(sorted(set(source_files) - set(target_files)))
    deleted = tuple(sorted(set(target_files) - set(source_files)))
    changed_paths = tuple(sorted(
        relative for relative in set(source_files) & set(target_files)
        if source_files[relative] != target_files[relative]
    ))
    if deleted:
        raise ReleaseValidationError(f"promotion refuses {len(deleted)} deleted public paths", report)
    if not added and not changed_paths:
        return PromotionResult(False, target, None, "unchanged", added, changed_paths, deleted)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.candidate-", dir=target.parent))
    staged = temporary / target.name
    backup: Path | None = None
    try:
        shutil.copytree(source, staged)
        if target.exists():
            backup = target.parent / f"{target.name}.last-known-good"
            if backup.exists():
                backup = target.parent / f"{target.name}.last-known-good-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            os.replace(target, backup)
        os.replace(staged, target)
    except BaseException:
        # If replacement failed after moving the target, restore the prior
        # Published Site before surfacing the error.
        if backup is not None and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return PromotionResult(True, target, backup, "promoted", added, changed_paths, deleted)


def _tree_digest(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file():
            digest.update(child.relative_to(path).as_posix().encode())
            digest.update(b"\0")
            digest.update(child.read_bytes())
    return digest.hexdigest()


def validate(candidate: str | Path | Release, strict: bool = True, published_site: str | Path | None = None) -> ValidationReport:
    return validate_release(candidate, strict, published_site)


def validate_release_chain(candidate: str | Path | Release, original_published_site: str | Path) -> ValidationReport:
    """Validate a cumulative candidate directly against its original base."""

    return validate_release(candidate, published_site=original_published_site)


def promote(candidate: str | Path | Release, published_site: str | Path) -> PromotionResult:
    return promote_release(candidate, published_site)


# Naming aliases make the seam convenient for orchestration code while
# keeping one implementation of each operation.
build_candidate = build_release
validate_candidate = validate_release
promote_candidate = promote_release


__all__ = [
    "Release",
    "ReleaseBuilder",
    "ValidationFailure",
    "ValidationReport",
    "ReleaseValidationError",
    "ReleaseValidator",
    "PromotionResult",
    "grade_ats",
    "build_release",
    "validate_release",
    "validate_release_chain",
    "promote_release",
    "validate",
    "promote",
    "build_candidate",
    "validate_candidate",
    "promote_candidate",
]
