"""Static conference records, strength and schedule projections."""
from collections import defaultdict
from dataclasses import dataclass
from functools import partial
from html import escape
import re
from statistics import mean, median

from .reference_archive import Season, season_team_routes
from .win_probability import win_probability


_PREFIX = {"football": "cfb", "basketball": "cbb"}
_INDEPENDENTS = {"Independents", "FBS Independents", "Indep."}
_UNAFFILIATED = _INDEPENDENTS | {"", "—"}
_DASH = "—"
_RECORD = re.compile(r"^(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?$")


def conference_route(name: str, year: int | None = None, *, sport="football") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"empty conference slug for {name!r}")
    prefix = _PREFIX[sport]
    return f"/{prefix}/conferences/{slug}/{year or 'index'}.html"


@dataclass
class Record:
    wins: int = 0
    losses: int = 0
    ties: int = 0
    margin: int = 0

    @classmethod
    def from_text(cls, text):
        match = _RECORD.fullmatch(text.strip())
        return cls(*(int(part or 0) for part in match.groups())) if match else None

    def __add__(self, other):
        return Record(self.wins + other.wins, self.losses + other.losses,
                      self.ties + other.ties, self.margin + other.margin)

    def add_game(self, margin):
        self.wins += margin > 0
        self.losses += margin < 0
        self.ties += margin == 0
        self.margin += margin

    @property
    def games(self):
        return self.wins + self.losses + self.ties

    @property
    def win_pct(self):
        return (self.wins + self.ties / 2) / self.games if self.games else None

    def __str__(self):
        parts = (self.wins, self.losses, self.ties) if self.ties else (self.wins, self.losses)
        return "–".join(map(str, parts))


def _is_conference_game(game, home_conference, away_conference):
    return (not game.postseason and home_conference not in _UNAFFILIATED
            and home_conference == away_conference and game.conference_game is not False)


def _opponent_conference(standing, division, sport, groups):
    if standing and standing.conference not in {"", _DASH}:
        return standing.conference
    if sport == "basketball" and not standing:
        return "Non-D-I"
    if not standing:
        return "FCS" if division.lower() == "fcs" else "Other"
    return next((name for name in groups if name in _INDEPENDENTS), "Independents")


def _football_deltas(season, games):
    rows_by_week = defaultdict(list)
    for index, game in enumerate(games):
        rows_by_week[game.week, game.home].append((index, game))
        rows_by_week[game.week, game.away].append((index, game))

    deltas = {}
    previous = {}
    for key, rows in season.rankings.items():
        current = {row.school: row.rating for row in rows}
        if key.startswith("W"):
            week = int(key[1:])
            for school in previous.keys() & current.keys():
                played = rows_by_week.get((week, school), ())
                if not played:
                    continue
                change = float(current[school] - previous[school])
                if len(played) == 1:
                    deltas[played[0][0], school] = change
                else:
                    # Allocate the observed weekly movement in proportion to
                    # robust-weighted absolute forecast errors. Equal residuals
                    # (including all zero) get equal shares.
                    weights = []
                    for _, game in played:
                        predicted = _football_margin(game, previous, season.home_advantage)
                        residual = abs(game.home_score - game.away_score - predicted)
                        weights.append(residual / (14 + residual**2 / 225))
                    total = sum(weights)
                    for (index, _), weight in zip(played, weights, strict=True):
                        deltas[index, school] = change * (weight / total if total else 1 / len(played))
        previous = current
    return deltas


def _football_margin(game, ratings, home_advantage):
    if game.home_handicap is not None:
        return -float(game.home_handicap)

    def rating(school, division):
        return float(ratings.get(school, -26 if division.lower() == "fcs" else -22))

    venue = 0.0 if game.neutral_site else home_advantage
    return rating(game.home, game.home_division) - rating(game.away, game.away_division) + venue


@dataclass
class SeasonRecords:
    league: dict
    ooc: dict
    played: set
    best: dict
    worst: dict
    remaining: dict
    remaining_league: dict


def _remaining_probabilities(season, checkpoint, standings, sport):
    if checkpoint == "FINAL":
        return
    football = sport == "football"
    week = int(checkpoint[1:]) if checkpoint.startswith("W") else -1
    ratings = {name: row.rating for name, row in standings.items()}
    for game in season.games:
        if game.postseason or game.home_score is not None or game.away_score is not None:
            continue
        if football:
            if game.week <= week:
                continue
            margin = _football_margin(game, ratings, season.home_advantage)
        elif game.home_handicap is not None:
            margin = -float(game.home_handicap)
        else:
            continue
        yield game, win_probability(margin, sport=sport)


