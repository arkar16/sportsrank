"""Share a single prediction from the printed forecast tables on the static site."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape
from pathlib import Path
import re
import unicodedata

from bs4 import BeautifulSoup

from cfb.archive_markup import ui
from cfb.performance import _saved_margin
from cfb.reference_archive import _rows
from site_navigation import masthead


ORIGIN = "https://www.sportsrank.top"


@dataclass(frozen=True)
class Team:
    name: str
    mark: str
    rank: str
    record: str

    @classmethod
    def from_cell(cls, cell, standings):
        image = cell.find("img", class_="school-mark")
        name = cell.get_text(strip=True)
        rank, record = standings.get(name, ("", "—"))
        return cls(name, image["src"] if image else "", rank, record)

    @property
    def summary(self):
        return " ".join(filter(None, (self.rank, self.name, self.record)))


def _rank_records(path: Path, name: str, rank: str, record: str):
    if not path.is_file():
        return {}
    return {row[name]: (f"#{row[rank]}", row[record].replace("-", "–"))
            for row in _rows(path)}


@dataclass(frozen=True)
class Spread:
    sport: str
    edition: str
    date_label: str
    source: str
    home: Team
    away: Team
    neutral: bool
    home_margin: Decimal

    @property
    def sport_label(self):
        return "College football" if self.sport == "cfb" else "Men’s college basketball"

    @property
    def matchup(self):
        at = "vs." if self.neutral else "at"
        return f"{self.away.name} {at} {self.home.name}"

    @property
    def home_spread(self):
        handicap = f"{-self.home_margin:+f}".replace("-", "−") if self.home_margin else "Pick’em"
        return f"{self.home.name} {handicap}"

    @property
    def path(self):
        name = unicodedata.normalize("NFKD", self.matchup).encode("ascii", "ignore").decode()
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return f"/{self.sport}/picks/{self.edition}/{slug}.html"


def _document(game: Spread) -> str:
    title = escape(game.matchup)
    line = escape(game.home_spread)
    context = escape(f"{game.sport_label} · {game.date_label}" + (" · Neutral site" if game.neutral else ""))
    description = escape(f"{game.away.summary} vs. {game.home.summary}") + f" · {context}"
    section = "Football" if game.sport == "cfb" else "Basketball"
    canonical = ORIGIN + game.path
    preview = ""
    if game.home.mark:
        mark = ORIGIN + escape(game.home.mark)
        alt = f"{escape(game.home.name)} school mark"
        preview = (f'<meta property="og:image" content="{mark}"><meta property="og:image:alt" content="{alt}">\n'
                   f'<meta name="twitter:image" content="{mark}"><meta name="twitter:image:alt" content="{alt}">')
    source = escape(game.source)
    back = source if game.sport == "cfb" else "/cbb/current.html#forecasts"
    edition_link = f' · <a href="{source}">Dated edition</a>' if game.sport == "cbb" else ""
    teams = []
    for team in (game.away, game.home):
        logo = f'<img src="{escape(team.mark)}" width="60" height="60" alt="">' if team.mark else ""
        teams.append(f'<tr><th scope="row"><span class="team-line">{logo}<span class="rank">{escape(team.rank)}</span>'
                     f'<span class="school">{escape(team.name)}</span></span></th><td>{escape(team.record)}</td></tr>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {line} | SportsRank</title>
<link rel="canonical" href="{canonical}">
<meta name="description" content="{description} · Home spread {line}">
<meta property="og:type" content="website"><meta property="og:site_name" content="SportsRank">
<meta property="og:url" content="{canonical}"><meta property="og:title" content="{title} · {line}">
<meta property="og:description" content="{description}">
<meta name="twitter:card" content="summary">{preview}
<link rel="stylesheet" href="/cfb/assets/record-book.css"><link rel="stylesheet" href="/cfb/assets/spread-card.css?v=2">
<script src="/cfb/assets/spread-card.js?v=2" defer></script></head><body>{masthead()}
<main class="spread-page"><nav aria-label="Forecasts"><a class="back" href="{back}">{section} forecasts</a>{edition_link}</nav>
<h1>{title}</h1><p class="edition">{context}</p>
<table class="game-line"><thead><tr><th scope="col">CORS rank · Team</th><th scope="col">Record</th></tr></thead>
<tbody>{''.join(teams)}</tbody></table>
<p class="pick">Home spread <strong>{line}</strong></p>
<div class="share-actions"><a id="game-link" href="{canonical}">Game link</a>
<button id="save-card" type="button" hidden>Save image</button><span id="share-status" role="status"></span></div>
</main></body></html>
"""


