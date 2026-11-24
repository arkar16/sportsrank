"""Remaining team-season games from a dated public schedule and one CORS checkpoint."""
from collections import defaultdict
from contextlib import closing
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from .cors_v1 import rounded
from cfb.reference_archive import Game
from cfb.reference_markup import school_label

EASTERN = ZoneInfo("America/New_York")


def remaining_schedules(database, season, checkpoint, as_of, **kwargs):
    sections, moved, _ = schedule_views(database, season, checkpoint, as_of, **kwargs)
    return sections, moved


def schedule_views(database, season, checkpoint, as_of, *, snapshots=Path(__file__).with_name("schedules")):
    """Build the team schedule and projections anchored to the ranking cutoff."""
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

    routes = {r["school"]: f'/cbb/teams/{r["team_id"]}/{year}.html' for r in teams.values()}
    fixtures = {g["game_id"]: g for g in season["forecasts"]}
    finished = {g["game_id"] for g in season["forecasts"] if g["status"] == "final"
                and datetime.fromisoformat(g["start_utc"]) + timedelta(hours=6) < as_of}
    rows = defaultdict(list)
    moved = set()
    projections = []
    cutoff = datetime.fromisoformat(checkpoint["cutoff_utc"]).astimezone(EASTERN)
    with snapshot.open(encoding="utf-8", newline="") as stream:
        for fixture in csv.DictReader(stream):
            tip = datetime.fromisoformat(fixture["tipoff"]).astimezone(EASTERN)
            known = fixture["time_known"] == "1"
            on_team_schedule = tip.date() >= local_day and (not known or tip > as_of)
            after_checkpoint = tip > cutoff if known else tip.date() >= cutoff.date()
            if not (on_team_schedule or after_checkpoint):
                continue
            home, away = (teams.get(fixture[key]) for key in ("home_id", "away_id"))
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
            if after_checkpoint and margin is not None:
                projections.append(Game(
                    checkpoint.get("week", 0), home["school"], away["school"], None, None,
                    -Decimal(str(margin)), neutral,
                    conference_game=game.get("conference_game") if game else None,
                    postseason=bool(game and (
                        game.get("season_type") == "postseason" or game.get("tournament")
                        or game.get("conference_tournament") or game.get("ncaa_round")
                    )),
                ))
            if not on_team_schedule or game_id in finished:
                continue
            if game_id is not None:
                moved.add(game_id)
            clock = tip.strftime("%I:%M %p").lstrip("0") + " ET" if known else "Time TBD"
            for team, opponent, side, opponent_name, spread in (
                (home, away, "Home", fixture["away_name"], -margin if margin is not None else None),
                (away, home, "Away", fixture["home_name"], margin),
            ):
                if team is None:
                    continue
                label = escape(opponent_name)
                if opponent:
                    label = f'#{opponent["rank"]} {school_label(opponent["school"], routes)}'
                line = "—" if spread is None else f"{spread:+.2f}" if spread else "Pick’em"
                cells = (f'<th scope="row">{tip:%a, %b %d}</th><td>{clock}</td><td>{label}</td>'
                         f'<td>{"Neutral" if neutral else side}</td><td class="num">{line}</td>')
                rows[year, team["school"]].append((tip, fixture["event_id"], f"<tr>{cells}</tr>"))
    schedule_date = date.fromisoformat(snapshot.stem)
    heading = (f'<section id="remaining-schedule"><h2>Remaining schedule</h2>'
               f'<p>Opponent ranks and projected spreads use the {cutoff:%B %d} CORS ratings. '
               f'Schedule as of {schedule_date:%B %d, %Y}.</p>'
               '<div class="table-scroll" role="region" aria-label="Remaining schedule" tabindex="0"><table>'
               '<thead><tr><th scope="col">Date</th><th scope="col">Tipoff</th><th scope="col">Opponent</th>'
               '<th scope="col">Site</th><th scope="col" class="num">CORS spread (− if favored)</th>'
               '</tr></thead><tbody>')
    sections = {key: heading + "".join(row for _, _, row in sorted(games)) + "</tbody></table></div></section>"
                for key, games in rows.items()}
    return sections, moved, projections
