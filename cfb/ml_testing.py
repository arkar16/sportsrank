"""Offline, expanding-season experiments; never used by production forecasts."""
from __future__ import annotations

import csv
import hashlib
from io import StringIO
import json
from pathlib import Path

import numpy as np

from .performance import evaluate_season, seasons

SCHEMA = "sportsrank-ml-testing/v1"
GAME_COLUMNS = ("season", "week", "home", "away", "game_id", "actual_home_margin",
                "cors_home_margin", "calibration_home_margin", "zero_margin")


def _metrics(rows: list[dict], key: str) -> dict:
    errors = np.array([row[key] - row["actual_home_margin"] for row in rows])
    return {"mae": float(np.mean(np.abs(errors))),
            "rmse": float(np.sqrt(np.mean(errors ** 2)))}


def _summary(rows: list[dict]) -> dict:
    return {"game_count": len(rows),
            **{name: _metrics(rows, key) for name, key in (
                ("cors", "cors_home_margin"), ("calibration", "calibration_home_margin"),
                ("zero_margin", "zero_margin"))}}


def experiment(website: Path, *, from_season: int | None = None,
               through_season: int | None = None, min_training_seasons: int = 2,
               min_training_games: int = 100) -> dict:
    """Fit actual margin = intercept + slope * CORS using earlier saved forecasts.

    All completed saved FBS forecasts in a season form one indivisible test fold.
    The test season never participates in its fit, even when a season is partial.
    Reconstructions are counted but excluded from both training and evaluation.
    """
    if min_training_seasons < 1 or min_training_games < 2:
        raise ValueError("require at least one training season and two training games")
    if from_season is not None and through_season is not None and from_season > through_season:
        raise ValueError("from-season must not exceed through-season")
    available = [year for year in seasons(website)
                 if through_season is None or year <= through_season]
    training, training_seasons, folds, skipped, games, coverage = [], [], [], [], [], []
    sources = {}
    for year in available:
        report, _ = evaluate_season(website, year)
        rows = []
        for row in report["games"]:
            if row["status"] != "Evaluated" or row["prediction_source"] != "Saved spread":
                continue
            margin, actual = -float(row["home_handicap"]), float(row["actual_home_margin"])
            if not np.isfinite([margin, actual]).all():
                raise ValueError("non-finite experiment input")
            rows.append({"season": year, "week": row["week"], "home": row["home"],
                         "away": row["away"], "game_id": row.get("game_id"),
                         "actual_home_margin": actual, "cors_home_margin": margin})
        coverage.append({"season": year, "eligible_saved_games": len(rows),
                         **{key: report["summary"][key] for key in (
                             "reconstructed_predictions", "missing_predictions", "missing_scores", "non_fbs_games")}})
        for source in report["sources"]:
            sources[source["path"]] = source["sha256"]
        if from_season is None or year >= from_season:
            reason = None
            if not rows:
                reason = "no completed saved forecasts"
            elif len(training_seasons) < min_training_seasons or len(training) < min_training_games:
                reason = "insufficient earlier-season training data"
            else:
                x = np.array([[1.0, row["cors_home_margin"]] for row in training])
                y = np.array([row["actual_home_margin"] for row in training])
                coefficients, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
                if rank < 2:
                    reason = "earlier forecasts have no usable margin variation"
                elif not np.isfinite(coefficients).all():
                    raise ValueError("non-finite fitted coefficients")
                else:
                    intercept, slope = map(float, coefficients)
                    predicted = [{**row, "calibration_home_margin": intercept + slope * row["cors_home_margin"],
                                  "zero_margin": 0.0} for row in rows]
                    if not all(np.isfinite(row["calibration_home_margin"]) for row in predicted):
                        raise ValueError("non-finite fitted forecast")
                    folds.append({"season": year, "training_seasons": list(training_seasons),
                                  "training_game_count": len(training), "intercept": intercept,
                                  "slope": slope, "summary": _summary(predicted)})
                    games.extend(predicted)
            if reason is not None:
                skipped.append({"season": year, "reason": reason,
                                "training_game_count": len(training)})
        # Append only AFTER scoring: the current season cannot train itself.
        training.extend(rows)
        if rows:
            training_seasons.append(year)
    if not games:
        raise ValueError("no held-out games: need saved forecasts and sufficient earlier-season history")
    source_rows = [{"path": path, "sha256": digest} for path, digest in sorted(sources.items())]
    return {"schema": SCHEMA, "model": "linear-cors-calibration/v1", "numpy_version": np.__version__,
            "config": {"from_season": from_season, "through_season": through_season,
                       "min_training_seasons": min_training_seasons, "min_training_games": min_training_games,
                       "forecast_policy": "saved_only", "split_policy": "expanding_earlier_seasons"},
            "sources": source_rows,
            "source_manifest_sha256": hashlib.sha256(json.dumps(source_rows, sort_keys=True).encode()).hexdigest(),
            "coverage": coverage, "skipped_folds": skipped, "folds": folds,
            "summary": _summary(games), "games": games}


def write_experiment(report: dict, output: Path, website: Path, *, protected_roots: tuple[Path, ...] = ()) -> None:
    """Write a new local experiment, refusing any overlap with the source tree."""
    destination = output.resolve()
    for root in (website, *protected_roots):
        source = root.resolve()
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise ValueError("experiment output must be outside and separate from the website and input roots")
    raw = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    stream = StringIO()
    columns = GAME_COLUMNS if report["schema"] == SCHEMA else tuple(report["games"][0])
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(report["games"])
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(raw, encoding="utf-8")
    (output / "games.csv").write_text(stream.getvalue(), encoding="utf-8")
