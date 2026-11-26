"""Static conference records, strength and schedule projections."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from html import escape
import re
from statistics import mean, median

from .conference_matchups import build_matchup_pages, conference_pair, matchup_route, upcoming_matchup_games
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


def conference_comparison_route(year: int, *, sport="football") -> str:
    return f"/{_PREFIX[sport]}/conferences/{year}.html"


def conference_honors_route(*, sport="football") -> str:
    return f"/{_PREFIX[sport]}/history/conference-honors.html"


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
    ooc_matchups: dict


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
    ranks = {key: {r.school: r.rank for r in rows} for key, rows in season.rankings.items()}
    pregame = {}
    prior = ranks.get("PRESEASON", {})
    for key, edition in ranks.items():
        if key.startswith("W"):
            pregame[int(key[1:])] = prior if football else edition
            prior = edition
    remaining = list(_remaining_probabilities(season, checkpoint, standings, sport))
    stats = SeasonRecords(defaultdict(Record), defaultdict(lambda: defaultdict(Record)), set(),
                          {}, {}, _overall_probabilities(remaining), defaultdict(list), defaultdict(list))

    def conferences(game):
        return (
            _opponent_conference(standings.get(game.home), game.home_division, sport, groups),
            _opponent_conference(standings.get(game.away), game.away_division, sport, groups),
        )

    def add_ooc(game, home_conf, away_conf, margin=None):
        if home_conf == away_conf and home_conf not in _INDEPENDENTS:
            return
        if game.home not in standings and game.away not in standings:
            return
        stats.ooc_matchups[conference_pair(home_conf, away_conf)].append(game)
        for school, conf, opponent, sign in (
            (game.home, home_conf, away_conf, 1), (game.away, away_conf, home_conf, -1),
        ):
            if school in standings:
                record = stats.ooc[conf][opponent]
                if margin is not None:
                    record.add_game(sign * margin)

    for game in completed:
        home_conf, away_conf = conferences(game)
        league = _is_conference_game(game, home_conf, away_conf)
        home_margin = game.home_score - game.away_score
        if not league:
            add_ooc(game, home_conf, away_conf, home_margin)
        for school, home in (
            (game.home, True), (game.away, False),
        ):
            if school not in standings:
                continue
            stats.played.add(school)
            margin = home_margin if home else -home_margin
            if league:
                stats.league[school].add_game(margin)
            delta = game.home_cors_delta if home else game.away_cors_delta
            if delta is not None and margin:
                extremes = stats.best if margin > 0 else stats.worst
                old = extremes.get(school)
                if old is None or (delta - old[0]) * (1 if margin > 0 else -1) > 0:
                    opponent = game.away if home else game.home
                    rank = pregame.get(game.week, {}).get(opponent)
                    extremes[school] = (delta, game, home, rank)

    for game, home_prob in remaining:
        if _is_conference_game(game, *conferences(game)):
            stats.remaining_league[game.home].append(home_prob)
            stats.remaining_league[game.away].append(1 - home_prob)
    for game in upcoming_matchup_games(season.games, completed, checkpoint, sport):
        add_ooc(game, *conferences(game))
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


def _game_cell(selection, team_routes):
    if selection is None:
        return f"<td>{_DASH}</td>"
    delta, game, home, rank = selection
    opponent = game.away if home else game.home
    wins, loses = (game.home_score, game.away_score) if home else (game.away_score, game.home_score)
    label = escape(opponent)
    if opponent in team_routes:
        label = f'<a href="{team_routes[opponent]}">{label}</a>'
    if rank is not None:
        label = f"#{rank} {label}"
    site = "vs (N)" if game.neutral_site else "vs" if home else "@"
    result = "W" if wins > loses else "L"
    return f'<td data-sort-value="{delta}">{result} {wins}–{loses} {site} {label} ({delta:+.2f})</td>'


def _standings_table(members, stats, preseason, team_routes, *, active, independent, covered):
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
               + _game_cell(stats.best.get(school), team_routes)
               + _game_cell(stats.worst.get(school), team_routes))
        if active:
            proj_conf = _project(stats.league[school] if not independent else None, stats.remaining_league[school])
            proj_overall = _project_overall(member, stats.remaining[school])
            row += f'<td class="num">{proj_conf}</td><td class="num">{proj_overall}</td>'
        rows.append(f"<tr>{row}</tr>")
    return _table("Conference standings", headers, rows)


def _ooc_table(buckets, total, groups, route, year, *, conference):
    def cells(record, label):
        margin = f"{record.margin / record.games:+.1f}" if record.games else _DASH
        return f'<td>{label}</td><td class="num">{_percentage(record)}</td><td class="num">{margin}</td>'

    rows = []
    for opponent, record in sorted(buckets.items()):
        label = escape(opponent)
        if opponent in groups:
            label = f'<a href="{route(opponent, year)}">{label}</a>'
        href = matchup_route(conference, opponent, year, route)
        description = escape(f"{record} against {opponent} · game log")
        record_link = f'<a href="{escape(href)}" aria-label="{description}">{record}</a>'
        rows.append(f'<tr><th scope="row">{label}</th>{cells(record, record_link)}</tr>')
    footer = ""
    note = ""
    if total is not None:
        footer = f'<tfoot><tr><th scope="row">Total OOC</th>{cells(total, total)}</tr></tfoot>'
    else:
        note = '<p class="conference-note">No completed games are available for this conference in this edition.</p>'
    return ("<h2>Out-of-conference record</h2>" + note
            + _table("Out-of-conference record", ("Opponent conference", "Record", "Win %", "Avg margin"),
                     rows, footer=footer))


@dataclass
class ConferenceSummary:
    rank: int | None
    team_count: int
    mean_cors: Decimal
    median_cors: Decimal
    cors_spread: Decimal
    top_25: int
    overall: Record | None
    ooc: Record | None
    preseason_mean: Decimal | None

    def preseason_display(self):
        if self.preseason_mean is None:
            return _DASH
        return f"{self.preseason_mean:.2f} ({self.mean_cors - self.preseason_mean:+.2f})"

    def rank_link(self, ranked_count, comparison, label):
        if self.rank is None:
            return _DASH
        rank = f"#{self.rank} of {ranked_count}"
        return (f'<a href="{comparison}" aria-label="{rank} in the {escape(label)} conference comparison">'
                f'{rank}</a>')

    def render(self, ranked_count, comparison, label):
        metrics = [
            ("Conference rank", self.rank_link(ranked_count, comparison, label)),
            ("Mean CORS", f"{self.mean_cors:.2f}"), ("Median CORS", f"{self.median_cors:.2f}"),
            ("Top-25 teams", str(self.top_25)),
            ("CORS spread (high–low)", f"{self.cors_spread:.2f}"),
            ("Combined overall", _record_with_pct(self.overall)), ("Combined OOC", _record_with_pct(self.ooc)),
            ("Preseason Mean CORS", self.preseason_display()),
        ]
        items = "".join(f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in metrics)
        return f'<h2>Conference summary</h2><dl class="metrics conference-summary">{items}</dl>'


def _edition(checkpoint):
    return f"Through Week {checkpoint[1:]}" if checkpoint.startswith("W") else checkpoint.title()


def _season_pages(season, checkpoint, groups, stats, route, team_routes, sport, label, season_route):
    preseason = {r.school: r for r in season.rankings.get("PRESEASON" if sport == "football" else "W0", ())}
    averages = {name: mean(r.rating for r in members) for name, members in groups.items()}
    order = sorted((name for name, members in groups.items() if name not in _INDEPENDENTS and len(members) > 1),
                   key=lambda name: (-averages[name], name))
    ranks = {name: rank for rank, name in enumerate(order, 1)}
    year = season.year
    comparison = conference_comparison_route(year, sport=sport)
    active = checkpoint != "FINAL"
    for name, members in groups.items():
        records = [Record.from_text(r.record) for r in members]
        covered = any(r.school in stats.played for r in members)
        ratings = [r.rating for r in members]
        prior = [preseason[r.school].rating for r in members if r.school in preseason]
        summary = ConferenceSummary(
            rank=ranks.get(name),
            team_count=len(members),
            mean_cors=averages[name],
            median_cors=median(ratings),
            cors_spread=max(ratings) - min(ratings),
            top_25=sum(r.rank <= 25 for r in members),
            overall=sum(records, Record()) if all(record is not None for record in records) else None,
            ooc=sum(stats.ooc[name].values(), Record()) if covered else None,
            preseason_mean=mean(prior) if prior else None,
        )
        links = [(f"{name} history", route(name)), (f"{label} national rankings", season_route(year))]
        if summary.rank is None:
            links.append(("Conference comparison", comparison))
        nav = _conference_nav(links) + f'<p class="edition">{_edition(checkpoint)}</p>'
        table = _standings_table(members, stats, preseason, team_routes, active=active,
                                 independent=name in _INDEPENDENTS, covered=covered)
        notes = ""
        if covered:
            classification = (" Football seasons without game phase labels use the archived week or date."
                              if sport == "football" else
                              " Historical game logs can include unmarked conference tournament games.")
            notes = ('<p class="conference-note">Conf counts regular-season games against league members. '
                     'Opponent ranks are from before the game. Best wins and worst losses show the individual '
                     'game’s impact on the team’s CORS rating at the first update after the game.'
                     f'{classification}</p>')
            if season.conference_note:
                notes += f'<p class="conference-note">{escape(season.conference_note)}</p>'
        if active:
            scope = " within Division I" if sport == "basketball" else ""
            notes += (f'<p class="conference-note">Projections use the available regular-season schedule{scope}.</p>')
        ooc = (_ooc_table(stats.ooc[name], summary.ooc, groups, route, year, conference=name)
               if covered or stats.ooc[name] else "")
        title = f"{label} {name}"
        yield name, summary, _page(title, nav + summary.render(len(ranks), comparison, label) + table + notes + ooc)


def _record_cell(record):
    return f'<td data-sort-value="{record or _DASH}">{_record_with_pct(record)}</td>'


def _strength_table(summaries, route, year):
    rows = []
    for name, summary in summaries:
        rank = f"#{summary.rank}" if summary.rank else _DASH
        prior = summary.preseason_mean if summary.preseason_mean is not None else _DASH
        rows.append(
            f'<tr><td class="num">{rank}</td><th scope="row"><a href="{route(name, year)}">{escape(name)}</a></th>'
            f'<td class="num">{summary.team_count}</td><td class="num">{summary.mean_cors:.2f}</td>'
            f'<td class="num">{summary.median_cors:.2f}</td><td class="num">{summary.top_25}</td>'
            f'<td class="num">{summary.cors_spread:.2f}</td>'
            f'{_record_cell(summary.ooc)}{_record_cell(summary.overall)}'
            f'<td class="num" data-sort-value="{prior}">{summary.preseason_display()}</td></tr>'
        )
    return _table(
        "Conference strength",
        ("Rank", "Conference", "Teams", "Mean CORS", "Median CORS", "Top 25", "CORS spread (high–low)",
         "OOC record", "Overall record", "Preseason Mean CORS"),
        rows,
    )


def _conference_nav(links):
    anchors = " · ".join(f'<a href="{href}">{escape(label)}</a>' for label, href in links)
    return f'<nav class="archive-nav" aria-label="Conference pages">{anchors}</nav>'


CONFERENCE_HONORS = "Best and worst conferences by year"
_TEAM_HONORS = {
    "football": ("/cfb/history/nc_FBS_CFB_output.html", "/cfb/history/wt_FBS_CFB_output.html"),
    "basketball": ("/cbb/history/champions.html", "/cbb/history/last-place.html"),
}


def _conference_honors(finals, route, sport, directory, year_label, season_route):
    champions, last_place = _TEAM_HONORS[sport]
    nav = _conference_nav((("Conferences", directory), ("CORS national champions", champions),
                           ("Last-place CORS teams", last_place)))
    rows = []
    for year in sorted(finals, reverse=(sport == "basketball")):
        ranked = finals[year]
        best_name, best = ranked[0]
        worst_name, worst = ranked[-1]
        label = escape(year_label(year))
        comparison = conference_comparison_route(year, sport=sport)
        rows.append(
            f'<tr><th scope="row"><a href="{season_route(year)}">{label}</a></th>'
            f'<td><a href="{route(best_name, year)}">{escape(best_name)}</a></td>'
            f'<td><a href="{route(worst_name, year)}">{escape(worst_name)}</a></td>'
            f'<td class="num">{best.mean_cors:.2f}</td><td class="num">{worst.mean_cors:.2f}</td>'
            f'{_record_cell(best.overall)}{_record_cell(worst.overall)}'
            f'<td class="num"><a href="{comparison}" aria-label="Compare all {len(ranked)} ranked conferences '
            f'in {label}">{len(ranked)}</a></td></tr>'
        )
    if rows:
        body = '<p>First and last among ranked conferences by final mean CORS. Records combine the member teams’ results.</p>'
        body += _table(CONFERENCE_HONORS, ("Season", "Best conference", "Worst conference", "Best mean CORS",
                       "Worst mean CORS", "Best overall record", "Worst overall record", "Ranked conferences"), rows)
    else:
        body = '<p>No completed seasons have ranked conferences.</p>'
    return _page(CONFERENCE_HONORS, nav + body)


def build_conference_pages(seasons: list[Season], routes: dict[str, str], *, sport="football", year_label=str,
                           season_route=lambda y: f"/cfb/years/{y}/{y}_CFB.html"):
    owners = {}
    histories = defaultdict(list)
    route = partial(conference_route, sport=sport)
    directory = f"/{_PREFIX[sport]}/conferences/index.html"
    comparisons = {}
    finals = {}
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
        summaries = []
        team_routes = season_team_routes(season, routes)
        stats = _season_records(season, checkpoint, standings, groups, sport)
        for name, summary, markup in _season_pages(
            season, checkpoint, groups, stats, route, team_routes, sport, year_label(year), season_route,
        ):
            if owners.setdefault(route(name), name) != name:
                raise ValueError(f"ambiguous conference route for {name!r}")
            histories[name].append(year)
            summaries.append((name, summary))
            yield route(name, year).lstrip("/"), markup
        yield from build_matchup_pages(stats.ooc_matchups, groups, route, year, year_label, team_routes,
                                       page=_page, sport=sport, edition=_edition(checkpoint))
        summaries.sort(key=lambda item: (item[1].rank or float("inf"), item[0]))
        ranked = [(name, summary) for name, summary in summaries if summary.rank is not None]
        comparisons[year] = (checkpoint, _strength_table(summaries, route, year), bool(ranked))
        if checkpoint == "FINAL" and ranked:
            finals[year] = ranked

    for year, (checkpoint, table, has_ranks) in comparisons.items():
        label = year_label(year)
        nav = _conference_nav((("Conferences", directory), (f"{label} national rankings", season_route(year))))
        note = ("A conference needs at least two ranked teams to qualify. "
                "Rank follows mean CORS, with equal means ordered by conference name. Independents are unranked.")
        if not has_ranks:
            note = "No conferences qualify for a rank this season. " + note
        yield conference_comparison_route(year, sport=sport).lstrip("/"), _page(
            f"{label} conference comparison",
            f'{nav}<p class="edition">{_edition(checkpoint)}</p><p class="conference-note">{note}</p>{table}',
        )
    yield conference_honors_route(sport=sport).lstrip("/"), _conference_honors(
        finals, route, sport, directory, year_label, season_route,
    )

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
    links = [(CONFERENCE_HONORS, conference_honors_route(sport=sport))]
    strength = ""
    if comparisons:
        latest = next(reversed(comparisons))
        links.insert(0, (f"{year_label(latest)} comparison", conference_comparison_route(latest, sport=sport)))
        strength = f'<h2>{escape(year_label(latest))} conference strength</h2>{comparisons[latest][1]}'
    all_conferences = '<h2>All conferences</h2><ul class="program-list">' + "".join(items) + "</ul>"
    yield directory.lstrip("/"), _page(f"College {sport} conferences", _conference_nav(links) + strength + all_conferences)
