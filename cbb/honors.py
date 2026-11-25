"""NCAA tournament outcomes and the published final CORS standings."""
import csv
from html import escape
from pathlib import Path

from cfb.conference_pages import CONFERENCE_HONORS, conference_honors_route, conference_route
from cfb.reference_archive import season_team_routes
from cfb.reference_markup import school_label
from cfb.season_pages import _document
from .record_book import _season_link, season_name

NCAA = "https://www.ncaa.com/history/basketball-men/d1"
PROGRAM_NAMES = {
    "Connecticut": "UConn", "LaSalle": "La Salle", "Loyola (Ill.)": "Loyola Chicago",
    "Memphis State": "Memphis", "North Carolina State": "NC State", "Oklahoma A&M": "Oklahoma State",
    "Seattle": "Seattle U",
}


def _honors_table(headers, rows, numeric_cols=()):
    head = "".join(
        f'<th scope="col"{" class=\"num\"" if i in numeric_cols else ""}>{header}</th>'
        for i, header in enumerate(headers)
    )
    body = "".join(
        "<tr>" + "".join(
            f'<td{" class=\"num\"" if i in numeric_cols else ""}>{cell}</td>'
            for i, cell in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    return f'<div class="table-scroll" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _win_rate(record):
    parts = record.replace("-", "–").split("–")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return "—"
    wins, losses = int(parts[0]), int(parts[1])
    total = wins + losses
    return f"{wins / total:.2f}" if total else "—"


def write_honors(root, archives, routes, presentation, as_of, *, earlier_years=()):
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
            season = _season_link(year) if year in finals or year in earlier_years else season_name(year)
            labels = {name: school_label(PROGRAM_NAMES.get(name, name), team_routes.get(year, routes))
                      for name in (game["winner"], game["runner_up"]) if name}
            if game["status"] == "canceled":
                tournament.append((season, "Tournament canceled", "—", "—", "—"))
                continue
            winner = labels[game["winner"]]
            if game["status"] == "vacated":
                winner = f"Title vacated ({winner})"
            tournament.append((season, winner, escape(game["record"]), labels[game["runner_up"]], escape(game["score"])))

    def publish(name, title, nav_links, body):
        nav = '<nav class="archive-nav" aria-label="Archive">' + " · ".join(nav_links) + "</nav>"
        path = root / "history" / f"{name}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(presentation.render(_document(title, title, nav + body).encode(), archive=False, sortable=True))

    publish(
        "ncaa-champions",
        "NCAA tournament champions",
        (
            '<a href="/cbb/index.html">Basketball record book</a>',
            '<a href="/cbb/history/champions.html">CORS national champions</a>',
            '<a href="/cbb/history/last-place.html">Last-place finishers</a>',
        ),
        _honors_table(("Season", "Tournament champion", "Record", "Runner-up", "Final score"), tournament, numeric_cols=(2, 4))
        + f'<p><a href="{NCAA}">NCAA championship history</a></p>',
    )
    for last in (False, True):
        title = "Last-place CORS finishers" if last else "CORS national champions"
        body = "<p>Each season’s champion is the team ranked No. 1 in the final CORS standings.</p>" if not last else ""
        rows = []
        for year, s in sorted(finals.items(), reverse=True):
            standings = s.rankings["FINAL"]
            entry = max(standings, key=lambda r: r.rank) if last else min(standings, key=lambda r: r.rank)
            conf = escape(entry.conference or "—")
            if entry.conference and entry.conference != "—":
                href = conference_route(entry.conference, year, sport="basketball")
                conf = f'<a href="{escape(href)}">{escape(entry.conference)}</a>'
            rows.append((
                _season_link(year),
                school_label(entry.school, team_routes[year]),
                str(entry.rank),
                escape(entry.record),
                _win_rate(entry.record),
                conf,
                f"{entry.rating:.2f}",
            ))
        body += _honors_table(
            ("Season", "Team", "Final rank", "Record", "Win rate", "Conference", "CORS rating"),
            rows,
            numeric_cols=(2, 3, 4, 6),
        )
        if not last:
            body += '<p><a href="/cbb/history/ncaa-champions.html">NCAA tournament champions</a></p>'
            nav_links = (
                '<a href="/cbb/index.html">Basketball record book</a>',
                '<a href="/cbb/history/last-place.html">Last-place finishers</a>',
                '<a href="/cbb/history/ncaa-champions.html">NCAA tournament champions</a>',
            )
        else:
            nav_links = (
                '<a href="/cbb/index.html">Basketball record book</a>',
                '<a href="/cbb/history/champions.html">CORS national champions</a>',
                '<a href="/cbb/history/ncaa-champions.html">NCAA tournament champions</a>',
            )
        name = "last-place" if last else "champions"
        conference_link = f'<a href="{conference_honors_route(sport="basketball")}">{CONFERENCE_HONORS}</a>'
        nav_links += (conference_link,)
        publish(name, title, nav_links, body)
