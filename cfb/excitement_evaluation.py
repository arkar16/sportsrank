"""Offline whole-season evaluation preparation for ADR-0021.

Reports measure the accepted estimator; they never authorize a production
reference artifact. Callers must preserve an untouched evaluation partition if
these results inform tuning, and supply independently agreed release criteria.
No sources are acquired and no reference artifact is persisted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import ceil, sqrt
from statistics import fmean
from typing import Iterable, Literal

from .excitement import (
    SCORING_VERSION,
    ExcitementInputError,
    QualifiedFinal,
    QualifiedPregame,
    TimelinePoint,
    calculate_full_aev,
)
from .excitement_reference import (
    estimate_aev,
    fit_reference_artifact,
    reference_game_from_timeline,
)

EVALUATION_VERSION = "excitement-whole-season-evaluation-v1"


@dataclass(frozen=True)
class EvaluationGame:
    """One independently qualified complete game, before evidence is hidden."""

    pregame: QualifiedPregame
    final: QualifiedFinal
    timeline: tuple[TimelinePoint, ...]


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    a, b = fmean(left), fmean(right)
    covariance = sum((x - a) * (y - b) for x, y in zip(left, right))
    variance = sum((x - a) ** 2 for x in left) * sum((y - b) ** 2 for y in right)
    return covariance / sqrt(variance) if variance else None


def _ranks(values: list[float]) -> list[float]:
    """Average ranks preserve ties instead of resolving them by game identity."""
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        rank = (start + 1 + end) / 2
        for index, _ in ordered[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def _highest(rows: list[dict], field: str, fraction: float) -> set[str]:
    """Include all ties at the chosen top-fraction cutoff."""
    if not rows:
        return set()
    cutoff = sorted((row[field] for row in rows), reverse=True)[ceil(len(rows) * fraction) - 1]
    return {row["game_id"] for row in rows if row[field] >= cutoff}


def summarize_scores(rows: Iterable[dict], *, top_fraction: float = 0.1) -> dict:
    """Summarize prediction rows, including a matched final-margin baseline."""
    if not 0 < top_fraction <= 1:
        raise ExcitementInputError("top_fraction must be in (0, 1]")
    values = list(rows)
    observed = [row["observed"] for row in values]
    truth_top = _highest(values, "observed", top_fraction)
    result: dict = {"count": len(values), "top_fraction": top_fraction}
    for field in ("estimate", "baseline"):
        predictions = [row[field] for row in values]
        errors = [prediction - truth for prediction, truth in zip(predictions, observed)]
        predicted_top = _highest(values, field, top_fraction)
        result[field] = {
            "signed_bias": fmean(errors) if errors else None,
            "mean_absolute_error": fmean(abs(error) for error in errors) if errors else None,
            "root_mean_square_error": sqrt(fmean(error ** 2 for error in errors)) if errors else None,
            "score_correlation": _correlation(predictions, observed),
            "rank_correlation": _correlation(_ranks(predictions), _ranks(observed)),
            "highest_observed_count": len(truth_top),
            "highest_predicted_count": len(predicted_top),
            "entered_highest": sorted(predicted_top - truth_top),
            "left_highest": sorted(truth_top - predicted_top),
        }
    return result


def _baseline(
    target: EvaluationGame, training: tuple[EvaluationGame, ...], measurements: dict,
    *, hide_overtime: bool,
) -> float:
    """Final-margin-only nearest-50 comparator with the same OT target rules."""
    pool = training
    if not hide_overtime:
        same = tuple(game for game in training if game.final.overtime == target.final.overtime)
        pool = same or training
    margin = abs(target.final.home_score - target.final.away_score)
    distances = sorted(
        (abs(abs(game.final.home_score - game.final.away_score) - margin), game.final.game_id, game)
        for game in pool
    )
    cutoff = distances[min(50, len(distances)) - 1][0]
    neighbors = [game for distance, _, game in distances if distance <= cutoff]
    drama = []
    for game in neighbors:
        score = measurements[game.final.game_id]
        points = score.components.drama_points
        if hide_overtime:
            points += 2 * int(game.final.overtime)
        drama.append(points)
    actual = measurements[target.final.game_id]
    bonus = 3 * actual.components.quality + 3 * actual.components.surprise
    return fmean(drama) + bonus + (0 if hide_overtime else 2 * int(target.final.overtime))


def evaluate_season_holdouts(
    games: Iterable[EvaluationGame],
    *,
    data_kind: Literal["synthetic", "qualified_retained"],
    training_seasons: Iterable[int] | None = None,
    evaluation_seasons: Iterable[int] | None = None,
    reservation_id: str | None = None,
    top_fraction: float = 0.1,
) -> dict:
    """Fit solely on training seasons and hide each test game's richer inputs.

    With no partitions, run leave-one-season-out development folds. Explicit
    disjoint partitions run a reserved-season evaluation; ``reservation_id``
    references the externally frozen partition and criteria, not a claim this
    function can verify that no human previously inspected those data.
    """
    if data_kind not in ("synthetic", "qualified_retained"):
        raise ExcitementInputError("evaluation requires explicit evidence kind")
    if not 0 < top_fraction <= 1:
        raise ExcitementInputError("top_fraction must be in (0, 1]")
    cases = tuple(sorted(games, key=lambda game: (game.final.season, game.final.game_id)))
    if not cases:
        raise ExcitementInputError("no qualified complete games for evaluation")
    ids = [game.final.game_id for game in cases]
    if len(set(ids)) != len(ids):
        raise ExcitementInputError("duplicate game identity in evaluation")
    # Validate every full-data target before deriving any fold or feature scale.
    truths = {}
    references = {}
    for game in cases:
        if game.final.overtime is None:
            raise ExcitementInputError("full-data evaluation requires verified overtime status")
        truths[game.final.game_id] = calculate_full_aev(game.pregame, game.final, game.timeline)
        references[game.final.game_id] = reference_game_from_timeline(game.pregame, game.final, game.timeline)
    seasons = {game.final.season for game in cases}
    if (training_seasons is None) != (evaluation_seasons is None):
        raise ExcitementInputError("supply both training and evaluation season partitions")
    if training_seasons is None:
        if len(seasons) < 2:
            raise ExcitementInputError("whole-season holdout requires at least two seasons")
        folds = [(season, seasons - {season}) for season in sorted(seasons)]
        mode = "whole_season_development"
        if reservation_id is not None:
            raise ExcitementInputError("reservation_id requires explicit season partitions")
    else:
        train, test = set(training_seasons), set(evaluation_seasons)
        if not train or not test or train & test:
            raise ExcitementInputError("training and evaluation seasons must be nonempty and disjoint")
        if not (train | test) <= seasons:
            raise ExcitementInputError("partition names a season with no qualified games")
        if not isinstance(reservation_id, str) or not reservation_id.strip():
            raise ExcitementInputError("reserved evaluation requires a frozen partition/criteria identity")
        folds = [(season, train) for season in sorted(test)]
        mode = "reserved_season_evaluation"
    reports = []
    for season, train_seasons in folds:
        training = tuple(game for game in cases if game.final.season in train_seasons)
        testing = tuple(game for game in cases if game.final.season == season)
        artifact = fit_reference_artifact(
            (references[game.final.game_id] for game in training),
            artifact_id=f"{EVALUATION_VERSION}:holdout-{season}",
        )
        variants = []
        for tier in ("final", "quarter"):
            eligible = tuple(
                game for game in testing
                if tier == "final" or (game.final.quarter_scores is not None and game.final.home_score != game.final.away_score)
            )
            for hide_overtime in (False, True):
                rows = []
                unavailable_games = [
                    {"game_id": game.final.game_id, "reason": "valid winner-oriented quarter checkpoints unavailable"}
                    for game in testing if game not in eligible
                ]
                has_reference_tier = tier == "final" or any(row.has_quarters for row in artifact.games)
                for game in eligible:
                    if not has_reference_tier:
                        unavailable_games.append({
                            "game_id": game.final.game_id,
                            "reason": "training seasons contain no qualified quarter-tier references",
                        })
                        continue
                    limited = replace(
                        game.final,
                        quarter_scores=game.final.quarter_scores if tier == "quarter" else None,
                        overtime=None if hide_overtime else game.final.overtime,
                    )
                    estimate = estimate_aev(game.pregame, limited, artifact)
                    rows.append({
                        "game_id": game.final.game_id,
                        "observed": truths[game.final.game_id].value,
                        "estimate": estimate.value,
                        "baseline": _baseline(game, training, truths, hide_overtime=hide_overtime),
                        "overtime": game.final.overtime,
                        "support": estimate.evidence.reference_support,
                        "cutoff_distance": estimate.evidence.reference_cutoff_distance,
                        "reference_basis": estimate.evidence.reference_pool,
                    })
                metrics = summarize_scores(rows, top_fraction=top_fraction)
                subgroups = {
                    "overtime": {
                        str(status).lower(): summarize_scores([row for row in rows if row["overtime"] == status], top_fraction=top_fraction)
                        for status in (False, True)
                    },
                    "support": {
                        group: summarize_scores([row for row in rows if (row["support"] < 50) == (group == "below_50")], top_fraction=top_fraction)
                        for group in ("below_50", "at_least_50")
                    },
                }
                variants.append({
                    "tier": tier,
                    "overtime_input": "hidden" if hide_overtime else "known",
                    "eligible": len(eligible),
                    "scored": len(rows),
                    "unavailable": len(unavailable_games),
                    "unavailable_games": unavailable_games,
                    "metrics": metrics,
                    "subgroups": subgroups,
                    "largest_errors": sorted(rows, key=lambda row: (-abs(row["estimate"] - row["observed"]), row["game_id"]))[:10],
                    "rows": rows,
                })
        reports.append({
            "evaluation_season": season,
            "training_seasons": sorted(train_seasons),
            "training_games": len(training),
            "evaluation_games": len(testing),
            "training_game_ids": [game.final.game_id for game in training],
            "evaluation_game_ids": [game.final.game_id for game in testing],
            "reference_identity": artifact.checksum,
            "reference_artifact_id": artifact.artifact_id,
            "variants": variants,
        })
    return {
        "evaluation_version": EVALUATION_VERSION,
        "scoring_version": SCORING_VERSION,
        "data_kind": data_kind,
        "mode": mode,
        "reservation_id": reservation_id,
        "production_accepted": False,
        "acceptance_gate": "Requires separately agreed numerical criteria, sufficient references, and untouched empirical evidence; this report grants no acceptance.",
        "baseline": "Unweighted nearest-50 absolute-final-margin neighbors including cutoff ties; same overtime pool/target branches; observed bonuses separate.",
        "input_games": len(cases),
        "folds": reports,
    }