def _overall_probabilities(matchups):
    by_school = defaultdict(list)
    for game, home_prob in matchups:
        by_school[game.home].append(home_prob)
        by_school[game.away].append(1 - home_prob)
    return by_school


def _season_records(season, checkpoint, standings, groups, sport):
    football = sport == "football"
    week = int(checkpoint[1:]) if checkpoint.startswith("W") else -1
    completed = [g for g in season.games if g.home_score is not None and g.away_score is not None
                 and (not football or checkpoint == "FINAL" or g.week <= week)]
    deltas = _football_deltas(season, completed) if football else {}
    remaining = list(_remaining_probabilities(season, checkpoint, standings, sport))
    stats = SeasonRecords(defaultdict(Record), defaultdict(lambda: defaultdict(Record)), set(),
                          {}, {}, _overall_probabilities(remaining), defaultdict(list))

    def conferences(game):
        return (
            _opponent_conference(standings.get(game.home), game.home_division, sport, groups),
            _opponent_conference(standings.get(game.away), game.away_division, sport, groups),
        )

    for index, game in enumerate(completed):
        home_conf, away_conf = conferences(game)
        league = _is_conference_game(game, home_conf, away_conf)
        for school, conf, opponent_conf, home in (
            (game.home, home_conf, away_conf, True), (game.away, away_conf, home_conf, False),
        ):
            if school not in standings:
                continue
            stats.played.add(school)
            margin = (game.home_score - game.away_score) * (1 if home else -1)
            if league:
                stats.league[school].add_game(margin)
            elif conf != opponent_conf or conf in _INDEPENDENTS:
                stats.ooc[conf][opponent_conf].add_game(margin)
            delta = (deltas.get((index, school)) if football else
                     game.home_cors_delta if home else game.away_cors_delta)
            if delta is not None and margin:
                extremes = stats.best if margin > 0 else stats.worst
                old = extremes.get(school)
                if old is None or (delta - old[0]) * (1 if margin > 0 else -1) > 0:
                    extremes[school] = (delta, game, home)

    for game, home_prob in remaining:
        if _is_conference_game(game, *conferences(game)):
            stats.remaining_league[game.home].append(home_prob)
            stats.remaining_league[game.away].append(1 - home_prob)
    return stats


def _project(record, probabilities):
    if record is None:
        return _DASH
    wins = record.wins + record.ties / 2 + sum(probabilities)
    losses = record.losses + record.ties / 2 + sum(1 - p for p in probabilities)
    return f"{wins:.1f}–{losses:.1f}"


def _project_overall(standing, probabilities):
    return _project(Record.from_text(standing.record), probabilities)


def projected_overall_records(season: Season, *, sport: str) -> dict[str, str]:
    """Project overall W–L at the conference page's latest checkpoint."""
    checkpoint = next(reversed(season.rankings), None)
    if checkpoint is None or checkpoint == "FINAL":
        return {}
    standings = {row.school: row for row in season.rankings[checkpoint]}
    probabilities = _overall_probabilities(_remaining_probabilities(season, checkpoint, standings, sport))
    return {school: _project_overall(row, probabilities[school]) for school, row in standings.items()}


def _percentage(record):
    return f"{record.win_pct:.1%}" if record.win_pct is not None else _DASH


def _record_with_pct(record):
    if record is None:
        return _DASH
    return f"{record} ({_percentage(record)})" if record.games else str(record)


def _page(title: str, content: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{escape(title)} | SportsRank</title></head><body><main>'
            f'<h1>{escape(title)}</h1>{content}</main></body></html>')


def _table(label, headers, rows, *, footer=""):
    headings = "".join(f'<th scope="col">{escape(heading)}</th>' for heading in headers)
    return (f'<div class="table-scroll" role="region" aria-label="{label}" tabindex="0">'
            f'<table data-sortable aria-label="{label}"><thead><tr>{headings}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody>{footer}</table></div>')


def _game_cell(selection, standings, team_routes):
    if selection is None:
        return f"<td>{_DASH}</td>"
    delta, game, home = selection
    opponent = game.away if home else game.home
    wins, loses = (game.home_score, game.away_score) if home else (game.away_score, game.home_score)
    label = escape(opponent)
    if opponent in team_routes:
        label = f'<a href="{team_routes[opponent]}">{label}</a>'
    if opponent in standings:
        label = f"#{standings[opponent].rank} {label}"
    site = "vs (N)" if game.neutral_site else "vs" if home else "@"
    result = "W" if wins > loses else "L"
    return f'<td data-sort-value="{delta}">{result} {wins}–{loses} {site} {label} ({delta:+.2f})</td>'


