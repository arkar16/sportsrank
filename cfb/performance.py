"""Offline model performance from the site's saved forecasts and final scores."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from html import escape
from io import StringIO
import json
from pathlib import Path
import re
from typing import Any

from .forecast_release import _LegacySpreadTable
from .forecast_record import (
    EvidenceRef, FinalScore, ForecastCandidate, ForecastGrade, ForecastProvenance,
    ForecastSelection, GameIdentity, ScoreRevision, aggregate_grades, grade_forecast,
)
from .ranking_engine import HFA, MODEL_VERSION, natural_matchup

SCHEMA = "cors-performance/v1"
PREFIX = "cfb/performance"
NAVIGATION = "cfb/cfb.html"
NAV_LINK = b'<p><a href="performance/index.html">CORS model performance</a></p>\n'
GAME_COLUMNS = ("week", "home", "away", "neutral_site", "prediction_source", "rating_checkpoint",
                "home_handicap", "predicted_winner", "home_score", "away_score", "actual_home_margin",
                "straight_up", "cors_line_coverage", "absolute_error", "status")
COUNTS = ("saved_predictions", "reconstructed_predictions", "missing_predictions", "non_fbs_games", "missing_scores")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(path: Path) -> tuple[list[dict[str, str]], str]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    parser = _LegacySpreadTable()
    parser.feed(text)
    if not parser.rows:
        raise ValueError(f"no table in {path}")
    headers = parser.rows[0]
    if len(set(headers)) != len(headers):
        raise ValueError(f"duplicate columns in {path}")
    if any(len(row) != len(headers) for row in parser.rows[1:]):
        raise ValueError(f"incomplete row in {path}")
    return [dict(zip(headers, row)) for row in parser.rows[1:]], _digest(raw)


def _number(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("non-finite value")
    return number


def _integer(value: Any) -> int:
    number = _number(value)
    if number < 0 or number != int(number):
        raise ValueError("score/week must be a nonnegative integer")
    return int(number)


def _neutral(value: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise ValueError("invalid neutral_site")
    return value.lower() == "true"


def _identity(row: dict, week: int) -> tuple[str, str, bool]:
    if _integer(row["week"]) != week:
        raise ValueError("row week disagrees with file week")
    home, away = row["home_team"].strip(), row["away_team"].strip()
    if not home or not away or home == away:
        raise ValueError("invalid game participants")
    return home, away, _neutral(row["neutral_site"])


def _saved_margin(row: dict[str, str], home: str) -> tuple[Decimal, ForecastSelection | None]:
    # The displayed legacy line is a HOME handicap, not a favorite-only label.
    team, handicap = row["spread"].rsplit(" ", 1)
    if team != home:
        raise ValueError("saved spread is not in home-team orientation")
    margin = -_number(handicap)
    for column, sign in (("home_margin", 1), ("home_handicap", -1), ("spread_value", 1)):
        if column in row and _number(row[column]) * sign != margin:
            raise ValueError(f"saved {column} disagrees with displayed spread")
    selection = None
    if margin == 0 and row.get("predicted_winner", "").lower() not in {"pick'em", "pickem"}:
        selection = ForecastSelection.LEGACY_UNKNOWN
    return margin, selection


def _summary(grades: list[ForecastGrade]) -> dict[str, Any]:
    value = aggregate_grades(grades).to_dict()
    count = value["straight_up_count"]
    value["straight_up_percentage"] = str(Decimal(value["straight_up_wins"]) / count) if count else None
    return value


def seasons(website: Path) -> list[int]:
    root = website / "cfb/years"
    return sorted(int(path.name) for path in root.iterdir()
                  if path.is_dir() and path.name.isdecimal()
                  and any((path / "data/results/weekly_results").glob("*_FBS_results.html"))) if root.exists() else []


def evaluate_season(website: Path, season: int, *, from_week: int | None = None,
                    through_week: int | None = None) -> tuple[dict, list[ForecastGrade]]:
    root = website / "cfb/years" / str(season)
    available = {int(p.stem.split("_W", 1)[1].split("_", 1)[0]): p
                 for p in (root / "data/results/weekly_results").glob(f"{season}_W*_FBS_results.html")}
    if not available:
        raise ValueError(f"no local weekly results found for {season}")
    first = min(available) if from_week is None else from_week
    last = max(available) if through_week is None else through_week
    if first < 0 or last < first or first not in available or last not in available:
        raise ValueError("requested result week is unavailable")
    games, weekly, sources, all_grades = [], [], [], []
    for week in sorted(w for w in available if first <= w <= last):
        result_path = available[week]
        result_rows, result_sha = _read(result_path)
        spread_path = root / "spread" / f"{season}_W{week}_FBS_spread.html"
        prediction_rows, spread_sha = _read(spread_path) if spread_path.exists() else ([], None)
        prediction_map = {}
        for row in prediction_rows:
            key = _identity(row, week)
            if key in prediction_map:
                raise ValueError(f"duplicate saved prediction in {spread_path}: {key}")
            prediction_map[key] = row
        checkpoint = "PRESEASON" if week == 0 else f"W{week - 1}"
        rating_path = root / "rankings" / f"{season}_{checkpoint}_FBS_cors.html"
        ratings = None
        rating_sha = None
        counts = dict.fromkeys(COUNTS, 0)
        grades, seen = [], set()
        for row in result_rows:
            if any(row[side + "_division"].lower() != "fbs" for side in ("home", "away")):
                counts["non_fbs_games"] += 1
                continue
            key = _identity(row, week)
            if key in seen:
                raise ValueError(f"duplicate result in {result_path}: {key}")
            seen.add(key)
            home, away, neutral = key
            game = {name: None for name in GAME_COLUMNS}
            game.update(week=week, home=home, away=away, neutral_site=neutral)
            if any(row[side + "_score"].strip() in {"", "—", "-", "None", "nan"} for side in ("home", "away")):
                counts["missing_scores"] += 1
                game["status"] = "Pending score"
                games.append(game)
                continue
            home_score, away_score = (_integer(row[side + "_score"]) for side in ("home", "away"))
            game.update(home_score=home_score, away_score=away_score, actual_home_margin=str(home_score-away_score))
            prediction = prediction_map.get(key)
            selection = None
            if prediction is not None:
                margin, selection = _saved_margin(prediction, home)
                source = "Saved spread"
                forecast_sha = spread_sha
                home_rating, away_rating = prediction.get("home_cors", "0"), prediction.get("away_cors", "0")
                counts["saved_predictions"] += 1
            else:
                if ratings is None:
                    ratings = {}
                    if rating_path.exists():
                        rating_rows, rating_sha = _read(rating_path)
                        for rating in rating_rows:
                            name = rating.get("school", "")
                            if not name or name in ratings:
                                raise ValueError(f"invalid or duplicate rating in {rating_path}")
                            ratings[name] = rating["cors"]
                if home not in ratings or away not in ratings:
                    counts["missing_predictions"] += 1
                    game["status"] = "Missing prediction / preceding rating"
                    games.append(game)
                    continue
                home_rating, away_rating = ratings[home], ratings[away]
                # Existing CORS arithmetic; no changes to rating precision or model.
                matchup = natural_matchup(home_rating, away_rating, neutral_site=neutral)
                margin, source, forecast_sha = matchup.home_margin, "Reconstructed", rating_sha
                counts["reconstructed_predictions"] += 1
                game["rating_checkpoint"] = checkpoint
            identity = GameIdentity("site-performance:" + _digest(json.dumps([season, week, *key]).encode()),
                                    season, week, home, away, "fbs", "fbs", neutral)
            candidate = ForecastCandidate.create(
                game=identity, home_margin=margin, precision=max(0, -margin.as_tuple().exponent), selection=selection,
                provenance=ForecastProvenance(checkpoint, "site-performance", "sha256:"+forecast_sha,
                    "sha256:"+result_sha, MODEL_VERSION, source, SCHEMA,
                    _number(home_rating), _number(away_rating), Decimal(0) if neutral else Decimal(str(HFA))))
            score = FinalScore(identity, (ScoreRevision(home_score, away_score, datetime.now(timezone.utc),
                EvidenceRef("local-result-page", result_path.name, "sha256:"+result_sha)),))
            grade = grade_forecast(candidate, score)
            grades.append(grade)
            game.update(prediction_source=source, home_handicap=str(-margin),
                        predicted_winner=("Unknown (zero line)" if selection else "Pick'em") if margin == 0 else home if margin > 0 else away,
                        straight_up=grade.straight_up.value, cors_line_coverage=grade.coverage.value,
                        absolute_error=str(grade.absolute_error), status="Evaluated")
            games.append(game)
        all_grades.extend(grades)
        weekly.append({"week": week, **_summary(grades), **counts})
        for path, digest in ((result_path, result_sha), (spread_path, spread_sha), (rating_path, rating_sha)):
            if digest:
                sources.append({"path": path.relative_to(website).as_posix(), "sha256": digest})
    return {"schema_version": SCHEMA, "season": season, "from_week": first, "through_week": last,
            "summary": {**_summary(all_grades), **{k: sum(w[k] for w in weekly) for k in COUNTS}},
            "weekly": weekly, "games": games, "sources": sources}, all_grades


def backforecast(website: Path, season: int, *, from_week: int | None = None, through_week: int | None = None) -> dict:
    return evaluate_season(website, season, from_week=from_week, through_week=through_week)[0]


def _csv(rows: list[dict], columns: tuple[str, ...]) -> str:
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _label(key: str) -> str:
    return {"home_handicap": "CORS spread (home)", "cors_line_coverage": "CORS line coverage",
            "mae": "MAE (pts)", "rmse": "RMSE (pts)", "game_count": "Games", "straight_up_wins": "Wins",
            "straight_up_losses": "Losses", "straight_up_ties": "Ties", "no_covers": "No covers"}.get(key, key.replace("_", " ").capitalize())


def _text(value: Any) -> str:
    return "—" if value is None else escape(str(value))


def _table(rows: list[dict], columns: tuple[str, ...], *, season_links: bool = False) -> str:
    cells = []
    for row in rows:
        values = []
        for key in columns:
            value = row.get(key)
            if key in {"mae", "rmse", "absolute_error"} and value is not None:
                value = f"{Decimal(value):.2f}"
            cell = _text(value)
            if key == "season" and season_links:
                cell = f'<a href="{int(value)}/index.html">{int(value)}</a>'
            values.append(f"<td>{cell}</td>")
        cells.append("<tr>"+"".join(values)+"</tr>")
    return '<div class="scroll"><table><thead><tr>'+"".join(f"<th>{_label(c)}</th>" for c in columns)+"</tr></thead><tbody>"+"".join(cells)+"</tbody></table></div>"


def _page(title: str, body: str, navigation: str) -> bytes:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;color:#172033;background:#fff;margin:0 auto;padding:24px;max-width:1200px}}a{{color:#124f9c}}h1{{line-height:1.15}}.scroll{{overflow:auto;max-width:100%;margin:20px 0}}table{{border-collapse:collapse;white-space:nowrap;font-variant-numeric:tabular-nums}}th,td{{padding:8px 12px;text-align:right;border-bottom:1px solid #dce2e9}}th{{background:#eef2f7;text-align:right}}td:first-child,th:first-child{{text-align:left}}.metrics{{font-size:1.1rem;padding:16px;background:#eef2f7;border-radius:8px}}details{{margin:16px 0}}@media(max-width:600px){{body{{padding:16px}}h1{{font-size:1.6rem}}}}</style></head><body><nav>{navigation}</nav><h1>{escape(title)}</h1>{body}</body></html>'''.encode()


def _metrics(s: dict) -> str:
    accuracy = "—" if s["straight_up_percentage"] is None else f'{Decimal(s["straight_up_percentage"])*100:.1f}%'
    coverage = "—" if s["coverage_percentage"] is None else f'{Decimal(s["coverage_percentage"])*100:.1f}%'
    mae = "—" if s["mae"] is None else f'{Decimal(s["mae"]):.2f}'
    rmse = "—" if s["rmse"] is None else f'{Decimal(s["rmse"]):.2f}'
    return f'<p class="metrics"><strong>{s["game_count"]:,} games</strong> · Winner accuracy {accuracy} ({s["straight_up_wins"]:,}–{s["straight_up_losses"]:,}; {s["straight_up_ties"]:,} ties)<br>CORS line coverage {coverage} ({s["covers"]:,}–{s["no_covers"]:,}; {s["pushes"]:,} pushes)<br>MAE {mae} points · RMSE {rmse} points</p>'


WEEK_COLUMNS = ("week", "game_count", "straight_up_wins", "straight_up_losses", "straight_up_ties", "covers", "no_covers", "pushes", "mae", "rmse", *COUNTS)


def report_files(report: dict, *, site_links: bool = False) -> dict[str, bytes]:
    year, summary = report["season"], report["summary"]
    navigation = '<a href="../index.html">All seasons</a> · <a href="../../cfb.html">CORS home</a>' if site_links else "CORS model performance"
    body = '<p>Saved CORS spreads compared with final FBS-versus-FBS scores. Original spread rounding is preserved. Missing predictions are reconstructed from preceding ratings where available.</p>' + _metrics(summary)
    body += f'<p>{summary["saved_predictions"]:,} saved predictions · {summary["reconstructed_predictions"]:,} reconstructed · {summary["missing_predictions"]:,} unavailable · {summary["missing_scores"]:,} pending scores · {summary["non_fbs_games"]:,} non-FBS games excluded.</p>'
    body += '<p>CORS line coverage measures whether the selected favorite covered the CORS spread. Pushes are reported separately; zero lines have no winner selection. MAE/RMSE use every evaluated margin, including ties and zero lines. Lower margin error is better.</p>'
    body += '<p><a href="games.csv">Game CSV</a> · <a href="weekly.csv">Weekly CSV</a> · <a href="report.json">Report JSON</a></p><h2>Weekly performance</h2>' + _table(report["weekly"], WEEK_COLUMNS)
    body += '<h2>Game results</h2>' + _table(report["games"], GAME_COLUMNS)
    if site_links:
        body += '<details><summary>Saved inputs</summary><ul>'+"".join(f'<li><a href="../../../{escape(s["path"], quote=True)}">{escape(s["path"])}</a></li>' for s in report["sources"])+"</ul></details>"
    return {"index.html": _page(f"{year} CORS model performance", body, navigation),
            "report.json": (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False)+"\n").encode(),
            "games.csv": _csv(report["games"], GAME_COLUMNS).encode(),
            "weekly.csv": _csv(report["weekly"], WEEK_COLUMNS).encode()}


def build_performance_files(website: Path) -> dict[str, bytes]:
    output, summaries, grades = {}, [], []
    for year in seasons(website):
        report, evaluated = evaluate_season(website, year)
        grades.extend(evaluated)
        summaries.append({"season": year, **report["summary"]})
        for name, raw in report_files(report, site_links=True).items():
            output[f"{PREFIX}/{year}/{name}"] = raw
    totals = {**_summary(grades), **{k: sum(s[k] for s in summaries) for k in COUNTS}}
    body = '<p>Explore model performance across every season in the archive. Saved predictions keep their original values; reconstructed gaps are counted separately. This uses the existing site data without rerunning historical rankings.</p>'+_metrics(totals)
    body += f'<p>{len(summaries)} seasons · {totals["saved_predictions"]:,} saved predictions · {totals["reconstructed_predictions"]:,} reconstructed · {totals["missing_predictions"]:,} unavailable.</p><p><a href="seasons.csv">Download season summaries</a></p>'
    body += _table(list(reversed(summaries)), ("season", *WEEK_COLUMNS[1:]), season_links=True)
    output[f"{PREFIX}/index.html"] = _page("CORS model performance", body, '<a href="../cfb.html">CORS home</a>')
    output[f"{PREFIX}/seasons.csv"] = _csv(summaries, ("season", *WEEK_COLUMNS[1:])).encode()
    # Add one discoverable entry point; preserve the rest of this legacy page.
    nav = website / NAVIGATION
    if nav.exists():
        raw = nav.read_bytes()
        output[NAVIGATION] = raw if NAV_LINK in raw else raw + b"\n" + NAV_LINK
    return output


def write_performance(website: Path) -> tuple[str, ...]:
    files = build_performance_files(website)
    for relative, raw in files.items():
        path = website / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return tuple(sorted(files))


def validate_performance(website: Path) -> tuple[str, ...]:
    expected = build_performance_files(website)
    if NAVIGATION in expected and (website / NAVIGATION).read_bytes().count(NAV_LINK) != 1:
        raise ValueError("CORS home must link performance exactly once")
    actual_paths = {p.relative_to(website).as_posix() for p in (website / PREFIX).rglob("*") if p.is_file()}
    if actual_paths != set(expected) - {NAVIGATION}:
        raise ValueError("performance report paths differ from available seasons")
    for relative, raw in expected.items():
        path = website / relative
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"performance output differs from saved inputs: {relative}")
    return tuple(sorted(expected))
