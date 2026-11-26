"""Share a single prediction from the printed forecast tables on the static site."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape, unescape
from pathlib import Path
import re
import unicodedata

from bs4 import BeautifulSoup

from cfb.archive_markup import ui
from cfb.conference_pages import Record, _is_conference_game, _UNAFFILIATED
from cfb.excitement_pages import ESTIMATE_NOTE, aev_label
from cfb.game_excitement import Matchup
from cfb.performance import _saved_margin
from cfb.reference_archive import Season, _rows
from site_navigation import masthead


ORIGIN = "https://www.sportsrank.top"


def _matchup_slug(matchup):
    name = unicodedata.normalize("NFKD", matchup).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@dataclass
class ConferenceSlate:
    records: dict[str, Record]
    play: dict[tuple[str, str, str], str]

    @classmethod
    def build(cls, conferences, completed, fixtures):
        """Use the same regular-season league-game policy as the conference standings."""
        records = {team: Record() for team, league in conferences.items() if league and league not in _UNAFFILIATED}

        def league_game(game):
            return _is_conference_game(game, conferences.get(game.home) or "", conferences.get(game.away) or "")

        for game in completed:
            if league_game(game):
                margin = game.home_score - game.away_score
                records[game.home].add_game(margin)
                records[game.away].add_game(-margin)
        play = {}
        for key, game in fixtures.items():
            if league_game(game):
                play[key] = f"Conference ({conferences[game.home]})"
            elif (game.conference_game is False or game.postseason
                  or all(conferences.get(team) not in (None, "", "—") for team in (game.home, game.away))):
                play[key] = "OOC"
        return cls(records, play)


@dataclass(frozen=True)
class Team:
    name: str
    mark: str
    rank: str
    record: str

    @classmethod
    def from_cell(cls, cell, standings, conference):
        image = cell.find("img", class_="school-mark")
        name = cell.get_text(strip=True)
        rank, record = standings.get(name, ("", "—"))
        if record != "—":
            league = conference.records.get(name, "—")
            record = f"{record} ({league})".replace("–", "-")
        return cls(name, image["src"] if image else "", rank, record)

    @property
    def summary(self):
        return " ".join(filter(None, (self.rank, self.name, self.record)))


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
    play: str
    bev: str | None = None
    aev: str | None = None

    @property
    def sport_label(self):
        return "CFB" if self.sport == "cfb" else "MBB"

    @property
    def matchup(self):
        at = "vs." if self.neutral else "at"
        return f"{self.away.name} {at} {self.home.name}"

    @property
    def prediction(self):
        if not self.home_margin:
            return "Pick’em"
        verb = "by" if self.home_margin > 0 else "to lose by"
        return f"{self.home.name} {verb} {abs(self.home_margin)}"

    @property
    def path(self):
        return f"/{self.sport}/picks/{self.edition}/{_matchup_slug(self.matchup)}.html"


def _document(game: Spread) -> str:
    title = escape(game.matchup)
    line = escape(game.prediction)
    context = escape(f"{game.sport_label} · {game.date_label}")
    details = escape(f"{game.play} · " + ("Neutral site" if game.neutral else f"At {game.home.name}"))
    description = escape(f"{game.away.summary} vs. {game.home.summary}") + f" · {context} · {details}"
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
    appeal = _score_details(game.bev, game.aev)
    teams = []
    for team in (game.away, game.home):
        logo = f'<img src="{escape(team.mark)}" width="60" height="60" alt="">' if team.mark else ""
        teams.append(f'<tr><th scope="row"><span class="team-line">{logo}<span class="rank">{escape(team.rank)}</span>'
                     f'<span class="school">{escape(team.name)}</span></span></th><td>{escape(team.record)}</td></tr>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {line} | SportsRank</title>
<link rel="canonical" href="{canonical}">
<meta name="description" content="{description} · {line}">
<meta property="og:type" content="website"><meta property="og:site_name" content="SportsRank">
<meta property="og:url" content="{canonical}"><meta property="og:title" content="{title} · {line}">
<meta property="og:description" content="{description}">
<meta name="twitter:card" content="summary">{preview}
<link rel="stylesheet" href="/cfb/assets/record-book.css"><link rel="stylesheet" href="/cfb/assets/spread-card.css?v=3">
<script src="/cfb/assets/spread-card.js?v=3" defer></script></head><body>{masthead(compact=True)}
<main class="spread-page"><h1 class="sr-only">{title}</h1><p class="edition">{context}</p>
<table class="game-line"><thead><tr><th scope="col">CORS rank · Team</th><th scope="col">Overall (conf)</th></tr></thead>
<tbody>{''.join(teams)}</tbody></table>
<p class="pick"><strong>{line}</strong> <span class="game-details">{details}</span></p>
{appeal}<div class="share-actions"><a id="game-link" href="{canonical}">Game link</a>
<button id="save-card" type="button" hidden>Save image</button><span id="share-status" role="status"></span></div>
<nav aria-label="Forecasts"><a class="back" href="{back}">{game.sport_label} forecasts</a>{edition_link}</nav></main></body></html>
"""


_TABLE = re.compile(r"<table\b[^>]*>.*?</table>", re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.I | re.S)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.I | re.S)
_SHARE = re.compile(r'<!--sr-ui--><a class="share-spread" [^>]*><!--/sr-ui-->(.*?)<!--sr-ui--></a><!--/sr-ui-->', re.S)
_HINT = ui('<p class="spread-hint">Select a spread to share that game.</p>')


