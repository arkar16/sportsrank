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
