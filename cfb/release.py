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
    from .ranking_engine import (
        HFA,
        MODEL_VERSION,
        PreviousFinal,
        completed_games,
        load_previous_final_model,
        records_for_week,
        scheduled_games,
        scheduled_season_end_week,
        season_is_complete,
        season_rankings,
        spreads_for_week,
    )
    from .season_snapshot import (
        SeasonSnapshot,
        _checksum as _snapshot_checksum,
        _legacy_checksum as _legacy_snapshot_checksum,
    )
    from .season_source import SourceGame, SourceTeam
except ImportError:  # Direct execution from the cfb directory.
    from ranking_engine import (
        HFA,
        MODEL_VERSION,
        PreviousFinal,
        completed_games,
        load_previous_final_model,
        records_for_week,
        scheduled_games,
        scheduled_season_end_week,
        season_is_complete,
        season_rankings,
        spreads_for_week,
    )
    from season_snapshot import (
        SeasonSnapshot,
        _checksum as _snapshot_checksum,
        _legacy_checksum as _legacy_snapshot_checksum,
    )
    from season_source import SourceGame, SourceTeam


LAST_UPDATED_RE = re.compile(r"Last updated:\s*([^<\n]+)")


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

    return SeasonSnapshot(
        sport=str(value.get("sport", "cfb")),
        classification=str(value["classification"]),
        year=int(value["year"]),
        teams=tuple(SourceTeam(**team) for team in value.get("teams", [])),
        games=tuple(SourceGame(**game) for game in value.get("games", [])),
        metadata=MappingProxyType(dict(value.get("metadata", {}))),
        checksum=str(value["checksum"]),
    )


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
) -> PreviousFinal:
    if previous_final is not None:
        return _previous_model(previous_final)
    if published_site is not None and year > 1897:
        root = _site_for(published_site)
        candidate = root / "cfb" / "years" / str(year - 1) / "rankings" / f"{year - 1}_FINAL_{classification.upper()}_cors.html"
        if candidate.exists():
            return load_previous_final_model(candidate)
    return PreviousFinal(cors={}, wins_vs_expected={})


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
        clone_published: bool = False,
        hfa: float = HFA,
    ) -> None:
        requested = Path(output_root)
        self.release_id = release_id or requested.name
        if self.release_id in {"", ".", ".."} or Path(self.release_id).name != self.release_id:
            raise ValueError("release_id must be a single path component")
        if requested.name == "site":
            self.root = requested.parent
            self.site = requested
        elif requested.name == self.release_id:
            self.root = requested
            self.site = requested / "site"
        else:
            self.root = requested / self.release_id
            self.site = self.root / "site"
        self.model_version = model_version
        self.code_revision = code_revision
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.published_site = Path(published_site) if published_site else None
        self.clone_published = clone_published
        self.hfa = float(hfa)

    def build(
        self,
        snapshot: SeasonSnapshot,
        *,
        target_week: int | None = None,
        previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    ) -> Release:
        if self.model_version != MODEL_VERSION:
            raise ValueError(f"Unsupported CORS model version: {self.model_version}")
        if target_week is None:
            target_week = max(0, snapshot.complete_through_week)
        target_week = int(target_week)
        if target_week < 0:
            raise ValueError("target_week must be non-negative")
        complete_through_week = int(snapshot.complete_through_week)
        if target_week > max(0, complete_through_week):
            raise ValueError(
                "target_week cannot exceed snapshot complete_through_week"
            )
        scheduled_end = scheduled_season_end_week(snapshot)
        season_complete = season_is_complete(snapshot) and target_week >= scheduled_end
        final_relative = (
            Path("cfb")
            / "years"
            / str(snapshot.year)
            / "rankings"
            / f"{snapshot.year}_FINAL_{snapshot.classification.upper()}_cors.html"
        )
        inherited_final = bool(
            self.clone_published
            and self.published_site
            and (
                _site_for(self.published_site) / final_relative
            ).exists()
        )
        previous = _resolve_previous_final(
            snapshot.year,
            self.published_site,
            previous_final,
            snapshot.classification,
        )

        self.site.mkdir(parents=True, exist_ok=True)
        if self.clone_published and self.published_site and self.published_site.exists():
            shutil.copytree(self.published_site, self.site, dirs_exist_ok=True)

        # An inherited same-year FINAL remains addressable in a cloned
        # Published Site, even when this candidate is an earlier checkpoint.
        # It is not added to ``owned`` or ``required_artifacts`` and therefore
        # cannot be mistaken for a newly generated result.
        if not season_complete and not self.clone_published:
            stale_final = self.site / final_relative
            if stale_final.exists():
                stale_final.unlink()

        rankings = season_rankings(snapshot, target_week, previous, model_version=self.model_version)
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
        for week in range(target_week + 1):
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

        if season_complete:
            final_title = f"CORS {self.model_version} - {year} Final Rankings - {classification} CFB"
            write(
                str(base / "rankings" / f"{year}_FINAL_{classification}_cors.html"),
                _page(final_title, self.timestamp, _row_table(rankings[target_week], ranking_columns), [("Season", f"../{year}_CFB.html")]),
            )

        all_completed = tuple(game for game in completed_games(snapshot, target_week))
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
        all_slate = [game_row(game) for game in scheduled_games(snapshot, target_week)]
        write(
            str(base / "data" / "slate" / f"{year}_{classification}_slate.html"),
            _page(
                f"CORS {self.model_version} - {year} Slate - {classification} CFB",
                self.timestamp,
                _row_table(all_slate, slate_columns),
                [("Season", f"../../{year}_CFB.html")],
            ),
        )

        # Spreads use the preceding ranking (the same timing as legacy
        # ``weekly_spread``) and only reference FBS teams in that ranking.
        for week in range(1, target_week + 1):
            spread_rows = spreads_for_week(snapshot, week, rankings[max(0, week - 1)], hfa=self.hfa)
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
                        "ats_correct": abs(actual_margin) > abs(predicted_margin),
                    }
                )
            write(
                str(base / "spread" / f"{year}_W{week}_{classification}_spread_results.html"),
                _page(
                    f"CORS {self.model_version} - {year} W{week} Spread Results - {classification} CFB",
                    self.timestamp,
                    _row_table(result_rows),
                    [("Season", f"../{year}_CFB.html")],
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
                    pass
        existing_navigation = {(item["href"], item["label"]): item for item in inherited_navigation}
        merged_navigation = list(existing_navigation.values())
        links: list[tuple[str, str]] = [("CORS home", "../../cfb.html")]
        if season_complete:
            links.append(("Final", f"rankings/{year}_FINAL_{classification}_cors.html"))
        for week in range(target_week + 1):
            links.extend(
                (
                    (f"W{week} ranking", f"rankings/{year}_W{week}_{classification}_cors.html"),
                    (f"W{week} records", f"data/records/{year}_W{week}_{classification}_records.html"),
                    (f"W{week} results", f"data/results/weekly_results/{year}_W{week}_{classification}_results.html"),
                    (f"W{week} slate", f"data/slate/weekly_slate/{year}_W{week}_{classification}_slate.html"),
                )
            )
            if week:
                links.extend(
                    (
                        (f"W{week} spread", f"spread/{year}_W{week}_{classification}_spread.html"),
                        (f"W{week} spread results", f"spread/{year}_W{week}_{classification}_spread_results.html"),
                    )
                )
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
                    history_rows.append({"year": year, "school": row["school"], "conference": row["conference"], "record": row["record"], "cors": row["cors"], "kind": label})
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
                    school = row.get("school", row.get("School"))
                    if school is None:
                        continue
                    raw_year = row.get("year", row.get("Year"))
                    year_text = re.sub(r"[^0-9]", "", str(raw_year or ""))
                    if not year_text:
                        continue
                    inherited_row = {
                        "year": int(year_text),
                        "school": str(school),
                        "conference": str(row.get("conference", row.get("Conference", ""))),
                        "record": str(row.get("record", row.get("Record", ""))),
                        "cors": row.get("cors", row.get("CORS", 0.0)),
                        "kind": kind,
                    }
                    inherited.append(inherited_row)
                    history_rows.append(inherited_row)
                inherited_history[filename] = inherited
        for filename, title, kind in (
            (f"nc_{classification}_CFB_output.html", "National Champions", "National champion"),
            (f"wt_{classification}_CFB_output.html", "Worst Teams", "Worst team"),
        ):
            subset_by_identity: dict[tuple[int, str], dict[str, Any]] = {}
            for row in history_rows:
                if row["kind"] == kind:
                    # The current ranking is appended first and therefore
                    # wins if an overlay rebuilds an existing season.
                    subset_by_identity.setdefault((int(row["year"]), str(row["school"])), row)
            subset = sorted(subset_by_identity.values(), key=lambda row: (int(row["year"]), str(row["school"])))
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
                        _row_table(subset, ("year", "school", "conference", "record", "cors", "kind")),
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
        write(str(base / "data" / "snapshot.json"), _json(_snapshot_payload(snapshot)))
        metadata = {
            "release_id": self.release_id,
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
        }
        write(str(base / "metadata.json"), _json(metadata))
        metadata["required_artifacts"] = sorted(set(owned) | {"release.json", "manifest.json"})
        write("release.json", _json({**metadata, "owned_artifacts": sorted(owned)}))
        checksums = {relative: _sha256(self.site / relative) for relative in sorted(owned)}
        manifest = {
            **metadata,
            "owned_artifacts": sorted(owned),
            "artifact_checksums": checksums,
            "manifest_version": 1,
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
            owned_artifacts=tuple(sorted(owned)),
            scheduled_end_week=scheduled_end,
            season_complete=season_complete,
        )


def build_release(
    snapshot: SeasonSnapshot,
    output_root: str | Path,
    *,
    release_id: str | None = None,
    target_week: int | None = None,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    model_version: str = MODEL_VERSION,
    code_revision: str = "working-tree",
    timestamp: str | None = None,
    published_site: str | Path | None = None,
    clone_published: bool = False,
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
    ).build(snapshot, target_week=target_week, previous_final=previous_final)


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


def validate_release(
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
    failures: list[ValidationFailure] = []
    legacy_failures: list[ValidationFailure] = []
    report = ValidationReport(True, failures, legacy_failures, [], site)
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
    stored_manifest_checksum = manifest.get("manifest_checksum")
    if stored_manifest_checksum:
        unsigned_manifest = dict(manifest)
        unsigned_manifest.pop("manifest_checksum", None)
        expected_manifest_checksum = hashlib.sha256(_json(unsigned_manifest).encode("utf-8")).hexdigest()
        if stored_manifest_checksum != expected_manifest_checksum:
            _failure(failures, "metadata.checksum", "manifest checksum verification failed", manifest_path)
    if manifest.get("model_version") != MODEL_VERSION:
        _failure(failures, "model.version", f"expected {MODEL_VERSION}, got {manifest.get('model_version')}", manifest_path)
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
        derived_scheduled_end = scheduled_season_end_week(snapshot)
        derived_complete_through = int(snapshot.complete_through_week)
        try:
            manifest_target_week = int(manifest.get("target_week"))
        except (TypeError, ValueError):
            manifest_target_week = None
        derived_season_complete = (
            season_is_complete(snapshot)
            and manifest_target_week is not None
            and manifest_target_week >= derived_scheduled_end
        )
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
        if manifest_target_week is None or manifest_target_week < 0:
            _failure(failures, "season.target", "manifest target_week must be non-negative")
        elif manifest_target_week > max(0, derived_complete_through):
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

    classification = str(manifest.get("classification", "FBS")).upper()
    year = int(manifest.get("season", 0) or 0)
    target_week = int(manifest.get("target_week", 0) or 0)
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
    ranking_paths = [site / "cfb" / "years" / str(year) / "rankings" / f"{year}_W{week}_{classification}_cors.html" for week in range(target_week + 1)]
    ranking_rows: dict[int, list[dict[str, Any]]] = {}
    for week, path in [(week, ranking_paths[week]) for week in range(target_week + 1)]:
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
    final_rows: list[dict[str, Any]] | None = None
    if season_complete and final_path.exists():
        try:
            final_rows = _parse_table(final_path)
            _validate_ranking_rows(final_rows, expected_schools, failures, final_path)
        except ValueError as exc:
            _failure(failures, "html.table", str(exc), final_path)

    if snapshot is not None:
        try:
            previous = manifest.get("previous_final", {})
            prior = PreviousFinal(
                cors=dict(previous.get("cors", {})),
                wins_vs_expected=dict(previous.get("wins_vs_expected", {})),
            )
            expected_rankings = season_rankings(snapshot, target_week, prior)
            for week, expected in expected_rankings.items():
                actual = ranking_rows.get(week)
                if actual is None:
                    continue
                _compare_numeric_rows(actual, expected, ("cors", "mov", "sos", "expected_wins", "wins_vs_expected"), failures, ranking_paths[week])
                _validate_records(snapshot, week, actual, failures, ranking_paths[week])
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
        _validate_boundaries(snapshot, target_week, site, classification, failures, owned)
        _validate_spreads(snapshot, target_week, site, classification, expected_schools, failures)

    owned_html = [site / relative for relative in owned if relative.lower().endswith((".html", ".htm"))]
    for path in owned_html:
        try:
            document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            if document.html is None or document.head is None or document.body is None:
                _failure(failures, "html.structure", "HTML must contain html/head/body", path)
            if LAST_UPDATED_RE.search(path.read_text(encoding="utf-8")) is None:
                _failure(failures, "html.timestamp", "public page is missing Last updated", path)
            for anchor in document.find_all("a", href=True):
                _validate_link(site, path, str(anchor["href"]), failures)
        except OSError as exc:
            _failure(failures, "html.read", str(exc), path)

    # Overlay candidates record the identities inherited from the Published
    # Site.  Require every one to remain present so an apparently valid new
    # season cannot silently erase prior national-champion/worst-team rows.
    for filename, required_rows in (manifest.get("inherited_history", {}) or {}).items():
        path = site / "cfb" / "history" / filename
        if not required_rows:
            continue
        try:
            actual_rows = _parse_table(path)
            actual_identities = {
                (int(re.sub(r"[^0-9]", "", str(row.get("year", row.get("Year", ""))) or "0")), str(row.get("school", row.get("School", ""))))
                for row in actual_rows
            }
            for required in required_rows:
                identity = (int(required["year"]), str(required["school"]))
                if identity not in actual_identities:
                    _failure(failures, "history.loss", "inherited history row is missing", path)
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
                int(re.sub(r"[^0-9]", "", str(row.get("year", row.get("Year", ""))) or "0"))
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

    _scan_legacy(site, owned, legacy_failures)
    if published_site is not None:
        _validate_history_against_base(site, Path(published_site), failures)
    report.valid = not failures
    return report


def _validate_history_against_base(site: Path, base: Path, failures: list[ValidationFailure]) -> None:
    """Compare overlay history with an explicitly supplied base tree."""

    base = _site_for(base)
    for filename in ("nc_FBS_CFB_output.html", "wt_FBS_CFB_output.html"):
        base_path = next(
            (base / candidate_base / filename for candidate_base in (Path("cfb") / "history", Path("cfb") / "years" / "history") if (base / candidate_base / filename).exists()),
            None,
        )
        candidate_path = site / "cfb" / "history" / filename
        if base_path is None:
            continue
        try:
            base_rows = _parse_table(base_path)
            candidate_rows = _parse_table(candidate_path)
        except (OSError, ValueError) as exc:
            _failure(failures, "history.invalid", str(exc), candidate_path)
            continue
        candidate_ids = {
            (
                int(re.sub(r"[^0-9]", "", str(row.get("year", row.get("Year", ""))) or "0")),
                str(row.get("school", row.get("School", ""))),
            )
            for row in candidate_rows
        }
        for row in base_rows:
            identity = (
                int(re.sub(r"[^0-9]", "", str(row.get("year", row.get("Year", ""))) or "0")),
                str(row.get("school", row.get("School", ""))),
            )
            if identity not in candidate_ids:
                _failure(failures, "history.loss", "base history row is missing", candidate_path)


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
    for week in range(1, target_week + 1):
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

    report = validate_release(candidate)
    report.raise_for_failure()
    source = report.site
    assert source is not None
    target = Path(published_site)
    if _tree_digest(source) == _tree_digest(target):
        return PromotionResult(False, target, None, "unchanged")
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
    return PromotionResult(True, target, backup, "promoted")


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
    "build_release",
    "validate_release",
    "promote_release",
    "validate",
    "promote",
    "build_candidate",
    "validate_candidate",
    "promote_candidate",
]
