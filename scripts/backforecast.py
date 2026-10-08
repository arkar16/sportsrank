"""Offline CORS performance reports from retained rankings and final scores."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from html import escape
from io import StringIO
import json
from pathlib import Path
from typing import Any

import pandas as pd

from cfb.forecast_record import (
    EvidenceRef, FinalScore, ForecastCandidate, ForecastGrade, ForecastProvenance,
    GameIdentity, ScoreRevision, aggregate_grades, grade_forecast,
)
from cfb.ranking_engine import HFA, MODEL_VERSION, load_previous_final_rows, natural_matchup


GAME_COLUMNS = (
    "week", "rating_checkpoint", "home_team", "away_team", "neutral_site",
    "home_cors", "away_cors", "home_handicap", "predicted_winner",
    "home_score", "away_score", "actual_home_margin", "straight_up",
    "cors_line_coverage", "absolute_error",
)
RESULT_COLUMNS = {
    "week", "home_team", "away_team", "home_division", "away_division",
    "home_score", "away_score", "neutral_site",
}


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _integer(value: Any, label: str) -> int:
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError(f"{label} must be a nonnegative integer")
    return int(number)


def _neutral(value: Any) -> bool:
    text = str(value).strip().lower()
    if text not in {"true", "false"}:
        raise ValueError("neutral_site must be True or False")
    return text == "true"


def _table(path: Path) -> pd.DataFrame:
    tables = pd.read_html(StringIO(path.read_text(encoding="utf-8")), keep_default_na=False)
    if not tables or not RESULT_COLUMNS.issubset(tables[0].columns):
        raise ValueError(f"result page has incompatible columns: {path}")
    return tables[0]


def _summary(grades: list[ForecastGrade]) -> dict[str, Any]:
    summary = aggregate_grades(grades).to_dict()
    count = summary["straight_up_count"]
    summary["straight_up_percentage"] = str(Decimal(summary["straight_up_wins"]) / count) if count else None
    return summary


def backforecast(
    website: Path, season: int, *, from_week: int = 0,
    through_week: int | None = None,
) -> dict[str, Any]:
    """Use PRESEASON for W0 and W(N-1) for every subsequent scored week."""
    if season < 1 or from_week < 0 or (through_week is not None and through_week < from_week):
        raise ValueError("season and week range are invalid")
    root = website / "cfb" / "years" / str(season)
    results = root / "data" / "results" / "weekly_results"
    available = {
        int(path.stem.split("_W", 1)[1].split("_", 1)[0]): path
        for path in results.glob(f"{season}_W*_FBS_results.html")
    }
    if not available:
        raise ValueError(f"no local weekly results found for {season}: {results}")
    last = max(available) if through_week is None else through_week
    if last < from_week:
        raise ValueError("no result weeks are in the requested range")
    now = datetime.now(timezone.utc)
    all_grades: list[ForecastGrade] = []
    games: list[dict[str, Any]] = []
    weekly: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    code_digest = _digest(Path(__file__).read_bytes())

    for week in range(from_week, last + 1):
        if week not in available:
            raise ValueError(f"missing local results for {season} W{week}")
        checkpoint = "PRESEASON" if week == 0 else f"W{week - 1}"
        ranking_path = root / "rankings" / f"{season}_{checkpoint}_FBS_cors.html"
        result_path = available[week]
        ranking_digest = _digest(ranking_path.read_bytes())
        result_digest = _digest(result_path.read_bytes())
        ratings: dict[str, Any] = {}
        for row in load_previous_final_rows(ranking_path):
            school = row.get("school")
            if not isinstance(school, str) or not school.strip() or school in ratings:
                raise ValueError(f"invalid or duplicate school in {ranking_path}")
            ratings[school] = row.get("cors")
        grades: list[ForecastGrade] = []
        seen: set[tuple[str, str]] = set()
        non_fbs = missing_scores = 0
        for row in _table(result_path).to_dict(orient="records"):
            if _integer(row["week"], "week") != week:
                raise ValueError(f"result row is from the wrong week: {result_path}")
            matchup_key = (str(row["home_team"]), str(row["away_team"]))
            if matchup_key in seen:
                raise ValueError(f"duplicate game in {result_path}: {matchup_key}")
            seen.add(matchup_key)
            if any(str(row[side + "_division"]).lower() != "fbs" for side in ("home", "away")):
                non_fbs += 1
                continue
            if any(str(row[side + "_score"]).strip() in {"", "—", "-"} for side in ("home", "away")):
                missing_scores += 1
                continue
            home, away = matchup_key
            missing = {home, away} - ratings.keys()
            if missing:
                raise ValueError(f"missing {checkpoint} rating for {sorted(missing)}")
            neutral = _neutral(row["neutral_site"])
            matchup = natural_matchup(ratings[home], ratings[away], neutral_site=neutral)
            home_score = _integer(row["home_score"], "home_score")
            away_score = _integer(row["away_score"], "away_score")
            # Static result pages lack provider IDs. This ID is explicitly local
            # and is used only to prevent duplicate contributions to aggregates.
            local_id = "local-backforecast:" + hashlib.sha256(
                json.dumps([season, week, home, away]).encode()
            ).hexdigest()
            identity = GameIdentity(local_id, season, week, home, away, "fbs", "fbs", neutral)
            provenance = ForecastProvenance(
                checkpoint, "preseason" if week == 0 else f"through-week-{week - 1}",
                ranking_digest, result_digest, MODEL_VERSION, "static-result-backforecast",
                code_digest, matchup.home_rating, matchup.away_rating, Decimal(str(HFA)),
            )
            candidate = ForecastCandidate.create(
                game=identity, home_margin=matchup.home_margin,
                precision=matchup.precision, provenance=provenance,
            )
            score = FinalScore(identity, (ScoreRevision(
                home_score, away_score, now,
                EvidenceRef("local-result-page", result_path.name, result_digest),
            ),))
            grade = grade_forecast(candidate, score)
            grades.append(grade)
            games.append({
                "week": week, "rating_checkpoint": checkpoint,
                "home_team": home, "away_team": away, "neutral_site": neutral,
                "home_cors": str(matchup.home_rating), "away_cors": str(matchup.away_rating),
                "home_handicap": str(matchup.home_handicap),
                "predicted_winner": "Pick'em" if matchup.home_margin == 0 else (home if matchup.home_margin > 0 else away),
                "home_score": home_score, "away_score": away_score,
                "actual_home_margin": str(grade.actual_home_margin),
                "straight_up": grade.straight_up.value,
                "cors_line_coverage": grade.coverage.value,
                "absolute_error": str(grade.absolute_error),
            })
        all_grades.extend(grades)
        weekly.append({"week": week, **_summary(grades),
                       "non_fbs_games": non_fbs, "missing_scores": missing_scores})
        sources.append({"week": week, "rating_checkpoint": checkpoint,
                        "ranking_path": ranking_path.relative_to(website).as_posix(),
                        "ranking_sha256": ranking_digest,
                        "results_path": result_path.relative_to(website).as_posix(),
                        "results_sha256": result_digest})
    return {"schema_version": "cors-backforecast/v1", "season": season,
            "model_version": MODEL_VERSION, "from_week": from_week, "through_week": last,
            "generated_at": now.isoformat(), "summary": _summary(all_grades),
            "weekly": weekly, "games": games, "sources": sources}


def _csv(path: Path, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_report(report: dict[str, Any], output: Path, website: Path) -> None:
    if output.resolve().is_relative_to(website.resolve()):
        raise ValueError("report output must be outside the website directory")
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _csv(output / "games.csv", report["games"], GAME_COLUMNS)
    _csv(output / "weekly.csv", report["weekly"], tuple(report["weekly"][0]))
    summary = report["summary"]
    title = f"{report['season']} CORS backforecast performance"
    weekly_columns = ("week", "game_count", "straight_up_wins", "straight_up_losses", "covers", "no_covers", "pushes", "mae", "rmse")
    def label(name: str) -> str:
        return name.replace("_", " ").title().replace("Cors", "CORS").replace("Mae", "MAE").replace("Rmse", "RMSE")

    weekly_frame = pd.DataFrame(report["weekly"], columns=weekly_columns)
    for column in ("mae", "rmse"):
        weekly_frame[column] = weekly_frame[column].map(lambda value: "—" if value is None else f"{Decimal(value):.2f}")
    weekly_table = weekly_frame.rename(columns=label).to_html(index=False, escape=True)
    games_table = pd.DataFrame(report["games"], columns=GAME_COLUMNS).rename(columns=label).to_html(index=False, escape=True)
    coverage = summary["coverage_percentage"]
    coverage_text = "—" if coverage is None else f"{Decimal(coverage) * 100:.2f}%"
    accuracy = summary["straight_up_percentage"]
    accuracy_text = "—" if accuracy is None else f"{Decimal(accuracy) * 100:.2f}%"
    mae = "—" if summary["mae"] is None else f"{Decimal(summary['mae']):.2f}"
    rmse = "—" if summary["rmse"] is None else f"{Decimal(summary['rmse']):.2f}"
    html = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>
<style>body{{font:16px system-ui;margin:2rem;color:#172033}}.scroll{{overflow:auto}}
table{{border-collapse:collapse;white-space:nowrap}}th,td{{padding:.4rem .7rem;border:1px solid #ccd3dc}}th{{background:#eef2f7}}</style>
<h1>{escape(title)}</h1><p>{escape(report['model_version'])} · Weeks {report['from_week']}–{report['through_week']} · preceding-checkpoint ratings</p>
<p>{summary['game_count']} games · Straight-up: {summary['straight_up_wins']}–{summary['straight_up_losses']} ({accuracy_text}) ·
CORS line coverage: {summary['covers']}–{summary['no_covers']} ({coverage_text}), {summary['pushes']} pushes ·
MAE: {mae} points · RMSE: {rmse} points</p>
<h2>Weekly performance</h2><div class="scroll">{weekly_table}</div>
<h2>Game results</h2><div class="scroll">{games_table}</div></html>"""
    (output / "report.html").write_text(html, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season", type=int)
    parser.add_argument("--website", type=Path, default=Path("website"))
    parser.add_argument("--from-week", type=int, default=0)
    parser.add_argument("--through-week", type=int, help="default: latest local weekly results")
    parser.add_argument("--output", type=Path, help="default: .sportsrank/backforecasts/SEASON")
    args = parser.parse_args(argv)
    output = args.output or Path(".sportsrank/backforecasts") / str(args.season)
    try:
        report = backforecast(args.website, args.season, from_week=args.from_week,
                              through_week=args.through_week)
        write_report(report, output, args.website)
    except (OSError, ValueError, ArithmeticError) as error:
        parser.exit(1, f"backforecast failed: {error}\n")
    summary = report["summary"]
    mae = "—" if summary["mae"] is None else f"{Decimal(summary['mae']):.2f}"
    rmse = "—" if summary["rmse"] is None else f"{Decimal(summary['rmse']):.2f}"
    print(f"CORS {MODEL_VERSION}: {summary['game_count']} games, MAE {mae}, RMSE {rmse}")
    print(f"Report: {output / 'report.html'}")
    print(f"CSV: {output / 'games.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
