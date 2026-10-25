"""Paired, earlier-season-only model comparisons using private starter-pack data."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np

from .performance import evaluate_season, seasons
from .starter_pack import AVAILABILITY_HOURS, FEATURES, MIN_HISTORY, load_features

MODELS = {"calibration": ("cors_home_margin",), "advanced": FEATURES,
          "combined": ("cors_home_margin", *FEATURES)}
RIDGE_ALPHA = 1.0


def _summary(rows: list[dict]) -> dict:
    result = {"game_count": len(rows)}
    for model in ("cors", *MODELS):
        errors = np.array([row[f"{model}_home_margin"] - row["actual_home_margin"] for row in rows])
        result[model] = {"mae": float(np.mean(np.abs(errors))), "rmse": float(np.sqrt(np.mean(errors ** 2)))}
    return result


def _fit(training: list[dict], rows: list[dict], fields: tuple[str, ...], alpha: float) -> tuple[list, dict]:
    x = np.array([[row[name] for name in fields] for row in training])
    y = np.array([row["actual_home_margin"] for row in training])
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1.0
    design = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    penalty = np.eye(design.shape[1]) * np.sqrt(alpha)
    penalty[0, 0] = 0  # Intercept is not regularized.
    coefficients = np.linalg.lstsq(np.vstack((design, penalty)), np.concatenate((y, np.zeros(len(penalty)))), rcond=None)[0]
    test = np.array([[row[name] for name in fields] for row in rows])
    prediction = np.column_stack((np.ones(len(test)), (test - mean) / scale)) @ coefficients
    if not np.isfinite(prediction).all() or not np.isfinite(coefficients).all():
        raise ValueError("non-finite fitted forecast")
    return prediction.tolist(), {"features": list(fields), "means": mean.tolist(), "scales": scale.tolist(),
                                 "intercept": float(coefficients[0]), "standardized_coefficients": coefficients[1:].tolist(),
                                 "ridge_alpha": alpha}


def experiment(website: Path, starter_pack: Path, *, from_season: int | None = None,
               through_season: int | None = None, min_training_seasons: int = 2,
               min_training_games: int = 100) -> dict:
    if min_training_seasons < 1 or min_training_games < 2:
        raise ValueError("require at least one training season and two training games")
    if from_season is not None and through_season is not None and from_season > through_season:
        raise ValueError("from-season must not exceed through-season")
    data = load_features(starter_pack, through_season=through_season)
    available = set(seasons(website))
    by_season = defaultdict(list)
    for row in data["games"]:
        by_season[row["season"]].append(row)
    training, training_seasons, games, folds, skipped, joins = [], [], [], [], [], []
    sources = list(data["sources"])
    for year, candidates in sorted(by_season.items()):
        counts = Counter(feature_games=len(candidates))
        if year not in available:
            joins.append({"season": year, **counts, "missing_site_season": len(candidates)})
            continue
        report, _ = evaluate_season(website, year)
        sources.extend({"source": "website", **source} for source in report["sources"])
        by_id, by_pair = {}, defaultdict(list)
        for forecast in report["games"]:
            if forecast["status"] != "Evaluated" or forecast["prediction_source"] != "Saved spread":
                continue
            key = (forecast["home"], forecast["away"], forecast["neutral_site"])
            by_pair[key].append(forecast)
            identifier = str(forecast.get("game_id", ""))
            if identifier.isdecimal():
                if identifier in by_id:
                    raise ValueError("duplicate saved forecast game id")
                by_id[identifier] = forecast
        paired = []
        for row in candidates:
            key = (row["home"], row["away"], row["neutral_site"])
            forecast = by_id.get(row["game_id"])
            method = "game_id"
            if forecast is None:
                matches = by_pair[key]
                if len(matches) != 1 or row["season_matchup_count"] != 1:
                    counts["ambiguous_identity" if matches else "missing_saved_forecast"] += 1
                    continue
                forecast = matches[0]
                # A conflicting explicit provider ID is never replaced by a name join.
                if str(forecast.get("game_id", "")).isdecimal():
                    counts["conflicting_game_id"] += 1
                    continue
                method = "unique_teams_and_orientation"
            if key != (forecast["home"], forecast["away"], forecast["neutral_site"]):
                counts["conflicting_identity"] += 1
                continue
            if (row["home_score"], row["away_score"]) != (forecast["home_score"], forecast["away_score"]):
                counts["conflicting_scores"] += 1
                continue
            margin = -float(forecast["home_handicap"])
            if not np.isfinite(margin):
                raise ValueError("non-finite CORS forecast")
            paired.append({**row, "week": forecast["week"], "cors_join": method, "cors_home_margin": margin})
            counts[method] += 1
        counts["paired_games"] = len(paired)
        joins.append({"season": year, **dict(sorted(counts.items()))})
        if from_season is None or year >= from_season:
            if not paired or len(training_seasons) < min_training_seasons or len(training) < min_training_games:
                skipped.append({"season": year, "reason": "insufficient paired games or earlier-season history",
                                "paired_games": len(paired), "training_game_count": len(training)})
            else:
                models = {}
                predictions = [dict(row) for row in paired]
                for model, fields in MODELS.items():
                    values, models[model] = _fit(training, paired, fields, 0.0 if model == "calibration" else RIDGE_ALPHA)
                    for row, value in zip(predictions, values):
                        row[f"{model}_home_margin"] = value
                folds.append({"season": year, "training_seasons": list(training_seasons),
                              "training_game_count": len(training), "models": models, "summary": _summary(predictions)})
                games.extend(predictions)
        training.extend(paired)
        if paired:
            training_seasons.append(year)
    if not games:
        raise ValueError("no held-out games: need paired starter-pack features, saved CORS and earlier seasons")
    sources.sort(key=lambda row: (row["source"], row["path"]))
    return {"schema": "sportsrank-starter-experiment/v1", "model": "prior-game-ridge/v1", "numpy_version": np.__version__,
            "config": {"from_season": from_season, "through_season": through_season,
                       "min_training_seasons": min_training_seasons, "min_training_games": min_training_games,
                       "min_prior_team_games": MIN_HISTORY, "availability_hours": AVAILABILITY_HOURS,
                       "feature_cutoff": "Monday 00:00 UTC of target kickoff week",
                       "timestamp_policy": "naive timestamps are UTC; blank legacy TBD flags use recorded timestamps",
                       "feature_policy": "equal-game means of same-season completed FBS-vs-FBS games",
                       "forecast_policy": "saved_only", "split_policy": "expanding_earlier_seasons",
                       "ridge_alpha": RIDGE_ALPHA},
            "sources": sources, "source_manifest_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
            "coverage": data["coverage"], "cors_join_coverage": joins, "skipped_folds": skipped,
            "folds": folds, "summary": _summary(games), "games": games}
