"""Read published CORS standings and game results for the static record book."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import unicodedata

from lxml import html

from .historical_rebuild import _school
from .performance import _saved_margin
from .reference_markup import source_bytes
from .week_calendar import EASTERN, WEEK_ONE_BOUNDARIES


@dataclass(frozen=True, slots=True)
class Standing:
    school: str
    rank: int
    rating: Decimal
    record: str
    conference: str


@dataclass(frozen=True, slots=True)
class Game:
    week: int
    home: str
    away: str
    home_score: int | None
    away_score: int | None
    home_handicap: Decimal | None
    neutral_site: bool = False
    tipoff_label: str = ""
    home_division: str = ""
    away_division: str = ""
    conference_game: bool | None = None
    postseason: bool = False
    home_cors_delta: float | None = None
    away_cors_delta: float | None = None
    start_utc: str = ""


@dataclass(slots=True)
class Season:
    year: int
    rankings: dict[str, list[Standing]]
    games: list[Game]
    updated_at: str = ""
    home_advantage: float = 3.0
    conference_note: str = ""


def program_routes(seasons: list[Season]) -> dict[str, str]:
    """Routes depend only on a program's canonical name, never its rank or era."""
    names = {row.school for season in seasons for rows in season.rankings.values() for row in rows}
    routes = {}
    used = {}
    for name in sorted(names):
        slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug.replace("'", "").replace("&", " and ")).strip("-")
        if not slug or slug in used:
            raise ValueError(f"program URL requires an explicit identity for {name!r} (conflicts with {used.get(slug)!r})")
        used[slug] = name
        routes[name] = f"/cfb/teams/{slug}/index.html"
    return routes


def program_season_route(route: str, year: int) -> str:
    return f"{route.rsplit('/', 1)[0]}/{year}.html"


def season_team_routes(season: Season, routes: dict[str, str]) -> dict[str, str]:
    """Every team ranked at any checkpoint gets a review for this season."""
    ranked = {row.school for rows in season.rankings.values() for row in rows}
    return {
        school: program_season_route(route, season.year)
        for school, route in routes.items() if school in ranked
    }


def _rows(path: Path):
    raw = source_bytes(path.read_bytes())
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    table = html.fromstring(text).xpath("(//table)[1]//tr")
    if not table:
        raise ValueError(f"missing archive table in {path}")
    headers = [cell.text_content().strip() for cell in table[0]]
    for row in table[1:]:
        cells = [cell.text_content().strip() for cell in row if cell.tag in ("td", "th")]
        yield dict(zip(headers, cells, strict=True))


def _checkpoint_order(key: str) -> tuple[int, int]:
    if key == "PRESEASON":
        return 0, 0
    if key == "FINAL":
        return 2, 0
    return 1, int(key[1:])


def _standings(directory: Path, year: int) -> dict[str, list[Standing]]:
    paths = {path.name.split("_")[1]: path for path in directory.glob(f"{year}_*_FBS_cors.html")}
    rankings = {}
    for key in sorted(paths, key=_checkpoint_order):
        rows = [Standing(_school(row["school"]), int(row["rank"]), Decimal(row["cors"]),
                         row["record"], row["conference"]) for row in _rows(paths[key])]
        if len({row.school for row in rows}) != len(rows) or any(
            row.rank < 1 or not row.rating.is_finite() for row in rows
        ):
            raise ValueError(f"invalid standings in {paths[key]}")
        if key == "FINAL" and not rows:
            raise ValueError(f"empty FINAL standings in {paths[key]}")
        if rows:
            rankings[key] = rows
    return rankings


