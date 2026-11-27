"""Fixed letter grades for the public one-decimal game excitement scores."""
from bisect import bisect_right
from functools import lru_cache


LETTERS = ("F", "D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+")
# Inclusive lower bounds, D- through A+. Fit once on ten completed seasons.
MINIMUMS = {
    ("cfb", "BEV"): (17.8, 21.4, 24.8, 31.6, 37.9, 45.6, 50.0, 56.3, 64.8, 70.7, 78.3, 89.6),
    ("cfb", "AEV"): (18.3, 22.1, 25.5, 32.2, 38.5, 48.0, 54.7, 61.0, 67.2, 70.5, 74.1, 79.8),
    ("cbb", "BEV"): (1.6, 2.7, 4.1, 8.4, 14.3, 25.0, 32.1, 37.9, 41.4, 46.9, 58.6, 78.7),
    ("cbb", "AEV"): (10.8, 14.4, 17.9, 24.5, 31.3, 41.7, 48.0, 53.9, 59.7, 62.7, 66.5, 71.5),
}
SCALE_LINK = '<a href="/excitement/grades.html">Letter grade scale</a>'


@lru_cache(maxsize=4096)
def letter_grade(sport: str, metric: str, displayed_value: str) -> str:
    value = float(displayed_value)
    if not 0 <= value <= 100:
        raise ValueError(f"Invalid {metric} score: {displayed_value!r}")
    return LETTERS[bisect_right(MINIMUMS[sport, metric], value)]


def graded_number(sport: str, metric: str, number: str) -> str:
    return f"{number} ({letter_grade(sport, metric, number.removesuffix('*'))})"


def read_number(sport: str, metric: str, label: str) -> str:
    """Read a numeric field from either a graded page or a preserved number-only page."""
    number, separator, _ = label.partition(" ")
    if separator and label != graded_number(sport, metric, number):
        raise ValueError(f"{sport} {metric} grade disagrees with its score: {label!r}")
    return number
