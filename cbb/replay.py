"""Replay frozen CORS v0.4.0 on men's Division I basketball history.

The first slate uses preseason ratings. Each subsequent Monday at 06:00
America/New_York recomputes CORS from cumulative eligible results and the
preceding checkpoint's ratings. The first update uses divisor 1, increasing
each Monday. Each game uses the Monday cutoff strictly before tipoff.

The cache records tipoff times, without historical completion timestamps.
At a cutoff we use only final games that started strictly more than six
hours earlier. Games starting in that six-hour window enter the following
week's update. These are reconstructed projections.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import math
from pathlib import Path
from typing import TypedDict
from zoneinfo import ZoneInfo

from .source import Game, Season, read_history

GENESIS_SEASON = 2020
HOME_ADVANTAGE = 2.0
CARRYOVER_STRENGTH = 1.0
MARGIN_WEIGHT = 4.0
_REGRESSION_FACTOR = 1.75
_EASTERN = ZoneInfo("America/New_York")
_WEEK = timedelta(days=7)
_RESULT_BUFFER = timedelta(hours=6)


class RankingRow(TypedDict):
    rank: int
    team_id: int
    school: str
    conference: str
    cors: float
    wins: int
    losses: int
    win_pct: float
    mov: float
    sos: float
    expected_wins: float
    wins_vs_expected: float


class Checkpoint(TypedDict):
    week: int
    cutoff_utc: str
    rows: tuple[RankingRow, ...]


class ForecastRow(TypedDict):
    season: int
    game_id: int
    start_utc: str
    cutoff_utc: str
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    neutral_site: bool
    home_cors: float
    away_cors: float
    home_margin: float
    status: str
    home_points: int | None
    away_points: int | None
    ncaa_round: str | None
    actual_home_margin: int | None


class SeasonReplay(TypedDict):
    season: int
    rankings: tuple[Checkpoint, ...]
    final_cutoff_utc: str
    final_rankings: tuple[RankingRow, ...]
    forecasts: tuple[ForecastRow, ...]


@dataclass
class _TeamResults:
    opponents: list[int] = field(default_factory=list)
    wins: int = 0
    points_for: int = 0
    points_against: int = 0

    def add(self, opponent: int, points_for: int, points_against: int) -> None:
        self.opponents.append(opponent)
        self.wins += int(points_for > points_against)
        self.points_for += points_for
        self.points_against += points_against


def _previous_monday(instant: datetime) -> datetime:
    """Return the local Monday cutoff strictly before the UTC instant."""
    local_day = instant.astimezone(_EASTERN).date()
    monday = local_day - timedelta(days=local_day.weekday())
    cutoff = datetime.combine(monday, time(6), _EASTERN)
    return cutoff if cutoff < instant else cutoff - _WEEK


def _iso_utc(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).isoformat()


def _rank(rows: list[RankingRow]) -> tuple[RankingRow, ...]:
    rows.sort(key=lambda row: (
        -row["cors"], -row["wins"], row["losses"], row["school"], row["team_id"],
    ))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return tuple(rows)


def _rankings(
    season: Season,
    results: dict[int, _TeamResults],
    previous_cors: dict[int, float],
    week: int,
    margin_weight: float,
) -> tuple[RankingRow, ...]:
    # Port of cfb.ranking_engine's v0.4.0 scoring and rounding boundaries.
    cors_max = 50 + math.log10(31) * margin_weight * 1.2
    rows: list[RankingRow] = []
    for team_id, team in season.teams.items():
        stats = results[team_id]
        played = len(stats.opponents)
        win_pct = mov = sos = expected = 0.0
        if played:
            win_pct = round(stats.wins / played, 2)
            mov = (stats.points_for - stats.points_against) / played
            sos = round(sum(previous_cors[opponent] for opponent in stats.opponents) / played, 2)
            scored = stats.points_for ** 2.37
            conceded = stats.points_against ** 2.37
            expected = round(scored / (scored + conceded) * played, 2)
        win_points = round((win_pct * 100) / 2, 2)
        margin_points = math.copysign(math.log10(1 + abs(mov)) * margin_weight, mov)
        scaled_sos = 0.8 + (sos / cors_max) * (1.2 - 0.8)
        previous_term = previous_cors[team_id] / week
        cors = round(((win_points + margin_points) * scaled_sos + previous_term) / 2, 2)
        rows.append(RankingRow(
            rank=0, team_id=team_id, school=team.school, conference=team.conference, cors=cors,
            wins=stats.wins, losses=played - stats.wins, win_pct=win_pct,
            mov=round(mov, 2), sos=sos, expected_wins=expected,
            wins_vs_expected=round(stats.wins - expected, 2),
        ))
    return _rank(rows)


def _preseason(
    season: Season, prior_final: tuple[RankingRow, ...], carryover_strength: float,
) -> tuple[RankingRow, ...]:
    returning = {row["team_id"]: row for row in prior_final}
    rows: list[RankingRow] = []
    for team_id, team in season.teams.items():
        prior = returning.get(team_id)
        cors = 0.0
        if prior is not None:
            adjusted = prior["cors"] - prior["wins_vs_expected"] * _REGRESSION_FACTOR
            cors = round(adjusted * carryover_strength, 2)
        rows.append(RankingRow(
            rank=0, team_id=team_id, school=team.school, conference=team.conference, cors=cors,
            wins=0, losses=0, win_pct=0.0, mov=0.0, sos=0.0,
            expected_wins=0.0, wins_vs_expected=0.0,
        ))
    return _rank(rows)


def _forecast(
    season: Season, game: Game, cutoff: str, ratings: dict[int, float], home_advantage: float,
) -> ForecastRow:
    home = ratings[game.home_team_id]
    away = ratings[game.away_team_id]
    home_points = away_points = actual_margin = None
    if game.score is not None:
        home_points, away_points = game.score
        actual_margin = home_points - away_points
    return ForecastRow(
        season=season.year, game_id=game.id, start_utc=_iso_utc(game.start_utc), cutoff_utc=cutoff,
        home_team_id=game.home_team_id, away_team_id=game.away_team_id,
        home_team=season.teams[game.home_team_id].school,
        away_team=season.teams[game.away_team_id].school,
        neutral_site=game.neutral_site, home_cors=home, away_cors=away,
        home_margin=round(home - away + (0.0 if game.neutral_site else home_advantage), 2),
        status=game.status, home_points=home_points, away_points=away_points,
        actual_home_margin=actual_margin, ncaa_round=game.ncaa_round,
    )


def _replay(
    season: Season,
    prior_final: tuple[RankingRow, ...],
    home_advantage: float,
    carryover_strength: float,
    margin_weight: float,
) -> SeasonReplay:
    slates: dict[datetime, list[Game]] = defaultdict(list)
    for game in season.games:
        slates[_previous_monday(game.start_utc)].append(game)
    finals = [(game, game.score) for game in season.games if game.score is not None]
    last_result_start = finals[-1][0].start_utc
    final_cutoff = _previous_monday(last_result_start) + _WEEK
    if last_result_start + _RESULT_BUFFER >= final_cutoff:
        final_cutoff += _WEEK
    first_cutoff = min(slates)
    last_cutoff = max(max(slates), final_cutoff)

    results = {team_id: _TeamResults() for team_id in season.teams}
    rows = _preseason(season, prior_final, carryover_strength)
    ratings = {row["team_id"]: row["cors"] for row in rows}
    rankings: list[Checkpoint] = []
    forecasts: list[ForecastRow] = []
    final_rankings = rows
    result_index = 0
    cutoff = first_cutoff
    week = 0
    while cutoff <= last_cutoff:
        latest_start = cutoff.astimezone(timezone.utc) - _RESULT_BUFFER
        while result_index < len(finals) and finals[result_index][0].start_utc < latest_start:
            game, (home, away) = finals[result_index]
            results[game.home_team_id].add(game.away_team_id, home, away)
            results[game.away_team_id].add(game.home_team_id, away, home)
            result_index += 1
        if result_index:
            rows = _rankings(season, results, ratings, week, margin_weight)
            ratings = {row["team_id"]: row["cors"] for row in rows}
        cutoff_utc = _iso_utc(cutoff)
        rankings.append(Checkpoint(week=week, cutoff_utc=cutoff_utc, rows=rows))
        forecasts.extend(
            _forecast(season, game, cutoff_utc, ratings, home_advantage)
            for game in slates.get(cutoff, ())
        )
        if cutoff == final_cutoff:
            final_rankings = rows
        cutoff += _WEEK
        week += 1
    return SeasonReplay(
        season=season.year, rankings=tuple(rankings), final_cutoff_utc=_iso_utc(final_cutoff),
        final_rankings=final_rankings, forecasts=tuple(forecasts),
    )


def replay_seasons(
    db_path: str | Path,
    through_season: int = 2024,
    *,
    home_advantage: float = HOME_ADVANTAGE,
    carryover_strength: float = CARRYOVER_STRENGTH,
    margin_weight: float = MARGIN_WEIGHT,
) -> tuple[SeasonReplay, ...]:
    """Replay consecutive seasons by ending year, starting with 2019–20.

    Rankings are chronological from preseason (week 0). FINAL is the Monday
    checkpoint that includes the last completed result, independent of any
    later unscored fixtures. Forecasts are sorted by UTC tipoff and game ID.
    Selected scheduled games have null scores. All timestamps are UTC ISO 8601.
    Positive home_margin and actual_home_margin favor the designated home team.

    Home advantage adds points to non-neutral projections. Carryover strength
    scales the prior FINAL rating after the frozen wins-vs-expected adjustment.
    Margin weight sets the signed logarithmic MOV coefficient and the
    corresponding CORS maximum used to normalize strength of schedule.
    """
    if type(through_season) is not int or through_season < GENESIS_SEASON:
        raise ValueError(f"through_season must be an ending year at least {GENESIS_SEASON}")
    for name, value in (
        ("home_advantage", home_advantage),
        ("carryover_strength", carryover_strength),
        ("margin_weight", margin_weight),
    ):
        if isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if carryover_strength < 0 or margin_weight < 0:
        raise ValueError("carryover_strength and margin_weight must be non-negative")

    replays: list[SeasonReplay] = []
    prior_final: tuple[RankingRow, ...] = ()
    for season in read_history(db_path, GENESIS_SEASON, through_season):
        replay = _replay(season, prior_final, home_advantage, carryover_strength, margin_weight)
        replays.append(replay)
        prior_final = replay["final_rankings"]
    return tuple(replays)
