"""Score cached, chronologically replayed CBB forecasts through 2024.

Replay must apply each candidate's settings relative to the frozen model and
produce each prediction and rest differential from information available before
tipoff. The scorer checks pairing and enforces season boundaries for tuning.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import fsum, isfinite, sqrt
from typing import Literal


@dataclass(frozen=True)
class Candidate:
    home_advantage_delta: float = 0.0  # Points added to the frozen home advantage.
    preseason_carryover_scale: float = 1.0
    margin_weight_scale: float = 1.0


# Change one factor at a time. Order breaks ties, starting with the frozen model.
BASELINE = Candidate()
CANDIDATE_GRID = (
    BASELINE,
    Candidate(home_advantage_delta=-1.0),
    Candidate(home_advantage_delta=1.0),
    Candidate(preseason_carryover_scale=0.5),
    Candidate(preseason_carryover_scale=1.5),
    Candidate(margin_weight_scale=0.5),
    Candidate(margin_weight_scale=1.5),
)
REST_DAY_LIMIT = 3.0
REST_COEFFICIENTS = (0.0, 0.25, -0.25, 0.5, -0.5)
WARMUP_SEASON = 2020
FIRST_FIT_SEASON = 2021
DEVELOPMENT_SEASONS = (2022, 2023, 2024)
LAST_DEVELOPMENT_SEASON = DEVELOPMENT_SEASONS[-1]

Strategy = Literal["baseline", "tuned", "tuned_rest"]
GameID = int | str


@dataclass(frozen=True)
class ForecastRow:
    season: int  # Season ending year.
    game_id: GameID
    predicted_home_margin: float
    actual_home_margin: float
    rest_days_diff: float | None  # Pregame home minus away days, or None if unavailable.


@dataclass(frozen=True)
class FittedModel:
    candidate: Candidate
    rest_coefficient: float = 0.0  # Points per clipped day of rest advantage.


@dataclass(frozen=True)
class Score:
    games: int
    rmse: float
    accuracy: float | None  # Fraction of decided games, or None if there are no decisions.
    pick_ems: int
    actual_ties: int


@dataclass(frozen=True)
class SeasonResult:
    season: int
    models: Mapping[Strategy, FittedModel]
    scores: Mapping[Strategy, Score]
    paired_games: int


@dataclass(frozen=True)
class Finalist:
    strategy: Strategy
    model: FittedModel
    through_season: int


@dataclass(frozen=True)
class ExperimentResult:
    seasons: tuple[SeasonResult, ...]
    pooled: Mapping[Strategy, Score]
    paired_games: int
    finalist: Finalist
    pick_em_rule: str = (
        "Count every exact zero forecast as a pick'em. Exclude pick'ems from winner accuracy. "
        "Actual ties contribute to RMSE and are excluded from the accuracy denominator."
    )


SeasonRows = dict[int, dict[GameID, ForecastRow]]


def evaluate_experiment(
    forecasts: Mapping[Candidate, Iterable[ForecastRow]],
    *,
    include_rest: bool = True,
) -> ExperimentResult:
    """Evaluate rolling strategies and choose a finalist for a later holdout.

    Supply every CANDIDATE_GRID member with identical game coverage and game
    facts across 2021–24. Optional 2020 replay rows are ignored. Rows past 2024
    are rejected. Each fold selects a candidate by pooled prior-season squared
    error, then optionally fits a rest coefficient on that candidate's prior
    forecasts. Clip rest to +/-3 days and use zero when rest is unavailable.

    The finalist's strategy has the lowest pooled 2022–24 RMSE, with ties
    favoring baseline, then tuned without rest. Refit that strategy through
    2024. Accuracy uses unrounded margin signs and the result's pick'em rule.
    """
    indexed = _index_paired_rows(forecasts)
    seasons = []
    pooled_predictions: dict[Strategy, list[tuple[float, float]]] = {}
    for season in DEVELOPMENT_SEASONS:
        models = _fit_models(indexed, season - 1, include_rest)
        scores = {}
        for strategy, model in models.items():
            predictions = [
                (row.actual_home_margin, _prediction(row, model.rest_coefficient))
                for row in indexed[model.candidate][season].values()
            ]
            scores[strategy] = _score(predictions)
            pooled_predictions.setdefault(strategy, []).extend(predictions)
        seasons.append(SeasonResult(season, models, scores, len(indexed[BASELINE][season])))

    pooled = {strategy: _score(pairs) for strategy, pairs in pooled_predictions.items()}
    winner = min(pooled, key=lambda strategy: pooled[strategy].rmse)
    final_model = FittedModel(BASELINE)
    if winner != "baseline":
        final_model = _fit_models(indexed, LAST_DEVELOPMENT_SEASON, winner == "tuned_rest")[winner]
    return ExperimentResult(
        tuple(seasons),
        pooled,
        pooled["baseline"].games,
        Finalist(winner, final_model, LAST_DEVELOPMENT_SEASON),
    )


def _index_paired_rows(
    forecasts: Mapping[Candidate, Iterable[ForecastRow]],
) -> dict[Candidate, SeasonRows]:
    if forecasts.keys() != set(CANDIDATE_GRID):
        raise ValueError("forecasts must cover exactly CANDIDATE_GRID")
    indexed = {}
    for candidate in CANDIDATE_GRID:
        seasons: SeasonRows = {
            season: {} for season in range(FIRST_FIT_SEASON, LAST_DEVELOPMENT_SEASON + 1)
        }
        for row in forecasts[candidate]:
            if type(row.season) is not int or not WARMUP_SEASON <= row.season <= LAST_DEVELOPMENT_SEASON:
                raise ValueError(f"season outside the 2020–24 development window: {row.season!r}")
            if row.season == WARMUP_SEASON:
                continue
            if type(row.game_id) not in (int, str) or row.game_id == "":
                raise ValueError(f"invalid game ID: {row.game_id!r}")
            games = seasons[row.season]
            key = (row.season, row.game_id)
            if row.game_id in games:
                raise ValueError(f"duplicate forecast for {candidate}, game {key!r}")
            if not isfinite(row.predicted_home_margin) or not isfinite(row.actual_home_margin):
                raise ValueError(f"non-finite margin for {candidate}, game {key!r}")
            if row.rest_days_diff is not None and not isfinite(row.rest_days_diff):
                raise ValueError(f"non-finite rest differential for {candidate}, game {key!r}")
            games[row.game_id] = row

        for season, games in seasons.items():
            if candidate == BASELINE:
                if not games:
                    raise ValueError(f"missing baseline games for {season}")
                continue
            reference = indexed[BASELINE][season]
            if games.keys() != reference.keys():
                missing = len(reference.keys() - games.keys())
                extra = len(games.keys() - reference.keys())
                raise ValueError(
                    f"unpaired coverage for {candidate}, {season}: {missing} missing, {extra} extra"
                )
            for game_id, row in games.items():
                baseline = reference[game_id]
                if (row.actual_home_margin, row.rest_days_diff) != (
                    baseline.actual_home_margin, baseline.rest_days_diff
                ):
                    raise ValueError(f"inconsistent game facts for {candidate}, game {(season, game_id)!r}")
        indexed[candidate] = seasons
    return indexed


def _fit_models(
    indexed: Mapping[Candidate, SeasonRows], through: int, include_rest: bool
) -> dict[Strategy, FittedModel]:
    def prior_rows(candidate: Candidate) -> Iterable[ForecastRow]:
        for season in range(FIRST_FIT_SEASON, through + 1):
            yield from indexed[candidate][season].values()

    candidate = min(CANDIDATE_GRID, key=lambda entry: _squared_error(prior_rows(entry), 0.0))
    models: dict[Strategy, FittedModel] = {
        "baseline": FittedModel(BASELINE),
        "tuned": FittedModel(candidate),
    }
    if include_rest:
        coefficient = min(
            REST_COEFFICIENTS,
            key=lambda value: _squared_error(prior_rows(candidate), value),
        )
        models["tuned_rest"] = FittedModel(candidate, coefficient)
    return models


def _prediction(row: ForecastRow, rest_coefficient: float) -> float:
    rest = row.rest_days_diff or 0.0
    clipped = max(-REST_DAY_LIMIT, min(REST_DAY_LIMIT, rest))
    return row.predicted_home_margin + rest_coefficient * clipped


def _squared_error(rows: Iterable[ForecastRow], rest_coefficient: float) -> float:
    return fsum(
        (row.actual_home_margin - _prediction(row, rest_coefficient)) ** 2 for row in rows
    )


def _score(pairs: list[tuple[float, float]]) -> Score:
    squared_error = fsum((actual - forecast) ** 2 for actual, forecast in pairs)
    pick_ems = actual_ties = picks = correct = 0
    for actual, forecast in pairs:
        pick_ems += forecast == 0
        if actual == 0:
            actual_ties += 1
        elif forecast != 0:
            picks += 1
            correct += (actual > 0) == (forecast > 0)
    accuracy = correct / picks if picks else None
    return Score(len(pairs), sqrt(squared_error / len(pairs)), accuracy, pick_ems, actual_ties)
