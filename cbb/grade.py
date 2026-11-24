"""Grade paired home margins for one frozen basketball model."""
from dataclasses import dataclass
from math import fsum, isfinite, sqrt


@dataclass(frozen=True)
class MarginGrade:
    games: int
    rmse: float
    correct_picks: int
    picks: int
    pick_ems: int

    @property
    def winner_accuracy(self):
        return self.correct_picks / self.picks if self.picks else None


def grade_margins(pairs):
    """Pick'ems count in RMSE and yield no winner selection."""
    errors = []
    picks = correct = 0
    for prediction, result in pairs:
        if not isfinite(prediction) or not isfinite(result) or result == 0:
            raise ValueError("Expected a finite forecast and a decided basketball game")
        errors.append((prediction - result) ** 2)
        if prediction != 0:
            picks += 1
            correct += (prediction > 0) == (result > 0)
    if not errors:
        raise ValueError("No completed games to grade")
    return MarginGrade(len(errors), sqrt(fsum(errors) / len(errors)), correct, picks, len(errors) - picks)


@dataclass(frozen=True)
class PerformanceGrade:
    games: int
    wins: int
    losses: int
    pick_ems: int
    covers: int
    no_covers: int
    pushes: int
    mae: float | None
    rmse: float | None

    @property
    def picks(self):
        return self.wins + self.losses

    @property
    def winner_accuracy(self):
        return self.wins / self.picks if self.picks else None

    @property
    def coverage_decisions(self):
        return self.covers + self.no_covers

    @property
    def line_coverage(self):
        return self.covers / self.coverage_decisions if self.coverage_decisions else None


def grade_game(prediction, result):
    """Return (straight_up, line_coverage, abs_error) for one completed game."""
    if not isfinite(prediction) or not isfinite(result) or result == 0:
        raise ValueError("Expected a finite forecast and a decided basketball game")
    abs_err = abs(prediction - result)
    if prediction == 0:
        return "pick_em", "no_pick", abs_err
    straight_up = "correct" if (prediction > 0) == (result > 0) else "incorrect"
    if prediction > 0:
        coverage = "cover" if result > prediction else "push" if result == prediction else "no_cover"
    else:
        coverage = "cover" if result < prediction else "push" if result == prediction else "no_cover"
    return straight_up, coverage, abs_err


def grade_performance(pairs):
    """Compute straight-up, CORS line coverage, MAE, and RMSE over (prediction, actual) pairs."""
    abs_errors = []
    sq_errors = []
    wins = losses = pick_ems = covers = no_covers = pushes = 0
    for prediction, result in pairs:
        su, cov, err = grade_game(prediction, result)
        abs_errors.append(err)
        sq_errors.append(err * err)
        if su == "correct":
            wins += 1
        elif su == "incorrect":
            losses += 1
        else:
            pick_ems += 1
        if cov == "cover":
            covers += 1
        elif cov == "no_cover":
            no_covers += 1
        elif cov == "push":
            pushes += 1
    if not abs_errors:
        return PerformanceGrade(0, 0, 0, 0, 0, 0, 0, None, None)
    n = len(abs_errors)
    return PerformanceGrade(
        n, wins, losses, pick_ems, covers, no_covers, pushes,
        fsum(abs_errors) / n, sqrt(fsum(sq_errors) / n),
    )
