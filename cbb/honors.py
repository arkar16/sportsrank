"""NCAA tournament outcomes and the published final CORS standings."""
import csv
from html import escape
from pathlib import Path

from cfb.reference_archive import season_team_routes
from cfb.reference_markup import school_label
from cfb.season_pages import _document
from .record_book import _season_link, _table, season_name

NCAA = "https://www.ncaa.com/history/basketball-men/d1"
PROGRAM_NAMES = {
    "Connecticut": "UConn", "LaSalle": "La Salle", "Loyola (Ill.)": "Loyola Chicago",
    "Memphis State": "Memphis", "North Carolina State": "NC State", "Oklahoma A&M": "Oklahoma State",
    "Seattle": "Seattle U",
}


def write_honors(root, archives, routes, presentation, as_of):
    finals = {s.year: s for s in archives if s.rankings.get("FINAL")}
    team_routes = {year: season_team_routes(s, routes) for year, s in finals.items()}
    tournament = []
    with Path(__file__).with_name("ncaa_finals.csv").open(encoding="utf-8") as source:
        for game in csv.DictReader(source):
            year = int(game["year"])
            # The source lists tournament years, without tipoff dates. Publish past
            # tournaments once their season ends, including when rebuilding an older edition.
            if year >= as_of.year + (as_of.month >= 7):
                continue
            season = _season_link(year) if year in finals else season_name(year)
            labels = {name: school_label(PROGRAM_NAMES.get(name, name), team_routes.get(year, routes))
                      for name in (game["winner"], game["runner_up"]) if name}
            if game["status"] == "canceled":
                tournament.append((season, "Tournament canceled", "—", "—", "—"))
                continue
            winner = labels[game["winner"]]
            if game["status"] == "vacated":
                winner = f"Title vacated ({winner})"
            tournament.append((season, winner, escape(game["record"]), labels[game["runner_up"]], escape(game["score"])))

    nav = '<nav class="archive-nav"><a href="/cbb/index.html">Basketball record book</a></nav>'
    for last in (False, True):
        title = "Last-place CORS finishers" if last else "National champions"
        body = nav
        if not last:
            body += ('<h2>NCAA tournament</h2>' + _table(("Season", "Champion", "Record", "Runner-up", "Final score"), tournament)
                     + f'<p><a href="{NCAA}">NCAA championship history</a></p><h2>Final CORS leaders</h2>')
        rows = []
        for year, s in sorted(finals.items(), reverse=True):
            standings = s.rankings["FINAL"]
            entry = max(standings, key=lambda r: r.rank) if last else min(standings, key=lambda r: r.rank)
            rows.append((_season_link(year), school_label(entry.school, team_routes[year]), str(entry.rank),
                         escape(entry.record), escape(entry.conference), f"{entry.rating:.2f}"))
        body += ('<p>Based on the published final Division I ranking for each completed CORS season.</p>'
                 + _table(("Season", "Team", "Final rank", "Record", "Conference", "CORS rating"), rows))
        name = "last-place" if last else "champions"
        path = root / "history" / f"{name}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(presentation.render(_document(title, title, body).encode(), archive=False))