_TABLE = re.compile(r"<table\b[^>]*>.*?</table>", re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.I | re.S)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.I | re.S)
_SHARE = re.compile(r'<!--sr-ui--><a class="share-spread" [^>]*><!--/sr-ui-->(.*?)<!--sr-ui--></a><!--/sr-ui-->', re.S)
_HINT = ui('<p class="spread-hint">Select a spread to share that game.</p>')


def _link_table(markup: str, directory: Path, pick_field: str, read_game) -> str:
    """Insert only presentation markup so archived football source bytes remain recoverable."""
    markup = _SHARE.sub(r"\1", markup).replace(_HINT, "")
    table = _TABLE.search(markup)
    if not table:
        return markup
    rows = _ROW.finditer(table[0])
    heading = next(rows, None)
    if heading is None:
        return markup
    fields = [th.get_text(strip=True) for th in BeautifulSoup(heading[0], "html.parser").find_all("th")]
    index = fields.index(pick_field)
    edits = []
    games = {}
    for row in rows:
        cells = BeautifulSoup(row[0], "html.parser").find_all("td")
        if not cells:
            continue
        game = read_game(dict(zip(fields, cells, strict=True)))
        if game.path in games:
            raise ValueError(f"Two games have the same share URL: {game.path}")
        games[game.path] = game
        cell = list(_CELL.finditer(row[0]))[index]
        start, end = cell.span(1)
        opening = ui(f'<a class="share-spread" href="{game.path}" aria-label="Share {escape(game.matchup)}: {escape(game.home_spread)}">')
        edits.append((row.start() + start, row.start() + end, opening + cell[1] + ui("</a>")))
    if not games:
        return markup
    # Write every destination before exposing its link in the slate.
    for game in games.values():
        target = directory / game.path.removeprefix(f"/{game.sport}/")
        content = _document(game).encode()
        if not target.exists() or target.read_bytes() != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    parts = []
    pos = 0
    for start, end, link in edits:
        parts.extend((table[0][pos:start], link))
        pos = end
    parts.append(table[0][pos:])
    markup = markup[:table.start()] + "".join(parts) + markup[table.end():]
    # Outside the archive's presentation markers, so the hint also strips cleanly.
    return markup.replace("</h1>", "</h1>" + _HINT, 1)


def football_links(raw: bytes, path: Path, directory: Path) -> bytes:
    year, week = path.stem.split("_")[:2]
    source = f"/cfb/years/{year}/spread/{path.name}"
    number = int(week[1:])
    checkpoint = f"W{number - 1}" if number else "PRESEASON"
    ranking = path.parent.parent / "rankings" / f"{year}_{checkpoint}_FBS_cors.html"
    standings = _rank_records(ranking, "school", "rank", "record")

    def read_game(cells):
        home = Team.from_cell(cells["home_team"], standings)
        away = Team.from_cell(cells["away_team"], standings)
        values = {field: cell.get_text(strip=True) for field, cell in cells.items()}
        margin, _ = _saved_margin(values, home.name)
        return Spread("cfb", f"{year}/week-{week[1:]}", f"{year} · Week {week[1:]}", source,
                      home, away, values["neutral_site"].lower() == "true", margin)

    try:
        markup = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        markup = raw.decode("latin-1")
        encoding = "latin-1"
    return _link_table(markup, directory, "spread", read_game).encode(encoding, errors="xmlcharrefreplace")


def basketball_links(directory: Path) -> None:
    path = directory / "current.html"
    markup = path.read_text(encoding="utf-8")
    source = re.search(r'href="(editions/[\dTZ-]+\.html)"', markup)
    if source is None:
        raise ValueError("The basketball forecast has no dated edition")
    standings = _rank_records(directory / source[1], "Team", "Rank", "Record")

    def read_game(cells):
        home = Team.from_cell(cells["Home"], standings)
        away = Team.from_cell(cells["Away"], standings)
        tipoff = cells["Tipoff (Eastern)"].get_text(strip=True)
        date, sep, _ = tipoff.partition(" · ")
        day = datetime.strptime(date, "%b %d, %Y").date()
        label = tipoff + (" ET" if sep else "")
        pick = cells["Reconstructed pick"].get_text(strip=True)
        margin = Decimal(0)
        if pick != "Pick’em":
            winner, points = pick.rsplit(" by ", 1)
            points = Decimal(points)
            if winner not in (away.name, home.name) or not points.is_finite() or points <= 0:
                raise ValueError(f"Invalid basketball pick: {pick}")
            margin = points if winner == home.name else -points
        return Spread("cbb", day.isoformat(), label, "/cbb/" + source[1],
                      home, away, cells["Court"].get_text(strip=True) == "Neutral", margin)

    updated = _link_table(markup, directory, "Reconstructed pick", read_game)
    if updated != markup:
        path.write_text(updated, encoding="utf-8")
