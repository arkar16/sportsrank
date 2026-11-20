"""Grade the margins actually printed in verified public basketball editions."""
from datetime import datetime

from bs4 import BeautifulSoup

# First successful public-page check for each confirmed deployment. Preparation
# alone cannot establish that readers saw a forecast before the game started.
VERIFIED_EDITIONS = {
    "2026-11-19T185800Z": ("2026-11-19T20:10:40Z",
                           "https://github.com/arkar16/sportsrank/actions/runs/44605798674"),
}


def grade_edition(path, verified_at, fixtures):
    """Join frozen printed fixtures conservatively; ambiguous/rescheduled fixtures stay ungraded."""
    page = BeautifulSoup(path.read_bytes(), "html.parser")
    heading = page.find("h2", string="Game forecasts")
    if heading is None:
        return [], 0, 0
    eligible = []
    unresolved = late = 0
    for row in heading.find_next("table").select("tbody tr"):
        tipoff, away, home, pick, line = (cell.get_text(strip=True) for cell in row.select("td"))
        matches = fixtures.get((tipoff, away, home), ())
        if len(matches) != 1:
            unresolved += 1
            continue
        game, score_available = matches[0]
        if datetime.fromisoformat(game["start_utc"]) <= verified_at:
            late += 1
            continue
        if not score_available:
            unresolved += 1
            continue
        if pick not in (away, home, "Pick’em"):
            raise ValueError("Unrecognized pick in retained basketball edition")
        margin = float(line)
        if (pick == "Pick’em") != (margin == 0) or margin < 0:
            raise ValueError("Invalid printed basketball margin")
        eligible.append((margin if pick == home else -margin, game["actual_home_margin"]))
    return eligible, unresolved, late