def final_history_rows(site: Path, year: int) -> tuple[dict[str, str], dict[str, str]]:
    """Return the first and last published FINAL ranks with their archived values."""
    path = site / f"cfb/years/{year}/rankings/{year}_FINAL_FBS_cors.html"
    rows = sorted(_rows(path), key=lambda row: int(row["rank"]))
    if not rows or [int(row["rank"]) for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError(f"missing or ambiguous FINAL ranks in {path}")
    return rows[0], rows[-1]


def _score(value) -> int | None:
    if value in ("", None, "nan", "None"):
        return None
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number != int(number):
        raise ValueError(f"invalid game score {value!r}")
    return int(number)


def _key(week, home, away, home_score, away_score, neutral):
    return (int(week), _school(home), _school(away), _score(home_score), _score(away_score), neutral)


def _matchup(game: Game):
    return game.week, game.home, game.away, game.neutral_site


def _football_start_utc(value: str | None) -> str:
    if not value:
        return ""
    start = datetime.fromisoformat(value)
    if start.tzinfo is None:
        # Date-only records refer to the local game day. Archived kickoff
        # timestamps, when present, use UTC.
        start = start.replace(tzinfo=EASTERN if len(value) == 10 else timezone.utc)
    return start.astimezone(timezone.utc).isoformat()


def _postseason(year: int, week: int, game_date: str, season_type: str = "") -> bool:
    if season_type:
        return season_type.lower() == "postseason"
    # Since 2024 the first Monday of the canonical championship week is
    # the Monday immediately after Thanksgiving weekend.
    november_end = date(year, 11, 30)
    thanksgiving = november_end - timedelta(days=(november_end.weekday() - 3) % 7)
    championship_week = thanksgiving + timedelta(days=4)
    if year in WEEK_ONE_BOUNDARIES:
        return week >= 1 + (championship_week - WEEK_ONE_BOUNDARIES[year].date()).days // 7
    if not game_date:
        return False
    start = datetime.fromisoformat(game_date)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    # The delayed 2020 championship weekend was December 18–19.
    cutoff = date(2020, 12, 18) if year == 2020 else championship_week
    if year < 1992:
        cutoff = date(year, 12, 15)
    return start.astimezone(EASTERN).date() >= cutoff


def _games(site: Path, directory: Path, year: int, *, active: bool) -> list[Game]:
    # The performance report is FBS-only. Start from the full schedule of
    # results so e.g. a ranked team's win against an unranked opponent survives.
    path = site / f"cfb/performance/{year}/report.json"
    report = json.loads(path.read_bytes()) if path.is_file() else {}
    forecasts = defaultdict(deque)
    for row in report.get("games", []):
        key = _key(row["week"], row["home"], row["away"], row.get("home_score"),
                   row.get("away_score"), row["neutral_site"])
        forecasts[key].append(row)

    games = []
    results = directory / f"data/results/{year}_FBS_results.html"
    for row in _rows(results) if results.is_file() else ():
        key = _key(row["week"], row["home_team"], row["away_team"], row.get("home_score"),
                   row.get("away_score"), row["neutral_site"].lower() == "true")
        forecast = forecasts[key].popleft() if forecasts.get(key) else {}
        handicap = forecast.get("home_handicap")
        games.append(Game(
            *key[:5], Decimal(str(handicap)) if handicap is not None else None, key[5],
            home_division=row.get("home_division", ""), away_division=row.get("away_division", ""),
            postseason=_postseason(year, key[0], row.get("game_date", ""), row.get("season_type", "")),
            start_utc=_football_start_utc(row.get("game_date") or forecast.get("game_date")),
        ))
    # A newly retained weekly report can precede the aggregate results page.
    for key, rows in forecasts.items():
        for row in rows:
            handicap = row.get("home_handicap")
            games.append(Game(*key[:5], Decimal(str(handicap)) if handicap is not None else None,
                              key[5], postseason=_postseason(year, key[0], ""),
                              start_utc=_football_start_utc(row.get("game_date"))))
    for row in report.get("upcoming", []):
        games.append(Game(int(row["week"]), _school(row["home"]), _school(row["away"]), None, None,
                          Decimal(str(row["home_handicap"])), row["neutral_site"],
                          postseason=_postseason(year, int(row["week"]), row.get("game_date", "")),
                          start_utc=_football_start_utc(row.get("game_date"))))
    if active:
        known = defaultdict(deque)
        for index, game in enumerate(games):
            known[_matchup(game)].append(index)
        for path in sorted((directory / "data/slate/weekly_slate").glob(f"{year}_W*_FBS_slate.html")):
            for row in _rows(path):
                game = Game(
                    int(row["week"]), _school(row["home_team"]), _school(row["away_team"]),
                    None, None, None, row["neutral_site"].lower() == "true",
                    home_division=row.get("home_division", ""), away_division=row.get("away_division", ""),
                    postseason=_postseason(year, int(row["week"]), row.get("game_date", ""),
                                           row.get("season_type", "")),
                    start_utc=_football_start_utc(row.get("game_date")),
                )
                key = _matchup(game)
                if known[key]:
                    index = known[key].popleft()
                    if game.start_utc and not games[index].start_utc:
                        games[index] = replace(games[index], start_utc=game.start_utc)
                else:
                    games.append(game)

    # A retrospective report can include a fresh estimate even where the
    # original spread page survives. The retained forecast takes precedence.
    saved = {}
    counts = Counter(map(_matchup, games))
    for path in (directory / "spread").glob(f"{year}_W*_FBS_spread.html"):
        for row in _rows(path):
            key = (int(row["week"]), _school(row["home_team"]), _school(row["away_team"]),
                   row["neutral_site"].lower() == "true")
            if counts[key] == 1:
                margin, _ = _saved_margin(row, row["home_team"])
                if key in saved and saved[key] != -margin:
                    raise ValueError(f"ambiguous published forecast in {path} for {key}")
                saved[key] = -margin
    return [replace(game, home_handicap=saved[key]) if (key := _matchup(game)) in saved else game
            for game in games]


def read_archive(site: Path) -> list[Season]:
    seasons = []
    for directory in sorted((site / "cfb/years").iterdir()):
        if not directory.name.isdecimal() or not directory.is_dir():
            continue
        year = int(directory.name)
        standings = _standings(directory / "rankings", year)
        if standings:
            games = _games(site, directory, year, active="FINAL" not in standings)
            # ADR-0024 retains the old W0 URL as a preseason alias in rebuilt
            # historical seasons that had no actual Week 0 game.
            if (year < 2024 and "W0" in standings and standings["W0"] == standings.get("PRESEASON")
                    and not any(game.week == 0 for game in games)):
                del standings["W0"]
            last = next(reversed(standings))
            raw = (directory / f"rankings/{year}_{last}_FBS_cors.html").read_bytes()
            timestamp = re.search(rb"Last updated:\s*([0-9][0-9T:.+ Z-]*)", raw)
            updated = timestamp[1].decode("ascii").strip() if timestamp else ""
            home_advantage = 3.0
            if "FINAL" not in standings:
                for path in sorted((directory / "spread").glob(f"{year}_W*_FBS_spread.html"),
                                   key=lambda path: _checkpoint_order(path.name.split("_")[1]), reverse=True):
                    match = re.search(rb"league HFA ([-+0-9.]+) pts", path.read_bytes())
                    if match:
                        home_advantage = float(match[1])
                        break
            seasons.append(Season(year, standings, games, updated, home_advantage))
    return seasons
