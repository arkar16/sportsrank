"""Static conference season records using membership as archived in each ranking."""
from collections import defaultdict
from html import escape
from functools import partial
import re

from .reference_archive import Season, program_season_route


_PREFIX = {"football": "cfb", "basketball": "cbb"}


def conference_route(name: str, year: int | None = None, *, sport="football") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"empty conference slug for {name!r}")
    prefix = _PREFIX[sport]
    return f"/{prefix}/conferences/{slug}/{year or 'index'}.html"


def _page(title: str, content: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{escape(title)} | SportsRank</title></head><body><main>'
            f'<h1>{escape(title)}</h1>{content}</main></body></html>')


def build_conference_pages(seasons: list[Season], routes: dict[str, str], *, sport="football", year_label=str,
                           season_route=lambda y: f"/cfb/years/{y}/{y}_CFB.html"):
    owners = {}
    histories = defaultdict(list)
    route = partial(conference_route, sport=sport)
    directory = f"/{_PREFIX[sport]}/conferences/index.html"
    for season in seasons:
        checkpoint = next(reversed(season.rankings))
        groups = defaultdict(list)
        for row in season.rankings[checkpoint]:
            if row.conference and row.conference != "—":
                groups[row.conference].append(row)
        year = season.year
        edition = f"Through Week {checkpoint[1:]}" if checkpoint.startswith("W") else checkpoint.title()
        for name, members in groups.items():
            if owners.setdefault(route(name), name) != name:
                raise ValueError(f"ambiguous conference route for {name!r}")
            histories[name].append(year)
            rows = []
            for member in sorted(members, key=lambda row: row.rank):
                team = program_season_route(routes[member.school], year)
                rows.append(f'<tr><td class="num">{member.rank}</td>'
                            f'<th scope="row"><a href="{team}">{escape(member.school)}</a></th>'
                            f'<td>{escape(member.record)}</td><td class="num">{member.rating:.2f}</td></tr>')
            nav = (f'<nav><a href="{route(name)}">{escape(name)} history</a> · '
                   f'<a href="{season_route(year)}">{year_label(year)} national rankings</a></nav>')
            table = (f'<p class="edition">{edition}</p><div class="table-scroll"><table>'
                     '<thead><tr><th>National rank</th><th>Team</th><th>Overall record</th>'
                     '<th>CORS rating</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
            yield route(name, year).lstrip("/"), _page(f"{year_label(year)} {name}", nav + table)

    items = []
    for name, years in sorted(histories.items()):
        history = route(name)
        decades = defaultdict(list)
        for year in reversed(years):
            decades[year // 10 * 10].append(f'<a href="{route(name, year)}">{year_label(year)}</a>')
        links = "".join(f'<div><h3>{decade}s</h3><p>{" ".join(entries)}</p></div>'
                        for decade, entries in decades.items())
        yield history.lstrip("/"), _page(name, f'<nav><a href="{directory}">Conferences</a></nav>'
                                                  f'<h2>Seasons</h2><div class="year-archive">{links}</div>')
        items.append(f'<li><a href="{history}">{escape(name)}</a></li>')
    yield directory.lstrip("/"), _page(f"College {sport} conferences", '<ul class="program-list">' + ''.join(items) + '</ul>')
