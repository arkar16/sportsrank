"""Read published CORS standings and game results for the static record book."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, replace
from decimal import Decimal
import json
from pathlib import Path
import re
import unicodedata

from lxml import html

from .historical_rebuild import _school
from .performance import _saved_margin
from .reference_markup import source_bytes


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


@dataclass(slots=True)
class Season:
    year: int
    rankings: dict[str, list[Standing]]
    games: list[Game]
    updated_at: str = ""


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


def _games(site: Path, directory: Path, year: int, *, active: bool) -> list[Game]:
    # The performance report is FBS-only. Start from the full schedule of
    # results so e.g. a ranked team's win against an unranked opponent survives.
    path = site / f"cfb/performance/{year}/report.json"
    report = json.loads(path.read_bytes()) if path.is_file() else {}
    forecasts = defaultdict(deque)
    for row in report.get("games", []):
        key = _key(row["week"], row["home"], row["away"], row.get("home_score"),
                   row.get("away_score"), row["neutral_site"])
        forecasts[key].append(row.get("home_handicap"))

    games = []
    results = directory / f"data/results/{year}_FBS_results.html"
    for row in _rows(results) if results.is_file() else ():
        key = _key(row["week"], row["home_team"], row["away_team"], row.get("home_score"),
                   row.get("away_score"), row["neutral_site"].lower() == "true")
        handicap = forecasts[key].popleft() if forecasts.get(key) else None
        games.append(Game(*key[:5], Decimal(str(handicap)) if handicap is not None else None, key[5]))
    # A newly retained weekly report can precede the aggregate results page.
    for key, handicaps in forecasts.items():
        for handicap in handicaps:
            games.append(Game(*key[:5], Decimal(str(handicap)) if handicap is not None else None, key[5]))
    for row in report.get("upcoming", []):
        games.append(Game(int(row["week"]), _school(row["home"]), _school(row["away"]), None, None,
                          Decimal(str(row["home_handicap"])), row["neutral_site"]))
    if active:
        known = Counter(map(_matchup, games))
        for path in sorted((directory / "data/slate/weekly_slate").glob(f"{year}_W*_FBS_slate.html")):
            for row in _rows(path):
                game = Game(int(row["week"]), _school(row["home_team"]), _school(row["away_team"]),
                            None, None, None, row["neutral_site"].lower() == "true")
                key = _matchup(game)
                if known[key]:
                    known[key] -= 1
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
            seasons.append(Season(year, standings, games, updated))
    return seasons
