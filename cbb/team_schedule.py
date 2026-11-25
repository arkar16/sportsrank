"""Remaining team-season games from a dated public schedule and one CORS checkpoint."""
from collections import defaultdict
from contextlib import closing
import csv
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from .cors_v1 import rounded
from cfb.program_pages import ScheduledGame, TeamSchedule
from cfb.reference_archive import Game

EASTERN = ZoneInfo("America/New_York")


def tipoff_clock(tip):
    return tip.strftime("%I:%M %p").lstrip("0") + " ET"


def conference_tipoff(tip, *, time_known=True):
    """Label an Eastern date and use local midnight for date-only sort keys."""
    tip = tip.astimezone(EASTERN)
    label = tip.strftime("%b %d, %Y")
    if time_known:
        label += f" · {tipoff_clock(tip)}"
    else:
        tip = datetime.combine(tip.date(), time.min, EASTERN)
    return {"tipoff_label": label, "start_utc": tip.astimezone(UTC).isoformat()}


def projection_key(day, home, away, neutral):
    if neutral and home > away:
        home, away = away, home
    return day, home, away, neutral


def _matchup(week, home, away, margin, neutral, tipoff, game):
    return Game(
        week, home, away, None, None, Decimal(str(margin)).copy_negate() if margin is not None else None,
        neutral, tipoff,
        conference_game=game.get("conference_game") if game else None,
        postseason=bool(game and (
            game.get("season_type") == "postseason" or game.get("tournament")
            or game.get("conference_tournament") or game.get("ncaa_round")
        )),
    )


def schedule_views(database, season, checkpoint, as_of, *, snapshots=Path(__file__).with_name("schedules")):
    """Build the team schedule and known conference fixtures at the ranking cutoff."""
    year = season["season"]
    local_day = as_of.astimezone(EASTERN).date()
    available = [p for p in (snapshots / str(year)).glob("*.csv") if date.fromisoformat(p.stem) <= local_day]
    if not available:
        return {}, set(), []
    snapshot = max(available)
    ratings = {r["team_id"]: r for r in checkpoint["rows"]}
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        teams = {str(source): ratings[team] for team, source in db.execute(
            "SELECT id, json_extract(raw_json,'$.sourceId') FROM teams WHERE season = ?", (year,)
        ) if team in ratings}
        ids = {str(source): game for game, source in db.execute(
            "SELECT id, json_extract(raw_json,'$.sourceId') FROM games WHERE season = ?", (year,)
        ) if source is not None}

    fixtures = {g["game_id"]: g for g in season["forecasts"]}
    finished = {g["game_id"] for g in season["forecasts"] if g["status"] == "final"
                and datetime.fromisoformat(g["start_utc"]) + timedelta(hours=6) < as_of}
    rows = defaultdict(list)
    moved = set()
    projections = []
    projected_keys = set()
    projected_ids = set()
    cutoff = datetime.fromisoformat(checkpoint["cutoff_utc"]).astimezone(EASTERN)
    week = checkpoint.get("week", 0)

    def add_fixture(matchup, tip, fixture_id, home, away):
        for team, opponent in ((home, away), (away, home)):
            if team is not None:
                row = ScheduledGame(matchup, f"{tip:%a, %b %d}", opponent["rank"] if opponent else None)
                rows[year, team["school"]].append((tip, str(fixture_id), row))

    with snapshot.open(encoding="utf-8", newline="") as stream:
        for fixture in csv.DictReader(stream):
            tip = datetime.fromisoformat(fixture["tipoff"]).astimezone(EASTERN)
            known = fixture["time_known"] == "1"
            on_team_schedule = tip.date() >= local_day and (not known or tip > as_of)
            after_checkpoint = tip > cutoff if known else tip.date() >= cutoff.date()
            if not (on_team_schedule or after_checkpoint):
                continue
            home, away = (teams.get(fixture[key]) for key in ("home_id", "away_id"))
            if home is None and away is None:
                continue
            neutral = fixture["neutral"] == "1"
            game_id = ids.get(fixture["event_id"])
            game = fixtures.get(game_id)
            if game_id in finished:
                after_checkpoint = after_checkpoint and datetime.fromisoformat(game["start_utc"]) >= cutoff - timedelta(hours=6)
                if not after_checkpoint:
                    continue
            margin = None
            if home and away:
                h, a = home["team_id"], away["team_id"]
                if (game and game["cutoff_utc"] == checkpoint["cutoff_utc"]
                        and (game["home_team_id"], game["away_team_id"], game["neutral_site"]) == (h, a, neutral)):
                    margin = game["home_margin"]
                else:
                    if "home_advantages" in checkpoint:
                        venue = 0 if neutral else checkpoint["home_advantages"][h]
                        margin = rounded(home["cors"] - away["cors"] + venue)
                    else:
                        venue = 0 if neutral else season["home_advantages"][h]
                        margin = round(home["cors"] - away["cors"] + venue, 2)
            clock = tipoff_clock(tip) if known else "Time TBD"
            matchup = _matchup(
                week,
                home["school"] if home else fixture["home_name"],
                away["school"] if away else fixture["away_name"],
                margin, neutral, clock, game,
            )
            if after_checkpoint:
                key = projection_key(tip.date(), matchup.home, matchup.away, neutral)
                if key not in projected_keys:
                    projections.append(replace(matchup, **conference_tipoff(tip, time_known=known)))
                    projected_keys.add(key)
                if game_id is not None:
                    projected_ids.add(game_id)
            if not on_team_schedule or game_id in finished:
                continue
            if game_id is not None:
                moved.add(game_id)
            add_fixture(matchup, tip, fixture["event_id"], home, away)

    # The retained conference rebuild also uses the issued checkpoint's weekly
    # slate. A fixture can be absent from the dated public schedule, including
    # games that tipped between the ranking cutoff and publication.
    for game_id, game in fixtures.items():
        if game_id in projected_ids or game["cutoff_utc"] != checkpoint["cutoff_utc"]:
            continue
        home, away = (ratings.get(game[f"{side}_team_id"]) for side in ("home", "away"))
        if home is None or away is None or game["home_margin"] is None:
            continue
        tip = datetime.fromisoformat(game["start_utc"]).astimezone(EASTERN)
        neutral = game["neutral_site"]
        key = projection_key(tip.date(), home["school"], away["school"], neutral)
        if tip <= cutoff or key in projected_keys:
            continue
        matchup = _matchup(week, home["school"], away["school"], game["home_margin"], neutral, tipoff_clock(tip), game)
        projections.append(replace(matchup, **conference_tipoff(tip)))
        projected_keys.add(key)
        if tip > as_of:
            add_fixture(matchup, tip, game_id, home, away)
            moved.add(game_id)

    schedule_date = date.fromisoformat(snapshot.stem)
    schedules = {
        key: TeamSchedule(tuple(row for _, _, row in sorted(games, key=lambda item: item[:2])),
                          schedule_date, cutoff.date())
        for key, games in rows.items()
    }
    return schedules, moved, projections
