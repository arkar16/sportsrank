"""Independent semantic acceptance of complete Release progression artifacts.

This boundary consumes the Release's validated source and engine checkpoints.
It does not import the progression derivation or rendering implementation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .season_snapshot import SeasonSnapshot
from .season_source import is_explicit_non_played


_PROGRESSION_STYLE_SHA256 = "664bd7c16d15cc10aea236ad41e703ecb347ec16e004c59bb65ca6e0d2f6574d"


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
        if document.find("template") or document.find(attrs={"inert": True}):
            failures.append("progression HTML contains inert or template content")
        if document.find("link", rel=lambda value: value and "stylesheet" in value):
            failures.append("progression HTML contains an external stylesheet")
        styles = document.find_all("style")
        if (
            len(styles) != 1
            or styles[0].string is None
            or hashlib.sha256(styles[0].string.encode("utf-8")).hexdigest()
            != _PROGRESSION_STYLE_SHA256
        ):
            failures.append("progression HTML stylesheet differs from the accepted static layout")
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
        core_nodes = [*headings, *document.find_all("nav"), table]
        for core in core_nodes:
            for node in (core, *core.find_all(True), *core.parents):
                if getattr(node, "attrs", None) is None:
                    continue
                if (
                    node.has_attr("hidden")
                    or node.has_attr("inert")
                    or str(node.get("aria-hidden", "")).lower() == "true"
                    or node.has_attr("style")
                    or node.name == "template"
                    or (node.name == "details" and not node.has_attr("open"))
                ):
                    failures.append("progression HTML hides core static content")
                    break
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
        expected_links = [
            ("Season", f"../{year}_CFB.html"),
            ("Progression data", f"{year}_{classification}_progression.json"),
            *((f"{key} ranking", f"../rankings/{year}_{key}_{classification}_cors.html") for key in keys if key in values),
        ]
        actual_links = [
            (anchor.get_text(strip=True), str(anchor.get("href")))
            for anchor in document.select("nav a")
        ]
        if actual_links != expected_links:
            failures.append("progression HTML permanent navigation differs")
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