def _standings_table(members, stats, preseason, standings, team_routes, *, active, independent, covered):
    def order(member):
        rec = stats.league[member.school]
        pct = rec.win_pct if rec.win_pct is not None else -1
        return -pct, -rec.wins, rec.losses, -member.rating, member.rank

    headers = ["National rank", "Team", "Overall", "Conf", "CORS rating", "Pre",
               "Best win (Δ CORS)", "Worst loss (Δ CORS)"]
    if active:
        headers += ["Proj conf", "Proj overall"]
    rows = []
    for member in sorted(members, key=order if not independent and covered else lambda m: m.rank):
        school = member.school
        conf = stats.league[school] if not independent and covered else None
        prior = preseason.get(school)
        pre_rank = prior.rank if prior else _DASH
        movement = prior.rank - member.rank if prior else 0
        pre = f'{pre_rank} ({movement:+d})' if movement else str(pre_rank)
        row = (f'<td class="num">{member.rank}</td>'
               f'<th scope="row"><a href="{team_routes[school]}">{escape(school)}</a></th>'
               f'<td>{escape(member.record)}</td><td>{conf if conf is not None else _DASH}</td>'
               f'<td class="num">{member.rating:.2f}</td><td class="num" data-sort-value="{pre_rank}">{pre}</td>'
               + _game_cell(stats.best.get(school), standings, team_routes)
               + _game_cell(stats.worst.get(school), standings, team_routes))
        if active:
            proj_conf = _project(stats.league[school] if not independent else None, stats.remaining_league[school])
            proj_overall = _project_overall(member, stats.remaining[school])
            row += f'<td class="num">{proj_conf}</td><td class="num">{proj_overall}</td>'
        rows.append(f"<tr>{row}</tr>")
    return _table("Conference standings", headers, rows)


def _ooc_table(buckets, total, groups, route, year):
    def cells(record):
        margin = f"{record.margin / record.games:+.1f}" if record.games else _DASH
        return f'<td>{record}</td><td class="num">{_percentage(record)}</td><td class="num">{margin}</td>'

    rows = []
    for opponent, record in sorted(buckets.items()):
        label = escape(opponent)
        if opponent in groups:
            label = f'<a href="{route(opponent, year)}">{label}</a>'
        rows.append(f'<tr><th scope="row">{label}</th>{cells(record)}</tr>')
    footer = f'<tfoot><tr><th scope="row">Total OOC</th>{cells(total)}</tr></tfoot>'
    return ("<h2>Out-of-conference record</h2>"
            + _table("Out-of-conference record", ("Opponent conference", "Record", "Win %", "Avg margin"),
                     rows, footer=footer))


@dataclass
class ConferenceSummary:
    rank: int | None
    ratings: list
    top_25: int
    overall: Record | None
    ooc: Record | None
    preseason: list

    def render(self, ranked_count):
        average = mean(self.ratings)
        pre = _DASH
        if self.preseason:
            prior = mean(self.preseason)
            pre = f"{prior:.2f} ({average - prior:+.2f})"
        metrics = [
            ("Conference rank", f"#{self.rank} of {ranked_count}" if self.rank else _DASH),
            ("Mean CORS", f"{average:.2f}"), ("Median CORS", f"{median(self.ratings):.2f}"),
            ("Top-25 teams", str(self.top_25)),
            ("CORS spread (high–low)", f"{max(self.ratings) - min(self.ratings):.2f}"),
            ("Combined overall", _record_with_pct(self.overall)), ("Combined OOC", _record_with_pct(self.ooc)),
            ("Preseason Mean CORS", pre),
        ]
        items = "".join(f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in metrics)
        return f'<h2>Conference summary</h2><dl class="metrics conference-summary">{items}</dl>'


