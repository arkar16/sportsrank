"""Read normalized men's Division I schedules from the local history cache."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class Team:
    id: int
    school: str


@dataclass(frozen=True)
class Game:
    id: int
    start_utc: datetime
    home_team_id: int
    away_team_id: int
    neutral_site: bool
    status: str
    score: tuple[int, int] | None


@dataclass(frozen=True)
class Season:
    year: int
    teams: dict[int, Team]
    games: tuple[Game, ...]


def _read_season(db: sqlite3.Connection, year: int) -> Season:
    # The cache's season-specific /teams responses define D-I membership.
    # IDs are stable across seasons and independent of names and conferences.
    teams = {
        team_id: Team(team_id, json.loads(raw)["school"])
        for team_id, raw in db.execute(
            "SELECT id, raw_json FROM teams WHERE season = ? ORDER BY id", (year,)
        )
    }
    if not teams:
        raise ValueError(f"Missing Division I roster for season {year}")

    games: list[Game] = []
    for game_id, start_utc, status, raw in db.execute(
        """
        SELECT id, start_utc, status, raw_json FROM games
        WHERE season = ? AND status IN ('final', 'scheduled')
        """,
        (year,),
    ):
        data = json.loads(raw)
        home = data["homeTeamId"]
        away = data["awayTeamId"]
        if home not in teams or away not in teams:
            continue
        if home == away:
            raise ValueError(f"Game {game_id} lists the same team twice")
        if data["startTimeTbd"]:
            if status == "final":
                raise ValueError(f"Final game {game_id} lacks a known tipoff time")
            continue
        try:
            start = datetime.fromisoformat(start_utc)
            if start.utcoffset() is None:
                raise ValueError("Expected a timezone offset")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Game {game_id} has an invalid start_utc") from exc
        neutral_site = data["neutralSite"]
        if not isinstance(neutral_site, bool):
            raise ValueError(f"Game {game_id} has an invalid neutralSite flag")
        score = None
        if status == "final":
            home_points, away_points = data["homePoints"], data["awayPoints"]
            if (
                type(home_points) is not int
                or type(away_points) is not int
                or min(home_points, away_points) < 0
                or home_points == away_points
            ):
                raise ValueError(f"Final basketball game {game_id} has an invalid score")
            score = (home_points, away_points)
        games.append(Game(
            game_id, start.astimezone(timezone.utc), home, away, neutral_site, status, score,
        ))

    games.sort(key=lambda game: (game.start_utc, game.id))
    if not any(game.score is not None for game in games):
        raise ValueError(f"No completed Division I games in season {year}")
    return Season(year, teams, tuple(games))


def read_history(db_path: str | Path, first_season: int, through_season: int) -> tuple[Season, ...]:
    """Read consecutive seasons from one SQLite snapshot without creating a cache."""
    uri = Path(db_path).resolve().as_uri()
    with closing(sqlite3.connect(f"{uri}?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        return tuple(_read_season(db, year) for year in range(first_season, through_season + 1))
