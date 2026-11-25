"""Rebuild basketball conferences offline using replay and retained site evidence."""
import argparse
from collections import Counter
import csv
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from lxml import html

from cfb.conference_pages import build_conference_pages
from cfb.reference_archive import Game, Season, Standing
from cfb.reference_markup import PagePresentation
from site_navigation import masthead

from .cors_v1 import _available, cors_v1_seasons
from .historical_replay import replay_historical_seasons
from .record_book import SECTIONS, _record, conference_game, conference_slate, season_name
from .replay import replay_seasons
from .team_schedule import EASTERN, conference_tipoff, projection_key, schedule_views


def _published_standings(path):
    doc = html.parse(str(path))
    rows = doc.xpath('//table[@aria-labelledby="cors-ranking"]/tbody/tr')
    return [Standing(c[1].text_content(), int(c[0].text_content()), Decimal(c[4].text_content()),
                     c[5].text_content(), c[6].text_content()) for c in rows]


def _standings(checkpoint):
    return [Standing(r["school"], r["rank"], Decimal(f'{r["cors"]:.2f}'), _record(r),
                     r["conference"] or "—") for r in checkpoint["rows"]]


def _fixture_key(start, home, away, neutral):
    return datetime.fromisoformat(start), home, away, neutral


def _retained_results(site, year, cutoff, standings, replayed):
    results = {}
    records = Counter()
    with (site / f"performance/{year}/games.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["status"] != "Evaluated":
                continue
            start = row["start_utc"]
            if datetime.fromisoformat(_available(datetime.fromisoformat(start))) > cutoff:
                continue
            home, away = row["home"], row["away"]
            h, a = int(row["home_score"]), int(row["away_score"])
            neutral = row["neutral_site"] == "True"
            key = _fixture_key(start, home, away, neutral)
            game = replayed.get(key)
            if game is None:
                game = Game(int(row["week"]), home, away, h, a, Decimal(row["home_handicap"]), neutral,
                            **conference_tipoff(key[0]))
            elif (game.home_score, game.away_score) != (h, a):
                game = replace(game, home_score=h, away_score=a, home_cors_delta=None, away_cors_delta=None)
            if key in results:
                if results[key] != game:
                    raise ValueError(f"Conflicting retained basketball result for {key}")
                continue
            results[key] = game
            records[home, h > a] += 1
            records[away, a > h] += 1

    for row in standings:
        record = f"{records[row.school, True]}–{records[row.school, False]}"
        if row.record != record:
            raise ValueError(f"{year} retained results for {row.school}: {record}, expected {row.record}")
    return list(results.values())


def _upcoming_rows(path):
    anchors = html.parse(str(path)).xpath('//*[@id="remaining-schedule"]')
    if not anchors:
        return
    anchor, = anchors
    if anchor.tag == "tr":
        for row in [anchor, *anchor.xpath("following-sibling::tr | ../following-sibling::tbody/tr")]:
            yield row[0], *row[-5:-2]
    else:
        # Older pages used a separate table, with or without a Win chance column.
        for row in anchor.xpath(".//tbody/tr"):
            yield row[0], *row[2:5]


def _published_tipoff(day, clock):
    clock = clock.strip()
    known = clock not in ("", "Time TBD")
    local_time = datetime.strptime(clock.removesuffix(" ET"), "%I:%M %p").time() if known else time.min
    return conference_tipoff(datetime.combine(day, local_time, EASTERN), time_known=known)


def _retained_schedule(site, year, standings, routes, week_page, replayed):
    by_url = {f"{route.rsplit('/', 1)[0]}/{year}.html": name for name, route in routes.items()}
    schedule = {}
    for row in standings:
        route = routes[row.school].removeprefix("/cbb/").rsplit("/", 1)[0]
        path = site / route / f"{year}.html"
        for day, opponent, venue, handicap in _upcoming_rows(path):
            links = opponent.xpath("./a/@href")
            other = by_url[links[0]] if links else opponent.text_content().strip()
            side = venue.text_content()
            home, away = row.school, other
            line = handicap.text_content().strip()
            spread = None if line == "—" else Decimal(0 if line == "Pick’em" else line)
            if side == "Away" or (side == "Neutral" and home > away):
                home, away = away, home
                if spread is not None:
                    spread = -spread
            month_day = datetime.strptime(f'{day.text.split(", ", 1)[1]} {year}', "%b %d %Y").date()
            day_key = date(year - (month_day.month >= 7), month_day.month, month_day.day)
            clock = day.findtext("small")
            if clock is None:
                clock = day.getnext().text_content()
            key = projection_key(day_key, home, away, side == "Neutral")
            game = Game(0, home, away, None, None, spread, side == "Neutral", **_published_tipoff(day_key, clock))
            if schedule.setdefault(key, game) != game:
                raise ValueError(f"Conflicting retained basketball projection for {key}")
    # Team schedule pages show games still upcoming at publication. The
    # retained week slate also covers fixtures between cutoff and publication.
    for table in html.parse(str(week_page)).xpath("//main//table"):
        if "Reconstructed pick" not in table.xpath("./thead/tr/th/text()"):
            continue
        for tipoff, visitor, host, venue, pick, _ in table.xpath("./tbody/tr"):
            date_label, _, clock = tipoff.text_content().partition(" · ")
            day_key = datetime.strptime(date_label, "%b %d, %Y").date()
            away, home = (cell.text_content() for cell in (visitor, host))
            line = pick.text_content()
            spread = Decimal(0)
            if line != "Pick’em":
                winner, margin = line.rsplit(" by ", 1)
                spread = Decimal(margin) * (-1 if winner == home else 1)
            neutral = venue.text_content() == "Neutral"
            if neutral and home > away:
                home, away, spread = away, home, -spread
            schedule.setdefault(projection_key(day_key, home, away, neutral),
                                Game(0, home, away, None, None, spread, neutral, **_published_tipoff(day_key, clock)))
    metadata = {}
    for start, home, away, neutral in replayed:
        day = start.astimezone(EASTERN).date()
        metadata[projection_key(day, home, away, neutral)] = replayed[start, home, away, neutral]
    return [replace(game, conference_game=source.conference_game, postseason=source.postseason)
            if (source := metadata.get(key)) else game for key, game in schedule.items()]


def _season_archive(site, model, year, latest_year, as_of, database, routes):
    root = site / f"seasons/{year}"
    current = _published_standings(root / "index.html")
    if not current:
        raise ValueError(f"No published standings for {year}")
    checkpoints = [c for c in model["rankings"] if datetime.fromisoformat(c["cutoff_utc"]) <= as_of]
    names = {r["team_id"]: r["school"] for c in checkpoints for r in c["rows"]}
    weeks = {c["cutoff_utc"]: c["week"] for c in checkpoints}
    rankings = {f'W{c["week"]}': _standings(c) for c in checkpoints}
    games = {}
    for row in model["forecasts"]:
        if row["home_team_id"] not in names or row["away_team_id"] not in names:
            continue
        game = conference_game(row, weeks.get(row["cutoff_utc"], 0), names)
        games[_fixture_key(row["start_utc"], game.home, game.away, game.neutral_site)] = game

    if year != latest_year:
        cutoff = model["final_cutoff_utc"]
        rankings["FINAL"] = rankings[f"W{weeks[cutoff]}"]
        if current != rankings["FINAL"]:
            raise ValueError(f"{year} replay does not match published FINAL")
        return Season(year, rankings, [game for game in games.values() if game.home_score is not None], cutoff)

    doc = html.parse(str(root / "index.html"))
    (published_cutoff,) = doc.xpath('//*[@id="season-notes"]//time/@datetime')
    if datetime.fromisoformat(published_cutoff) > as_of:
        raise ValueError("Retained ranking cutoff is after the rebuild timestamp")
    if (datetime.fromisoformat(checkpoints[-1]["cutoff_utc"]) == datetime.fromisoformat(published_cutoff)
            and current == next(reversed(rankings.values()))):
        _, _, projections = schedule_views(database, model, checkpoints[-1], as_of)
        if not projections:
            projections = conference_slate(model["forecasts"], checkpoints[-1], names)
        visible = [game for key, game in games.items() if game.home_score is not None and _available(key[0]) in weeks]
        return Season(year, rankings, visible + projections, published_cutoff)

    # Retain the issued checkpoint when the private cache predates it. A
    # replayed game has a usable delta only when its entire update checkpoint
    # matches the published standings.
    matching = {
        c["cutoff_utc"] for c in checkpoints
        if (path := root / f'weeks/{c["week"]}.html').exists()
        and rankings[f'W{c["week"]}'] == _published_standings(path)
    }
    for key, game in games.items():
        if _available(key[0]) not in matching:
            games[key] = replace(game, home_cors_delta=None, away_cors_delta=None)
    weekly = sorted((p for p in (root / "weeks").glob("*.html") if p.stem.isdecimal()),
                    key=lambda p: int(p.stem))
    rankings = {f"W{p.stem}": _published_standings(p) for p in weekly}
    if current != next(reversed(rankings.values())):
        raise ValueError(f"{year} latest retained standings disagree")
    results = _retained_results(site, year, datetime.fromisoformat(published_cutoff), current, games)
    schedule = _retained_schedule(site, year, current, routes, weekly[-1], games)
    return Season(year, rankings, results + schedule, published_cutoff, conference_note=(
        "Best wins and worst losses are selected from games with a verifiable CORS update. "
        "Scores reflect the ranking checkpoint. The retained schedule supplies projections for this edition."
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--historical-dir", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True, help="Existing site root with archived basketball pages")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    if args.as_of.utcoffset() is None:
        parser.error("--as-of requires a timezone")
    site = args.site / "cbb"
    modern = cors_v1_seasons(args.database, replay_seasons(args.database, args.season))
    historical = replay_historical_seasons(args.historical_dir)
    seasons = [*historical, *modern]
    routes = {r["school"]: f'/cbb/teams/{r["team_id"]}/index.html' for season in seasons
              for c in season["rankings"] for r in c["rows"]}
    archives = [_season_archive(site, season, season["season"], args.season, args.as_of, args.database, routes)
                for season in seasons]
    logos = {path.stem for path in (site / "assets/team-logos").glob("*.png")}
    marks = {name: f"/cbb/assets/team-logos/{team}.png" for name, route in routes.items()
             if (team := route.split("/")[-2]) in logos}
    presentation = PagePresentation(routes, marks, header=masthead(SECTIONS))
    for path, markup in build_conference_pages(
        archives, routes, sport="basketball", year_label=season_name,
        season_route=lambda year: f"/cbb/seasons/{year}/index.html",
    ):
        target = args.output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(presentation.render(markup.encode(), archive=False, sortable="data-sortable" in markup))


if __name__ == "__main__":
    main()
