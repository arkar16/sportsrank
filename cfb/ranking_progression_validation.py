"""Independent semantic acceptance of complete Release progression artifacts.

This boundary consumes the Release's validated source and engine checkpoints.
It does not import the progression derivation or rendering implementation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .season_snapshot import SeasonSnapshot
from .season_source import is_explicit_non_played


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_progression_artifacts(
    site: Path,
    snapshot: SeasonSnapshot,
    *,
    phase: str,
    target_week: int,
    preseason: Sequence[Mapping[str, Any]],
    rankings: Mapping[int, Sequence[Mapping[str, Any]]],
    model_version: str,
    dataset_id: str,
    carryover_identity: str,
) -> list[str]:
    """Return failures for either owned artifact, including undeclared fields.

The caller supplies already validated source/phase and freshly calculated
rankings, never values recovered from the generated progression document.
Release always supplies every eligible checkpoint; partial availability is a
derivation state, not permission for Release to omit calculated history.
"""
    year, classification = snapshot.year, snapshot.classification.upper()
    stem = f"cfb/years/{year}/history/{year}_{classification}_progression"
    json_path, html_path = site / f"{stem}.json", site / f"{stem}.html"
    failures: list[str] = []
    end = max(
        (int(game.week) for game in snapshot.games if not is_explicit_non_played(game)),
        default=-1,
    )
    keys = ["PRESEASON", *(f"W{week}" for week in range(end + 1)), "FINAL"]
    values: dict[str, Sequence[Mapping[str, Any]]] = {"PRESEASON": preseason}
    if phase != "preseason":
        values.update({f"W{week}": rankings[week] for week in range(target_week + 1)})
    if phase == "final":
        values["FINAL"] = rankings[target_week]
    provenance = {
        "sport": snapshot.sport,
        "classification": classification,
        "season": year,
        "dataset_id": dataset_id,
        "model_version": model_version,
        "source_snapshot": snapshot.checksum,
    }
    checkpoints = []
    for key in keys:
        checkpoint = {
            "key": key,
            "kind": key.lower() if key in {"PRESEASON", "FINAL"} else "week",
            "week": None if key in {"PRESEASON", "FINAL"} else int(key[1:]),
            "available": key in values,
            "source_snapshot": snapshot.checksum,
            "dataset_id": dataset_id,
            "model_version": model_version,
        }
        if key not in values:
            checkpoint["reason"] = "future_checkpoint"
        checkpoints.append(checkpoint)
    by_checkpoint = {
        key: {str(row["school"]): row for row in rows} for key, rows in values.items()
    }
    roster = sorted(snapshot.teams, key=lambda team: (team.school.casefold(), team.school))
    expected_rows = []
    for team in roster:
        cells = {}
        for key in keys:
            if key in values:
                row = by_checkpoint[key][team.school]
                cells[key] = {"available": True, "points": float(row["cors"]), "rank": int(row["rank"])}
            else:
                cells[key] = {"available": False, "reason": "future_checkpoint"}
        expected_rows.append({"team": team.school, "conference": team.conference, "checkpoints": cells})
    expected = {
        "schema_version": 1,
        **provenance,
        "carryover_identity": carryover_identity,
        "phase": phase,
        "target_week": -1 if phase == "preseason" else target_week,
        "provenance": provenance,
        "checkpoints": checkpoints,
        "rows": expected_rows,
    }
    try:
        actual = json.loads(json_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        # Canonical encoding distinguishes true from 1 as well as extra keys;
        # ordinary Python mapping equality would equate these JSON values.
        if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
            failures.append("progression JSON disagrees with source-derived values or schema")
    except (OSError, ValueError, TypeError) as exc:
        failures.append(f"progression JSON unavailable or malformed: {exc}")
    try:
        document = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
        # This artifact promises static content. A valid table hidden behind
        # executable markup must not pass merely because its initial DOM agrees.
        if document.find(["script", "iframe", "object", "embed", "base"]):
            failures.append("progression HTML contains executable or embedded content")
        for node in document.find_all(True):
            for attribute, value in node.attrs.items():
                if attribute.lower().startswith("on") or attribute.lower() == "srcdoc":
                    failures.append("progression HTML contains an executable attribute")
                if attribute.lower() in {"href", "src", "action", "formaction", "xlink:href"}:
                    normalized = "".join(str(value).split()).lower()
                    if normalized.startswith(("javascript:", "vbscript:", "data:")):
                        failures.append("progression HTML contains an executable URL")
            if node.name == "meta" and node.get("http-equiv", "").lower() == "refresh":
                failures.append("progression HTML contains a redirect")
        title = f"{year} CORS ranking progression — {classification}"
        if document.title is None or document.title.get_text(strip=True) != title:
            failures.append("progression HTML title differs")
        headings = document.find_all("h1")
        if len(headings) != 1 or headings[0].get_text(strip=True) != title:
            failures.append("progression HTML season heading differs")
        tables = document.find_all("table")
        if len(tables) != 1:
            raise ValueError("exactly one progression table is required")
        table = tables[0]
        headers = [cell.get_text(strip=True) for cell in table.select("thead th")]
        expected_headers = ["Team", "Conference"]
        for key in keys:
            expected_headers.extend((f"{key} Points", f"{key} National rank"))
        if headers != expected_headers:
            failures.append("progression HTML checkpoint columns disagree with source chronology")
        body_rows = table.select("tbody tr")
        if len(body_rows) != len(roster):
            failures.append("progression HTML roster count differs")
        for actual_row, expected_row in zip(body_rows, expected_rows):
            cells = actual_row.find_all(["th", "td"], recursive=False)
            expected_cells = [expected_row["team"], expected_row["conference"]]
            for key in keys:
                value = expected_row["checkpoints"][key]
                expected_cells.extend(
                    (repr(value["points"]), str(value["rank"]))
                    if value["available"] else ("unavailable", "—")
                )
            if [cell.get_text(strip=True) for cell in cells] != expected_cells:
                failures.append(f"progression HTML values differ for {expected_row['team']}")
        expected_links = {
            f"../{year}_CFB.html", f"{year}_{classification}_progression.json",
            *(f"../rankings/{year}_{key}_{classification}_cors.html" for key in values),
        }
        if not expected_links <= {str(anchor.get("href")) for anchor in document.find_all("a")}:
            failures.append("progression HTML permanent navigation is incomplete")
        expected_provenance = (
            f"Season {year} · Dataset {dataset_id} · Model {model_version} · "
            f"Source snapshot {snapshot.checksum}"
        )
        provenance_nodes = document.select(".provenance")
        if len(provenance_nodes) != 1 or provenance_nodes[0].get_text(strip=True) != expected_provenance:
            failures.append("progression HTML provenance differs")
    except (OSError, ValueError, TypeError) as exc:
        failures.append(f"progression HTML unavailable or malformed: {exc}")
    return failures
