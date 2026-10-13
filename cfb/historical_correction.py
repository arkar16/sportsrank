"""Independently validate the archive reconstruction before public export.

The original site must first pass the ordinary Release/source validator. This
module grants ownership only from that original site's inputs and the pinned
CSV. It never trusts the preview's manifest or calls its HTML renderer to decide
what the output should mean. No acquisition or publication occurs here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from bs4 import BeautifulSoup

from .historical_rebuild import (
    HistoricalInputs, POLICY, RESULT_COLUMNS, _bool, _school, historical_carryover,
    rebuild_history, correction_start, game_identity,
)
from .performance import _read, seasons, validate_performance
from .ranking_engine import PreviousFinal, preseason_ranking, records_for_week, spreads_for_week, week_ranking
from .ranking_progression import build_progression

SCHEMA = "historical-correction/v1"
PUBLIC_PATH = "historical-correction.json"
RANK_COLUMNS = ("rank", "school", "conference", "record", "win_pct", "cors", "mov", "sos", "expected_wins", "wins_vs_expected")
SPREAD_COLUMNS = ("week", "home_team", "away_team", "neutral_site", "home_cors", "away_cors", "spread", "home_margin", "home_handicap", "predicted_winner")
SLATE_COLUMNS = tuple(key for key in RESULT_COLUMNS if not key.endswith("score"))


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def inventory(root: Path) -> dict[str, dict]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("historical correction cannot consume symlinks")
        if path.is_file():
            raw = path.read_bytes()
            relative = path.relative_to(root).as_posix()
            result[relative] = {"path": relative, "sha256": digest(raw), "bytes": len(raw)}
    return result


@dataclass(frozen=True)
class CorrectionInputs:
    games_csv: Path
    sha256: str
    from_season: int | None = None

    def assert_current(self) -> None:
        if not isinstance(self.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("historical CSV requires a pinned SHA-256")
        if digest(self.games_csv.read_bytes()) != self.sha256:
            raise ValueError("historical CSV differs from its pinned identity")
        if self.from_season is not None and (type(self.from_season) is not int or not 1869 <= self.from_season < 2024):
            raise ValueError("historical correction must start before 2024")


def _chain(inputs: HistoricalInputs, start: int | None) -> list[int]:
    years = seasons(inputs.website)
    if start is None:
        start = correction_start(inputs, years)
    selected = [year for year in years if year >= start]
    if not selected or selected != list(range(start, max(years) + 1)):
        raise ValueError("correction requires the complete affected season chain")
    return selected


def _cell(value):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return Decimal(format(value, ".12g"))
    if isinstance(value, (int, Decimal)):
        return Decimal(str(value))
    return "" if value is None else str(value)


def _table(path: Path, expected: list[dict], columns: tuple[str, ...]) -> None:
    actual, _ = _read(path)
    if not actual:
        headings = [cell.get_text(strip=True) for cell in BeautifulSoup(path.read_bytes(), "html.parser").select("table thead th")]
        if headings != list(columns):
            raise ValueError(f"corrected empty table columns differ: {path.name}")
    if len(actual) != len(expected):
        raise ValueError(f"corrected row count differs: {path.name}")
    for row, wanted in zip(actual, expected):
        if set(row) != set(columns):
            raise ValueError(f"corrected columns differ: {path.name}")
        for key in columns:
            value = _cell(wanted.get(key))
            if isinstance(value, Decimal):
                try:
                    same = Decimal(row[key]) == value
                except InvalidOperation:
                    same = False
            else:
                same = row[key] == value
            if not same:
                raise ValueError(f"corrected {key} differs: {path.name}")


def _links(path: Path) -> list[str]:
    return [a["href"] for a in BeautifulSoup(path.read_bytes(), "html.parser").find_all("a", href=True)]


def validate_reconstruction(original: Path, corrected: Path, request: CorrectionInputs) -> tuple[dict, set[str]]:
    """Recompute domain values from original inputs; inspect output separately."""
    request.assert_current()
    before = inventory(original)
    inputs = HistoricalInputs(original, request.games_csv)
    selected = _chain(inputs, request.from_season)
    prior = inputs.prior_final(selected[0] - 1)
    previous_members = {team.school for team in inputs.roster(selected[0] - 1)}
    owned, records = set(), []

    def check(relative, rows, columns):
        _table(corrected / relative, rows, columns)
        owned.add(relative)

    for year in selected:
        snapshot, evidence = inputs.snapshot(year)
        members = {team.school for team in snapshot.teams}
        carried, membership = historical_carryover(year, prior, previous_members, members)
        evidence.update(membership=membership, prior_final_sha256=digest(canonical(asdict(prior))))
        pre = preseason_ranking(snapshot, carried)
        target = snapshot.complete_through_week
        ranks = {0: week_ranking(snapshot, 0, previous_final=carried)
                 if any(game.week == 0 for game in snapshot.games) else pre}
        for week in range(1, target + 1):
            ranks[week] = week_ranking(snapshot, week, ranks[week - 1])
        prefix = f"cfb/years/{year}"
        final_path = f"{prefix}/rankings/{year}_FINAL_FBS_cors.html"
        final = final_path in before
        checkpoints = {"PRESEASON": pre, **{f"W{week}": rows for week, rows in ranks.items()}}
        if final:
            checkpoints["FINAL"] = ranks[target]
        elif year != selected[-1]:
            raise ValueError("intermediate corrected season has no FINAL")
        for checkpoint, rows in checkpoints.items():
            check(f"{prefix}/rankings/{year}_{checkpoint}_FBS_cors.html", rows, RANK_COLUMNS)
        results = [{"week": g.week, "home_team": g.home_team, "home_division": g.home_classification,
                    "home_score": g.home_points, "away_team": g.away_team, "away_division": g.away_classification,
                    "away_score": g.away_points, "neutral_site": g.neutral_site,
                    "game_id": game_identity(year, g), "game_date": g.date or ""} for g in snapshot.games]
        check(f"{prefix}/data/results/{year}_FBS_results.html", results, RESULT_COLUMNS)
        if year < 2024:
            relative = f"{prefix}/data/slate/{year}_FBS_slate.html"
            inherited = _read(original / relative)[0] if relative in before else []
            old_results = _read(inputs.root(year) / f"data/results/{year}_FBS_results.html")[0]
            completed = {(int(row["week"]), _school(row["home_team"]), _school(row["away_team"]), _bool(row["neutral_site"]))
                         for row in old_results}
            pending = [row for row in inherited if (int(row["week"]), _school(row["home_team"]),
                       _school(row["away_team"]), _bool(row["neutral_site"])) not in completed]
            check(relative, results + pending, SLATE_COLUMNS)
        for week in range(target + 1):
            weekly = [row for row in results if row["week"] == week]
            check(f"{prefix}/data/results/weekly_results/{year}_W{week}_FBS_results.html", weekly, RESULT_COLUMNS)
            check(f"{prefix}/data/slate/weekly_slate/{year}_W{week}_FBS_slate.html", weekly, SLATE_COLUMNS)
            predictions = spreads_for_week(snapshot, week, pre if week == 0 else ranks[week - 1])
            check(f"{prefix}/reconstructed/{year}_W{week}_FBS_spread.html", predictions, SPREAD_COLUMNS)
            weekly_records = records_for_week(snapshot, week)
            check(f"{prefix}/data/records/{year}_W{week}_FBS_records.html", weekly_records, tuple(weekly_records[0]))
        progression = f"{prefix}/history/{year}_FBS_progression"
        if progression + ".html" in before:
            expected = build_progression(snapshot, ranks, pre, final_rows=ranks[target] if final else None,
                phase="final" if final else "week", target_week=target,
                dataset_id=POLICY, carryover_identity=evidence["prior_final_sha256"])
            actual = json.loads((corrected / (progression + ".json")).read_bytes())
            if actual != expected.to_dict():
                raise ValueError("corrected progression differs from recalculated checkpoints")
            # HTML must expose the same team/checkpoint cells; verify separately
            # from the progression serializer through its semantic attributes.
            _validate_progression_html(corrected / (progression + ".html"), actual)
            owned.update({progression + ".html", progression + ".json"})
        navigation = f"{prefix}/history/reconstruction.html"
        links = _links(corrected / navigation)
        expected_links = [f"../../../performance/{year}/index.html"]
        for week in range(target + 1):
            expected_links += [f"../rankings/{year}_W{week}_FBS_cors.html",
                               f"../data/results/weekly_results/{year}_W{week}_FBS_results.html",
                               f"../reconstructed/{year}_W{week}_FBS_spread.html"]
        if links != expected_links:
            raise ValueError("corrected checkpoint navigation differs")
        owned.add(navigation)
        year_page = f"{prefix}/{year}_CFB.html"
        if year_page in before:
            raw = (original / year_page).read_bytes()
            for week in range(target + 1):
                missing = f"spread/{year}_W{week}_FBS_spread.html"
                if f"{prefix}/{missing}" not in before:
                    raw = raw.replace(missing.encode(), f"reconstructed/{year}_W{week}_FBS_spread.html".encode())
            # Independently derive the complete direct forecast navigation; do
            # not use the renderer as its own acceptance oracle.
            forecast_links = ''
            for week in range(target + 1):
                directory = "spread" if f"{prefix}/spread/{year}_W{week}_FBS_spread.html" in before else "reconstructed"
                forecast_links += f'<li><a href="{directory}/{year}_W{week}_FBS_spread.html">Week {week} forecasts</a></li>'
            addition = (f'\n<section id="forecasts"><h2>Forecasts</h2>'
                        f'<p><a href="../../performance/{year}/index.html">Forecast results and model performance</a></p>'
                        f'<ul>{forecast_links}</ul></section>\n').encode()
            raw = raw.replace(b"</body>", addition + b"</body>", 1) if b"</body>" in raw else raw + addition
            if (corrected / year_page).read_bytes() != raw:
                raise ValueError("correction changed unrelated season navigation")
            owned.add(year_page)
        evidence.update(through_week=target, final=final,
                        final_sha256=digest(canonical(ranks[target])) if final else None)
        records.append(evidence)
        prior = PreviousFinal({r["school"]: r["cors"] for r in ranks[target]},
                              {r["school"]: r["wins_vs_expected"] for r in ranks[target]}, year, "FBS")
        previous_members = members
    for filename, kind, choose in (("nc_FBS_CFB_output.html", "National champion", 0), ("wt_FBS_CFB_output.html", "Worst team", -1)):
        rows = []
        for year in seasons(original):
            path = corrected / f"cfb/years/{year}/rankings/{year}_FINAL_FBS_cors.html"
            if path.exists():
                rank = _read(path)[0][choose]
                rows.append({"year": year, **rank, "kind": kind})
        check("cfb/history/" + filename, rows, ("year", "school", "conference", "record", "win_pct", "cors", "kind"))
    report = {"schema": POLICY, "purpose": "unsealed_local_review", "source_csv_sha256": request.sha256,
              "from_season": selected[0], "through_season": selected[-1], "seasons": records,
              "added_postseason_games": sum(len(item["added_games"]) for item in records)}
    if json.loads((corrected / "historical-review.json").read_bytes()) != report:
        raise ValueError("historical review evidence differs from original inputs")
    performance = set(validate_performance(corrected, reconstruct_all=True))
    after = inventory(corrected)
    if not before.keys() <= after.keys():
        raise ValueError("correction removed an inherited public path")
    allowed = owned | performance | {"historical-review.json"}
    if not after.keys() - before.keys() <= allowed:
        raise ValueError("correction invented artifact ownership")
    for path in before.keys() - allowed:
        if before[path] != after[path]:
            raise ValueError(f"correction changed preserved bytes: {path}")
    request.assert_current()
    if inventory(original) != before:
        raise ValueError("original site changed during correction validation")
    return report, owned


def _validate_progression_html(path: Path, document: dict) -> None:
    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("corrected progression has no HTML table")
    headers = [cell.get_text(strip=True) for cell in table.select("thead th")]
    expected_headers = ["Team", "Conference"]
    for checkpoint in document["checkpoints"]:
        expected_headers += [checkpoint["key"] + " Points", checkpoint["key"] + " National rank"]
    if headers != expected_headers:
        raise ValueError("corrected progression HTML checkpoint headers differ")
    rows = table.select("tbody tr")
    if len(rows) != len(document["rows"]):
        raise ValueError("corrected progression HTML team count differs")
    for actual, row in zip(rows, document["rows"]):
        cells = actual.find_all(["th", "td"], recursive=False)
        if len(cells) != len(headers) or [cell.get_text(strip=True) for cell in cells[:2]] != [row["team"], row["conference"]]:
            raise ValueError("corrected progression HTML team identity differs")
        for index, checkpoint in enumerate(document["checkpoints"]):
            wanted = row["checkpoints"][checkpoint["key"]]
            points, rank = cells[2 + index * 2:4 + index * 2]
            if wanted["available"]:
                if Decimal(points.get_text(strip=True)) != Decimal(str(wanted["points"])) or rank.get_text(strip=True) != str(wanted["rank"]):
                    raise ValueError("corrected progression HTML values differ")
            elif (points.get_text(strip=True) != "unavailable" or rank.get_text(strip=True) != "—"
                  or points.get("aria-label") != "unavailable: " + wanted["reason"]):
                raise ValueError("corrected progression HTML availability differs")


def apply_correction(original: Path, output: Path, request: CorrectionInputs, *, market_lines=None) -> dict:
    request.assert_current()
    before = inventory(original)
    rebuild_history(original, request.games_csv, output, from_season=request.from_season, market_lines=market_lines)
    report, owned = validate_reconstruction(original, output, request)
    if inventory(original) != before:
        raise ValueError("original site changed during correction preparation")
    after = inventory(output)
    document = {**report, "schema": SCHEMA, "policy": POLICY, "purpose": "validated_public_transform",
                "original_site_inventory_sha256": digest(canonical(list(before.values()))),
                "artifacts": [after[path] for path in sorted(owned)]}
    (output / "historical-review.json").unlink()
    (output / PUBLIC_PATH).write_bytes(canonical(document))
    return binding_for(document, (output / PUBLIC_PATH).read_bytes())


def binding_for(document: dict, raw: bytes) -> dict:
    return {"schema": SCHEMA, "path": PUBLIC_PATH, "sha256": digest(raw),
            "source_csv_sha256": document["source_csv_sha256"],
            "original_site_inventory_sha256": document["original_site_inventory_sha256"],
            "paths": sorted([PUBLIC_PATH, *(item["path"] for item in document["artifacts"])])}


def validate_binding(binding: dict) -> None:
    if not isinstance(binding, dict) or set(binding) != {"schema", "path", "sha256", "source_csv_sha256", "original_site_inventory_sha256", "paths"}:
        raise ValueError("invalid historical correction binding fields")
    if binding["schema"] != SCHEMA or binding["path"] != PUBLIC_PATH:
        raise ValueError("invalid historical correction binding schema")
    for key in ("sha256", "source_csv_sha256", "original_site_inventory_sha256"):
        if not isinstance(binding[key], str) or re.fullmatch(r"[0-9a-f]{64}", binding[key]) is None:
            raise ValueError("invalid historical correction digest")
    paths = binding["paths"]
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths) or paths != sorted(set(paths)) or PUBLIC_PATH not in paths:
        raise ValueError("invalid historical correction paths")
    for path in paths:
        if (not path or "\\" in path or "\x00" in path or PurePosixPath(path).as_posix() != path
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts):
            raise ValueError("unsafe historical correction path")


def validate_public_correction(root: Path, binding: dict) -> dict:
    validate_binding(binding)
    raw = (root / PUBLIC_PATH).read_bytes()
    document = json.loads(raw)
    if (document.get("schema") != SCHEMA or document.get("policy") != POLICY
            or document.get("purpose") != "validated_public_transform"
            or binding_for(document, raw) != binding):
        raise ValueError("public historical correction differs from its binding")
    actual = inventory(root)
    for entry in document["artifacts"]:
        if actual.get(entry["path"]) != entry:
            raise ValueError("corrected artifact differs from validated evidence")
    if (root / "historical-review.json").exists():
        raise ValueError("unsealed review metadata remains in corrected export")
    return document


def corrected_coverage(original: dict, document: dict, binding: dict) -> dict:
    entry = next((row for row in document["seasons"] if row["season"] == 2025), None)
    if original["status"] == "none" or entry is None:
        return original
    return {"schema": "ranking-progression/used-coverage/v2", "status": "corrected",
            "original": original, "correction_sha256": binding["sha256"],
            "artifact_snapshot_checksum": entry["snapshot_sha256"],
            "carryover_identity": entry["prior_final_sha256"]}


def validate_corrected_coverage(root: Path, coverage: dict, document: dict, binding: dict) -> None:
    if coverage != corrected_coverage(coverage["original"], document, binding):
        raise ValueError("corrected coverage differs from historical correction")
    progression = json.loads((root / "cfb/years/2025/history/2025_FBS_progression.json").read_bytes())
    if (progression.get("source_snapshot") != coverage["artifact_snapshot_checksum"]
            or progression.get("carryover_identity") != coverage["carryover_identity"]
            or progression.get("dataset_id") != POLICY):
        raise ValueError("corrected progression provenance differs from coverage")
