"""Offline selected-provider observations and CORS market comparison.

Raw CFBD responses remain private. Only the selected spread and the facts needed
to identify and reproduce a comparison become public derived observations.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from .week_calendar import EASTERN, WEEK_ONE_BOUNDARIES

SCHEMA = "cors-market-lines/v1"
PUBLIC_PATH = "cfb/performance/market-lines.json"
POLICY = "teamrankings-2013-2020-bovada-2021/v1"
FIELDS = {"season", "game_id", "phase", "provider_week", "start", "home", "away",
          "provider", "home_handicap", "retrieved_at", "response_sha256"}
GAME_COLUMNS = ("provider", "market_home_handicap", "market_pick", "market_ats",
                "market_absolute_error", "market_status")
SUMMARY_COLUMNS = ("market_games", "market_wins", "market_losses", "market_pushes",
                   "market_no_picks", "market_ats_percentage", "paired_cors_mae",
                   "market_mae", "paired_cors_rmse", "market_rmse", "missing_market_lines")


def provider_for(season: int) -> str | None:
    return None if season < 2013 else "teamrankings" if season <= 2020 else "Bovada"


def _number(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a spread")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("invalid spread") from error
    if not result.is_finite():
        raise ValueError("non-finite spread")
    return result


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("market timestamp must be text")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("market timestamp requires an explicit timezone")
    return result


def _day(value: str) -> date:
    return _timestamp(value).astimezone(EASTERN).date()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _field(row: dict, name: str):
    camel = re.sub(r"_([a-z])", lambda match: match[1].upper(), name)
    if name in row and camel in row and row[name] != row[camel]:
        raise ValueError(f"conflicting market field {name}")
    return row[name] if name in row else row.get(camel)


class MarketLines:
    def __init__(self, document: dict):
        if (not isinstance(document, dict) or set(document) != {"schema", "policy", "observations"}
                or document["schema"] != SCHEMA or document["policy"] != POLICY
                or not isinstance(document["observations"], list)):
            raise ValueError("invalid market observation document")
        seen = set()
        for row in document["observations"]:
            if not isinstance(row, dict) or set(row) != FIELDS:
                raise ValueError("invalid market observation fields")
            season, week = row["season"], row["provider_week"]
            if type(season) is not int or not 1869 <= season <= 9999 or type(week) is not int or week < 0:
                raise ValueError("invalid market season/week")
            if row["provider"] != provider_for(season) or row["provider"] is None:
                raise ValueError("market provider differs from selected season policy")
            for field in ("game_id", "home", "away"):
                if not isinstance(row[field], str) or not row[field].strip() or row[field] != row[field].strip():
                    raise ValueError("invalid market game identity")
            if row["home"] == row["away"] or row["phase"] not in {"regular", "postseason"}:
                raise ValueError("invalid market participants/phase")
            _timestamp(row["start"])
            _timestamp(row["retrieved_at"])
            if not isinstance(row["response_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", row["response_sha256"]):
                raise ValueError("invalid market response digest")
            if row["home_handicap"] is not None:
                if not isinstance(row["home_handicap"], str):
                    raise ValueError("public spread must preserve decimal text")
                _number(row["home_handicap"])
            key = season, row["game_id"]
            if key in seen:
                raise ValueError("duplicate market game identity")
            seen.add(key)
        # Retain canonical bytes, not a caller-mutable reference.
        self._raw = _json({**document, "observations": sorted(document["observations"],
                           key=lambda row: (row["season"], row["game_id"]))})

    @property
    def observations(self) -> list[dict]:
        return json.loads(self._raw)["observations"]

    def to_bytes(self) -> bytes:
        return self._raw

    @classmethod
    def from_site(cls, website: Path) -> MarketLines | None:
        path = website / PUBLIC_PATH
        return cls(json.loads(path.read_bytes())) if path.exists() else None

    def index(self, website: Path) -> dict[tuple, dict | None]:
        """Retain exact game IDs and flag ambiguous calendar-only joins."""
        boundaries = {year: value.date() for year, value in WEEK_ONE_BOUNDARIES.items()}
        corrected = set()
        review = website / "historical-review.json"
        if review.exists():
            document = json.loads(review.read_bytes())
            for entry in document["seasons"]:
                year = entry["season"]
                boundary = date.fromisoformat(entry["week_one_boundary"])
                if year in boundaries and boundaries[year] != boundary:
                    raise ValueError("historical and modern market calendars disagree")
                boundaries[year] = boundary
                corrected.add(year)
        # Retained API regular weeks supply a bounded older-season date lattice.
        # A tied origin is not permission to attach a postseason line arbitrarily.
        origins: dict[int, Counter] = {}
        for row in self.observations:
            if row["phase"] == "regular" and row["provider_week"] >= 1:
                day = _day(row["start"])
                origin = day - timedelta(days=day.weekday(), weeks=row["provider_week"] - 1)
                origins.setdefault(row["season"], Counter())[origin] += 1
        for year, counts in origins.items():
            if year not in boundaries:
                ordered = counts.most_common()
                if len(ordered) == 1 or ordered[0][1] > ordered[1][1]:
                    boundaries[year] = ordered[0][0]
        result = {}
        for row in self.observations:
            year, week = row["season"], row["provider_week"]
            boundary = boundaries.get(year)
            if row["phase"] == "postseason":
                if boundary is None:
                    raise ValueError(f"postseason market calendar unavailable for {year}")
                week = 1 + (_day(row["start"]) - boundary).days // 7
                if week < 1:
                    raise ValueError("postseason market game precedes season boundary")
            elif week == 1 and boundary is not None and (year >= 2024 or year in corrected):
                if _day(row["start"]) < boundary:
                    week = 0
            key = year, week, row["home"], row["away"]
            result[(*key, row["game_id"])] = row
            result[key] = None if key in result else row
        return result


def load_inputs(manifest_path: Path) -> MarketLines:
    """Read hash-pinned local raw responses; never fetch or copy their envelopes."""
    manifest = json.loads(manifest_path.read_bytes())
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "responses"}
            or manifest["schema"] != "market-inputs/v1" or not isinstance(manifest["responses"], list)):
        raise ValueError("invalid private market input manifest")
    observations = []
    for source in manifest["responses"]:
        if not isinstance(source, dict) or set(source) != {"season", "path", "sha256", "retrieved_at"}:
            raise ValueError("invalid private market response reference")
        if type(source["season"]) is not int:
            raise ValueError("invalid market response season")
        _timestamp(source["retrieved_at"])
        raw = (manifest_path.parent / source["path"]).read_bytes()
        if _digest(raw) != source["sha256"]:
            raise ValueError("market response digest differs from retained input")
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError("market response must contain a game array")
        for row in rows:
            if not isinstance(row, dict) or row.get("season") != source["season"]:
                raise ValueError("market response season differs from requested season")
            provider = provider_for(row["season"])
            if provider is None or any(str(_field(row, side + "_classification")).lower() != "fbs"
                                       for side in ("home", "away")):
                continue
            if not isinstance(row.get("lines"), list):
                raise ValueError("market response lacks provider observations")
            selected = [line for line in row["lines"] if isinstance(line, dict) and line.get("provider") == provider]
            if len(selected) > 1:
                raise ValueError("duplicate selected-provider quote")
            spread = None
            if selected and selected[0].get("spread") is not None:
                try:
                    spread = str(_number(selected[0]["spread"]))
                except ValueError:
                    # The accepted policy treats missing/non-finite quotes as
                    # unavailable. Other malformed numeric shapes fail closed.
                    value = selected[0]["spread"]
                    try:
                        nonfinite = not Decimal(str(value)).is_finite()
                    except InvalidOperation:
                        nonfinite = False
                    if not nonfinite:
                        raise
            identifier = row.get("id")
            if type(identifier) is not int or identifier < 0:
                raise ValueError("invalid provider game ID")
            observations.append({"season": row["season"], "game_id": str(identifier),
                "phase": _field(row, "season_type"), "provider_week": row.get("week"),
                "start": _field(row, "start_date"), "home": _field(row, "home_team"),
                "away": _field(row, "away_team"), "provider": provider,
                "home_handicap": spread, "retrieved_at": source["retrieved_at"],
                "response_sha256": source["sha256"]})
    return MarketLines({"schema": SCHEMA, "policy": POLICY, "observations": observations})


def compare(game: dict, observation: dict | None, season: int) -> dict:
    """Use home handicaps throughout. No additional home-field adjustment."""
    result = dict.fromkeys(GAME_COLUMNS)
    result["provider"] = provider_for(season)
    result["market_status"] = "No provider selected" if result["provider"] is None else "Missing line"
    if observation is None or observation["home_handicap"] is None:
        return result
    market = _number(observation["home_handicap"])
    result.update(market_home_handicap=str(market), market_status="Missing prediction")
    if game.get("home_handicap") is None:
        return result
    cors = _number(game["home_handicap"])
    edge = market - cors
    pick = game["home"] if edge > 0 else game["away"] if edge < 0 else None
    result.update(market_pick=pick, market_status="Pending result", market_ats="No pick" if pick is None else None)
    if game.get("home_score") is None or game.get("away_score") is None:
        return result
    actual = _number(game["home_score"]) - _number(game["away_score"])
    result.update(market_status="Compared", market_absolute_error=str(abs(actual + market)))
    if pick is not None:
        adjusted = (actual + market) * (1 if edge > 0 else -1)
        result["market_ats"] = "Win" if adjusted > 0 else "Loss" if adjusted < 0 else "Push"
    return result


def summarize(games: list[dict]) -> dict:
    paired = [game for game in games if game.get("market_status") == "Compared"]
    record = Counter(game["market_ats"] for game in paired)
    wins, losses = record["Win"], record["Loss"]
    cors = [_number(game["absolute_error"]) for game in paired]
    market = [_number(game["market_absolute_error"]) for game in paired]
    def mean(values):
        return sum(values, Decimal(0)) / len(values) if values else None
    def rmse(values):
        square = mean([value * value for value in values])
        return square.sqrt() if square is not None else None
    def text(value):
        return str(value) if value is not None else None
    return {"market_games": len(paired), "market_wins": wins, "market_losses": losses,
            "market_pushes": record["Push"], "market_no_picks": record["No pick"],
            "market_ats_percentage": text(Decimal(wins) / (wins + losses) if wins + losses else None),
            "paired_cors_mae": text(mean(cors)), "market_mae": text(mean(market)),
            "paired_cors_rmse": text(rmse(cors)), "market_rmse": text(rmse(market)),
            "missing_market_lines": sum(game.get("market_status") == "Missing line" for game in games)}


def observation_for(index: dict, season: int, game: dict) -> dict | None:
    key = season, game["week"], game["home"], game["away"]
    identifier = game.get("game_id")
    if identifier and not identifier.startswith("archive:"):
        return index.get((*key, identifier))
    if key in index and index[key] is None:
        raise ValueError("ambiguous market matchup; game IDs are required to distinguish it")
    return index.get(key)


def enrich(report: dict, index: dict) -> None:
    for game in report["games"]:
        game.update(compare(game, observation_for(index, report["season"], game), report["season"]))
    report["market_policy"] = POLICY
    report["summary"].update(summarize(report["games"]))
    for week in report["weekly"]:
        week.update(summarize([game for game in report["games"] if game["week"] == week["week"]]))
