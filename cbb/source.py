"""Read normalized men's Division I schedules from the local history cache."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import re
import sqlite3


@dataclass(frozen=True)
class Team:
    id: int
    school: str
    conference: str = ""


@dataclass(frozen=True)
class Game:
    id: int
    start_utc: datetime
    home_team_id: int
    away_team_id: int
    neutral_site: bool
    status: str
    score: tuple[int, int] | None
    ncaa_round: str | None = None
    conference_game: bool | None = None
    season_type: str | None = None
    tournament: str | None = None
    conference_tournament: bool = False
    overtime: bool | None = None
    halftime: tuple[int, int] | None = None
    date_only: bool = False
    verified_at: str | None = None


@dataclass(frozen=True)
class Season:
    year: int
    teams: dict[int, Team]
    games: tuple[Game, ...]


EASTERN = ZoneInfo("America/New_York")


def fixture_start(stamp, date_only):
    instant = datetime.fromisoformat(stamp)
    if instant.utcoffset() is None:
        raise ValueError("Expected a timezone offset")
    if date_only:
        # CBBD encodes an unknown historical tipoff as midnight on the named date.
        day = instant.date() if instant.timetz().replace(tzinfo=None) == time.min else instant.astimezone(EASTERN).date()
        instant = datetime.combine(day, time.min, EASTERN)
    return instant.astimezone(timezone.utc)


def latest_start(start, date_only):
    if date_only:
        day = start.astimezone(EASTERN).date()
        return datetime.combine(day + timedelta(days=1), time.min, EASTERN).astimezone(timezone.utc)
    return start


def visible_final(fixture, as_of):
    if fixture["status"] != "final":
        return False
    start=datetime.fromisoformat(fixture["start_utc"])
    if start >= as_of:
        return False
    if fixture.get("verified_at"):
        stamp=datetime.fromisoformat(fixture["verified_at"])
        if stamp.tzinfo is None:
            stamp=stamp.replace(tzinfo=timezone.utc)
        if stamp<=as_of:
            return True
    return latest_start(start,fixture.get("date_only")) + timedelta(hours=6) < as_of


def _verification_column(db):
    return "verified_at" if any(r[1]=="verified_at" for r in db.execute("PRAGMA table_info(games)")) else "NULL"


def period_evidence(raw):
    """Qualify a retained two-half box against the final and each overtime boundary."""
    lines = raw.get("homePeriodPoints"), raw.get("awayPeriodPoints")
    if not all(isinstance(line, list) and len(line) >= 2 for line in lines):
        return None, None
    home, away = lines
    if (len(home) != len(away) or any(type(x) is not int or x < 0 for x in home + away)
            or (sum(home), sum(away)) != (raw.get("homePoints"), raw.get("awayPoints"))
            or any(sum(home[:n]) != sum(away[:n]) for n in range(2, len(home)))):
        return None, None
    return len(home) > 2, (home[0], away[0])


def supplemental_fixtures(database, years):
    """Cover every accepted provider fixture outside the Division I ranking replay."""
    result = {}
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for year in years:
            teams = {r[0] for r in db.execute("select id from teams where season=?", (year,))}
            games = []
            for gid, stamp, status, raw, verified in db.execute(
                    f"select id,start_utc,status,raw_json,{_verification_column(db)} from games where season=? "
                    "and status in ('scheduled','final','unverified','in_progress') order by start_utc,id", (year,)):
                r = json.loads(raw)
                home, away = r["homeTeamId"], r["awayTeamId"]
                if not stamp or (home in teams and away in teams and status not in ("unverified","in_progress")):
                    continue
                h, a = r.get("homePoints"), r.get("awayPoints")
                if status == "final" and (type(h) is not int or type(a) is not int or min(h, a) < 0 or h == a):
                    status, h, a = "unverified", None, None
                date_only = bool(r.get("startTimeTbd"))
                stamp = fixture_start(stamp, date_only).isoformat()
                ot, half = period_evidence(r)
                games.append(dict(game_id=gid, start_utc=stamp, status=status, home_team=r["homeTeam"],
                                  away_team=r["awayTeam"], home_team_id=home, away_team_id=away,
                                  neutral_site=r["neutralSite"], home_margin=None, home_points=h,
                                  away_points=a, overtime=ot, halftime=half, date_only=date_only, verified_at=verified))
            result[year] = games
    return result


def _read_season(db: sqlite3.Connection, year: int) -> Season:
    # The cache's season-specific /teams responses define D-I membership.
    # IDs are stable across seasons and independent of names and conferences.
    teams = {
        team_id: Team(team_id, data["school"], data.get("conference") or "—")
        for team_id, raw in db.execute(
            "SELECT id, raw_json FROM teams WHERE season = ? ORDER BY id", (year,)
        )
        for data in [json.loads(raw)]
    }
    if not teams:
        raise ValueError(f"Missing Division I roster for season {year}")

    games: list[Game] = []
    for game_id, start_utc, status, raw, verified in db.execute(
        f"""
        SELECT id, start_utc, status, raw_json, {_verification_column(db)} FROM games
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
        date_only = bool(data["startTimeTbd"])
        try:
            start = fixture_start(start_utc, date_only)
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
        ncaa_round = None
        if data.get("tournament") == "NCAA":
            ncaa_round = (data.get("gameNotes") or "").split(" - ")[-1] or "Tournament games"
        tournament = data.get("tournament")
        # The provider labels some conference tournaments "regular".
        notes = data.get("gameNotes") or ""
        conference_tournament = bool(data.get("conferenceGame") and re.search(
            r"(?:tournament|championship|playoffs).*? - .*?(?:round|final|play-in)",
            notes, re.IGNORECASE,
        ))
        games.append(Game(
            game_id, start.astimezone(timezone.utc), home, away, neutral_site, status, score, ncaa_round,
            data.get("conferenceGame"), data.get("seasonType"), tournament, conference_tournament,
            *period_evidence(data), date_only, verified,
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