def _season_pages(season, groups, standings, route, routes, sport, year_label, season_route):
    checkpoint = next(reversed(season.rankings))
    stats = _season_records(season, checkpoint, standings, groups, sport)
    preseason = {r.school: r for r in season.rankings.get("PRESEASON" if sport == "football" else "W0", ())}
    order = sorted((name for name, members in groups.items() if name not in _INDEPENDENTS and len(members) > 1),
                   key=lambda name: (-mean(r.rating for r in groups[name]), name))
    ranks = {name: rank for rank, name in enumerate(order, 1)}
    team_routes = season_team_routes(season, routes)
    year = season.year
    edition = f"Through Week {checkpoint[1:]}" if checkpoint.startswith("W") else checkpoint.title()
    active = checkpoint != "FINAL"
    for name, members in groups.items():
        records = [Record.from_text(r.record) for r in members]
        covered = any(r.school in stats.played for r in members)
        summary = ConferenceSummary(
            ranks.get(name), [r.rating for r in members], sum(r.rank <= 25 for r in members),
            sum(records, Record()) if all(record is not None for record in records) else None,
            sum(stats.ooc[name].values(), Record()) if covered else None,
            [preseason[r.school].rating for r in members if r.school in preseason],
        )
        nav = (f'<nav><a href="{route(name)}">{escape(name)} history</a> · '
               f'<a href="{season_route(year)}">{year_label(year)} national rankings</a></nav>'
               f'<p class="edition">{edition}</p>')
        table = _standings_table(members, stats, preseason, standings, team_routes, active=active,
                                 independent=name in _INDEPENDENTS, covered=covered)
        notes = ""
        if covered:
            classification = (" Football seasons without game phase labels use the archived week or date."
                              if sport == "football" else
                              " Historical game logs can include unmarked conference tournament games.")
            notes = ('<p class="conference-note">Conf counts regular-season games against league members. '
                     'Best wins and worst losses use the opponent’s rank in this edition.'
                     f'{classification}</p>')
            if season.conference_note:
                notes += f'<p class="conference-note">{escape(season.conference_note)}</p>'
        if active:
            scope = " within Division I" if sport == "basketball" else ""
            notes += (f'<p class="conference-note">Projections use the available regular-season schedule{scope}.</p>')
        ooc = _ooc_table(stats.ooc[name], summary.ooc, groups, route, year) if covered else ""
        title = f"{year_label(year)} {name}"
        yield name, summary, _page(title, nav + summary.render(len(ranks)) + table + notes + ooc)


def _strength_table(summaries, route, year, year_label):
    rows = []
    for name, summary in sorted(summaries.items(), key=lambda item: (item[1].rank or float("inf"), item[0])):
        rank = f"#{summary.rank}" if summary.rank else _DASH
        rows.append(
            f'<tr><td class="num">{rank}</td><th scope="row"><a href="{route(name, year)}">{escape(name)}</a></th>'
            f'<td class="num">{len(summary.ratings)}</td><td class="num">{mean(summary.ratings):.2f}</td>'
            f'<td class="num">{median(summary.ratings):.2f}</td><td class="num">{summary.top_25}</td>'
            f'<td>{summary.ooc if summary.ooc is not None else _DASH}</td>'
            f'<td>{summary.overall if summary.overall is not None else _DASH}</td></tr>'
        )
    return (f'<h2>{year_label(year)} conference strength</h2>'
            + _table("Latest-season conference strength",
                     ("Rank", "Conference", "Teams", "Mean CORS", "Median CORS", "Top 25", "OOC record", "Overall record"), rows))


def build_conference_pages(seasons: list[Season], routes: dict[str, str], *, sport="football", year_label=str,
                           season_route=lambda y: f"/cfb/years/{y}/{y}_CFB.html"):
    owners = {}
    histories = defaultdict(list)
    route = partial(conference_route, sport=sport)
    directory = f"/{_PREFIX[sport]}/conferences/index.html"
    latest = {}
    year = None
    for season in sorted(seasons, key=lambda season: season.year):
        checkpoint = next(reversed(season.rankings))
        standings = {row.school: row for row in season.rankings[checkpoint]}
        groups = defaultdict(list)
        for row in standings.values():
            if row.conference and row.conference != _DASH:
                groups[row.conference].append(row)
        if not groups:
            continue
        year = season.year
        latest = {}
        for name, summary, markup in _season_pages(season, groups, standings, route, routes, sport, year_label, season_route):
            if owners.setdefault(route(name), name) != name:
                raise ValueError(f"ambiguous conference route for {name!r}")
            histories[name].append(year)
            latest[name] = summary
            yield route(name, year).lstrip("/"), markup

    items = []
    for name, years in sorted(histories.items()):
        history = route(name)
        decades = defaultdict(list)
        for season_year in reversed(years):
            decades[season_year // 10 * 10].append(f'<a href="{route(name, season_year)}">{year_label(season_year)}</a>')
        links = "".join(f'<div><h3>{decade}s</h3><p>{" ".join(entries)}</p></div>'
                        for decade, entries in decades.items())
        yield history.lstrip("/"), _page(name, f'<nav><a href="{directory}">Conferences</a></nav>'
                                              f'<h2>Seasons</h2><div class="year-archive">{links}</div>')
        items.append(f'<li><a href="{history}">{escape(name)}</a></li>')
    strength = _strength_table(latest, route, year, year_label) if latest else ""
    all_conferences = '<h2>All conferences</h2><ul class="program-list">' + "".join(items) + "</ul>"
    yield directory.lstrip("/"), _page(f"College {sport} conferences", strength + all_conferences)
