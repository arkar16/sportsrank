"""Convert CORS scoring margins to estimated outright win chances."""

from math import atan, cos, sin, sqrt


_MARGIN_ERROR = {"football": 15.0, "basketball": 11.0}


def win_probability(margin, *, sport: str) -> float:
    """Use a variance-matched t(14) for the expected team scoring margin."""
    sigma = _MARGIN_ERROR[sport]
    # With z = sqrt(14) tan(theta), the t(14) density is proportional
    # to cos(theta)^13. Integrate by reducing the odd powers of cosine.
    theta = atan(float(margin) / (sigma * sqrt(12 / 14) * sqrt(14)))
    sine, cosine = sin(theta), cos(theta)
    integral, half_integral = sine, 1.0
    for power in range(3, 14, 2):
        integral = (sine * cosine ** (power - 1) + (power - 1) * integral) / power
        half_integral *= (power - 1) / power
    return min(1.0, max(0.0, 0.5 + integral / (2 * half_integral)))


def format_win_chance(handicap, *, sport: str) -> str:
    """Format a team handicap, negative when favored, as a whole percentage."""
    return "—" if handicap is None else f"{win_probability(-handicap, sport=sport):.0%}"
