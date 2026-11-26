"""Publish historical tournament matchups with retained research forecasts."""
import csv
from collections import defaultdict
from datetime import datetime, timedelta
from html import escape
from math import isfinite
from pathlib import Path

from .api_history import national_final
from .record_book import EASTERN, _grade_row, _season_link, _table, _write, team_label

MODELS = (
    ("home_margin", "CORS v0.4.0 · Monday"),
    ("weekly_margin", "Margin model · Monday"),
    ("tipoff_margin", "Margin model · before tipoff"),
)
METHOD = (
    "<p>Each forecast reconstructs a matchup that was actually played. Weekly CORS and the "
    "weekly research margin model use Monday’s ratings. The research model rates teams by scoring margin, adjusting for opponent strength. The pre-tipoff replay uses the same "
    "research model and adds results from games that started more than six hours earlier. "
    "These historical picks were reconstructed after the tournament.</p>"
)


def _load_margins():
    seasons = defaultdict(dict)
    with Path(__file__).with_name("tournament_margins.csv").open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            year, game_id = int(row["season"]), int(row["game_id"])
            if game_id in seasons[year]:
                raise ValueError(f"Duplicate tournament forecast for {year}, game {game_id}")
            seasons[year][game_id] = row
    return seasons


def _completed_games(forecasts, margins, as_of):
    last_tip = max(datetime.fromisoformat(row["tipoff"]) for row in margins.values())
    if last_tip + timedelta(hours=6) >= as_of:
        return []
    tournament = [g for g in forecasts if g["ncaa_round"] and g["status"] == "final"]
    if len(tournament) != len(margins) or {g["game_id"] for g in tournament} != margins.keys():
        raise ValueError("Historical tournament finals differ from retained research coverage")

    games = []
    for game in tournament:
        row = margins[game["game_id"]]
        cutoff = datetime.fromisoformat(row["weekly_cutoff"])
        tip = datetime.fromisoformat(row["tipoff"])
        if (cutoff >= tip or tip != datetime.fromisoformat(game["start_utc"])
                or cutoff != datetime.fromisoformat(game["cutoff_utc"])
                or int(row["home_id"]) != game["home_team_id"]
                or int(row["away_id"]) != game["away_team_id"]):
            raise ValueError(f'Tournament matchup or forecast time changed for game {game["game_id"]}')
        predictions = {key: float(row[key]) for key in ("weekly_margin", "tipoff_margin")}
        if not all(isfinite(v) for v in predictions.values()):
            raise ValueError(f'Invalid research margin for game {game["game_id"]}')
        games.append({**game, **predictions})
    return sorted(games, key=lambda g: (g["start_utc"], g["game_id"]))


def _grades(games, models=MODELS):
    rows = [_grade_row(label, [(g[key], g["actual_home_margin"]) for g in games])
            for key, label in models]
    return _table(("Model and cutoff", "Games", "RMSE (points)", "Correct / picks", "Winner accuracy", "Pick’ems"), rows)


def _pick(game, key):
    margin = game[key]
    if margin == 0:
        return "Pick’em"
    school = game["home_team"] if margin > 0 else game["away_team"]
    return f"{escape(school)} by {abs(margin):.2f}"


def _year_body(year, games, logos, models, method=METHOD):
    rounds = defaultdict(list)
    for g in games:
        sides = []
        for side in ("away", "home"):
            school = team_label(g[f"{side}_team"], g[f"{side}_team_id"], logos, year=year)
            sides.append(f'{school} {g[f"{side}_points"]}')
        tip = datetime.fromisoformat(g["start_utc"]).astimezone(EASTERN)
        rounds[g["ncaa_round"]].append((f"{tip:%b %d · %I:%M %p}", "<br>".join(sides),
                                         *(_pick(g, key) for key, _ in models)))
    body = (f'<p><a href="/cbb/tournaments/index.html">Tournament archive</a> · {_season_link(year)} season · '
            f'<a href="/cbb/performance/{year}/index.html">Model performance</a></p>'
            + method + _grades(games, models))
    links = [f'<a href="#round-{i}">{escape(name)}</a>' for i, name in enumerate(rounds, 1)]
    body += '<nav aria-label="Tournament rounds"><p>' + ' · '.join(links) + '</p></nav>'
    for i, (name, rows) in enumerate(rounds.items(), 1):
        body += f'<h2 id="round-{i}">{escape(name)}</h2>' + _table(
            ("Tipoff (Eastern)", "Final score", *(label for _, label in models)), rows)
    return body


def build_tournaments(root, seasons, as_of, logos, *, blends=(), model="CORS v0.6.0", api_only=False):
    by_year = {s["season"]: s for s in blends}
    models = MODELS[:1] if api_only else MODELS
    if blends:
        models += (("blend", f"{escape(model)} · Monday"),)
    method = ("<p>These CORS picks reconstruct the matchups from the pregame Monday checkpoint. "
              "Final scores and tournament rounds come from CollegeBasketballData.</p>" if api_only else METHOD)
    margins = {} if api_only else _load_margins()
    archive = {}
    for season in seasons:
        year = season["season"]
        if api_only:
            tournament = [g for g in season["forecasts"] if g["ncaa_round"]]
            if not tournament or not any(national_final(g["ncaa_round"])
                                         for g in tournament):
                continue
            if any(g["status"] != "final" or datetime.fromisoformat(g["start_utc"]) + timedelta(hours=6) >= as_of
                   for g in tournament):
                continue
            games = [dict(g) for g in sorted(tournament, key=lambda g: (g["start_utc"], g["game_id"]))]
        elif year in margins:
            games = _completed_games(season["forecasts"], margins[year], as_of)
        else:
            continue
        if games:
            if blends:
                predictions = {g["game_id"]: g["home_margin"] for g in by_year[year]["forecasts"]}
                for g in games:
                    g["blend"] = predictions[g["game_id"]]
            archive[year] = games
    if archive:
        rows = []
        for year, games in sorted(archive.items(), reverse=True):
            _write(root, f"tournaments/{year}.html", f"{year} March Madness picks and results",
                   _year_body(year, games, logos, models, method))
            rows.append((f'<a href="/cbb/tournaments/{year}.html">{year} tournament</a>', len(games)))
        all_games = [g for games in archive.values() for g in games]
        _write(root, "tournaments/index.html", "March Madness picks and results",
               _table(("Men’s NCAA tournament", "Games"), rows) + "<h2>Across tournaments</h2>"
               + method + "<p>RMSE measures margin error in points. Lower is better. Winner accuracy "
               "counts games with a predicted winner.</p>" + _grades(all_games, models))
    return set(archive)