def _score_details(bev, aev):
    if not bev:
        return ""
    metrics = [f"Pregame appeal (BEV) <strong>{escape(bev)}</strong>"]
    if aev:
        metrics.append(f"Game drama (AEV) <strong>{escape(aev)}</strong>")
    text = '<p class="game-excitement">' + " · ".join(metrics) + "</p>"
    return text + (f"<p>{ESTIMATE_NOTE}</p>" if aev and "*" in aev else "")


def complete_basketball_card(directory, day, game, score):
    """Add the reconciled final's AEV to individual pregame cards retained from an earlier slate."""
    matchup = f"{game.away} {'vs.' if game.neutral_site else 'at'} {game.home}"
    target = directory / "picks" / day / f"{_matchup_slug(matchup)}.html"
    if not target.is_file():
        return
    raw = target.read_text(encoding="utf-8")
    page = BeautifulSoup(raw, "html.parser")
    old = page.select_one(".game-excitement strong")
    if old is None or old.get_text(strip=True) != score.bev or page.h1.get_text(strip=True) != matchup:
        return
    margin = -game.home_handicap
    line = "Pick’em" if not margin else f"{game.home} {'by' if margin > 0 else 'to lose by'} {abs(margin):.2f}"
    if page.select_one(".pick strong").get_text(strip=True) != line:
        return
    start = raw.index('<p class="game-excitement">')
    end = raw.index('<div class="share-actions">', start)
    updated = raw[:start] + _score_details(score.bev, unescape(aev_label(score, plain=True))) + raw[end:]
    if updated != raw:
        target.write_text(updated, encoding="utf-8")


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
        opening = ui(f'<a class="share-spread" href="{game.path}" aria-label="Share {escape(game.matchup)}: {escape(game.prediction)}">')
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


def football_links(raw: bytes, path: Path, directory: Path, season: Season, *, excitement=None) -> bytes:
    year, week = path.stem.split("_")[:2]
    source = f"/cfb/years/{year}/spread/{path.name}"
    number = int(week[1:])
    checkpoint = f"W{number - 1}" if number else "PRESEASON"
    rows = season.rankings.get(checkpoint, ())
    standings = {r.school: (f"#{r.rank}", r.record) for r in rows}
    conference = ConferenceSlate.build(
        {r.school: r.conference for r in rows},
        (g for g in season.games if g.week < number and g.home_score is not None and g.away_score is not None),
        {("", g.home, g.away): g for g in season.games if g.week == number},
    )

    def read_game(cells):
        home = Team.from_cell(cells["home_team"], standings, conference)
        away = Team.from_cell(cells["away_team"], standings, conference)
        values = {field: cell.get_text(strip=True) for field, cell in cells.items()}
        margin, _ = _saved_margin(values, home.name)
        neutral = values["neutral_site"].lower() == "true"
        score = (excitement or {}).get(Matchup(number, home.name, away.name, neutral))
        return Spread("cfb", f"{year}/week-{week[1:]}", f"{year} · Week {week[1:]}", source,
                      home, away, neutral, margin,
                      conference.play.get(("", home.name, away.name), "Play type unavailable"),
                      score.bev if score else None, unescape(aev_label(score, plain=True)) if score and score.final else None)

    try:
        markup = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        markup = raw.decode("latin-1")
        encoding = "latin-1"
    return _link_table(markup, directory, "spread", read_game).encode(encoding, errors="xmlcharrefreplace")


def basketball_links(directory: Path, conference: ConferenceSlate) -> None:
    path = directory / "current.html"
    markup = path.read_text(encoding="utf-8")
    source = re.search(r'href="(editions/[\dTZ-]+(?:-cbbd)?\.html)"', markup)
    if source is None:
        raise ValueError("The basketball forecast has no dated edition")
    edition = directory / source[1]
    rows = _rows(edition) if edition.is_file() else ()
    standings = {row["Team"]: (f'#{row["Rank"]}', row["Record"]) for row in rows}

    def read_game(cells):
        home = Team.from_cell(cells["Home"], standings, conference)
        away = Team.from_cell(cells["Away"], standings, conference)
        tipoff = cells["Tipoff (Eastern)"].get_text(strip=True)
        date, _, time = tipoff.partition(" · ")
        day = datetime.strptime(date, "%b %d, %Y").date()
        label = date
        if time:
            label += f" · {time.lstrip('0')} ET"
        pick = cells["Reconstructed pick"].get_text(strip=True)
        margin = Decimal(0)
        if pick != "Pick’em":
            winner, points = pick.rsplit(" by ", 1)
            points = Decimal(points)
            if winner not in (away.name, home.name) or not points.is_finite() or points <= 0:
                raise ValueError(f"Invalid basketball pick: {pick}")
            margin = points if winner == home.name else -points
        bev = cells["BEV"].get_text(strip=True) if "BEV" in cells else None
        final = cells.get("Final (away–home)")
        aev = cells["AEV"].get_text(strip=True) if "AEV" in cells and final and final.get_text(strip=True) != "—" else None
        return Spread("cbb", day.isoformat(), label, "/cbb/" + source[1],
                      home, away, cells["Court"].get_text(strip=True) == "Neutral", margin,
                      conference.play.get((day.isoformat(), home.name, away.name), "Play type unavailable"),
                      bev if bev != "—" else None, aev)

    updated = _link_table(markup, directory, "Reconstructed pick", read_game)
    if updated != markup:
        path.write_text(updated, encoding="utf-8")
