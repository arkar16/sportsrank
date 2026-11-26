"""Attach exact football game contributions only to matching archived checkpoints."""
from dataclasses import replace
from decimal import Decimal

from .cors_v1 import CompletedGame, SeasonInput, replay_seasons


def _input(game):
    return CompletedGame(game.week, game.home, game.home_division.lower(), game.home_score,
                         game.away, game.away_division.lower(), game.away_score, game.neutral_site)


def _matches(archived, replayed):
    return bool(archived) and {r.school: (r.rank, r.rating, r.record) for r in archived} == {
        r["school"]: (r["rank"], Decimal(r["cors"]), r["record"]) for r in replayed
    }


def attach_football_impacts(seasons, replays=None):
    if replays is None:
        inputs = []
        for season in seasons:
            members = {row.school: row.conference for rows in season.rankings.values() for row in rows}
            games = tuple(_input(g) for g in season.games
                          if g.home_score is not None and g.away_score is not None)
            for game in games:
                if game.home_division == "fbs":
                    members.setdefault(game.home_team, "")
                if game.away_division == "fbs":
                    members.setdefault(game.away_team, "")
            inputs.append(SeasonInput(season.year, tuple(members.items()), games))
        replays, _ = replay_seasons(inputs)

    for season, replay in zip(seasons, replays, strict=True):
        if season.year != replay.year:
            raise ValueError("Football replay and archive years differ")
        # The record book can also be built on partial or legacy archives.
        # Publish an attribution only when the replay matches the source edition.
        matching = set()
        previous_matches = _matches(season.rankings.get("PRESEASON"), replay.preseason)
        for week, rows in replay.weekly_rankings.items():
            # W0 doubles as a preseason alias in older archives with no Week 0 games.
            if week == 0 and "W0" not in season.rankings and rows == replay.preseason:
                continue
            current_matches = _matches(season.rankings.get(f"W{week}"), rows)
            if previous_matches and current_matches:
                matching.add(week)
            previous_matches = current_matches
        impacts = {replace(g, game_id="", game_date=""): changes
                   for g, changes in replay.game_impacts.items()}
        for index, game in enumerate(season.games):
            if game.week in matching and game.home_score is not None and game.away_score is not None:
                changes = impacts.get(_input(game), {})
                season.games[index] = replace(game, home_cors_delta=changes.get(game.home),
                                              away_cors_delta=changes.get(game.away))
