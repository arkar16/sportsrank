"""Basketball rest differential using only games observable at the rating cutoff."""
from bisect import bisect_left
from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


class RestHistory:
    def __init__(self, games):
        self.starts = defaultdict(list)
        for game in games:
            if game.score is not None:
                self.starts[game.home_team_id].append(game.start_utc)
                self.starts[game.away_team_id].append(game.start_utc)
        for starts in self.starts.values():
            starts.sort()

    def differential(self, home_id, away_id, cutoff):
        # Cache contains tipoffs and eventual final status, without final timestamps.
        bound = cutoff - timedelta(hours=6)
        previous = []
        for team_id in (home_id, away_id):
            starts = self.starts[team_id]
            index = bisect_left(starts, bound)
            if not index:
                return None
            previous.append(starts[index - 1].astimezone(EASTERN).date())
        return (previous[1] - previous[0]).days
