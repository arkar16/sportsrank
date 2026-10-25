"""Read private CFBD Starter Pack inputs and construct chronological features."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from io import StringIO
import math
from pathlib import Path

METRICS = ("offense_ppa", "defense_ppa", "offense_successRate", "defense_successRate")
FEATURES = tuple(f"{name}_difference" for name in METRICS) + ("home_field",)
MIN_HISTORY = 2
AVAILABILITY_HOURS = 24


def _table(path: Path, columns: tuple[str, ...], root: Path, sources: list) -> list[dict]:
    raw = path.read_bytes()
    reader = csv.DictReader(StringIO(raw.decode("utf-8-sig")))
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or not set(columns).issubset(headers):
        raise ValueError(f"missing or duplicate columns in {path.name}")
    rows = []
    for row in reader:
        if None in row or any(row[name] is None for name in columns):
            raise ValueError(f"incomplete CSV row in {path.name}")
        rows.append({name: row[name].strip() for name in columns})
    sources.append({"source": "starter_pack", "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(raw).hexdigest()})
    return rows


def _integer(value: str, field: str) -> int:
    try:
        number = float(value)
        if not math.isfinite(number) or number < 0 or not number.is_integer():
            raise ValueError
        return int(number)
    except ValueError:
        raise ValueError(f"invalid {field}: expected a nonnegative integer") from None


def _boolean(value: str, field: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise ValueError(f"invalid {field}: expected true/false")
    return value.lower() == "true"


def _kickoff(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # The bundle exports CFBD UTC timestamps without their timezone suffix.
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc))


def load_features(root: Path, *, through_season: int | None = None) -> dict:
    """Return one row per eligible game using prior FBS game means per team.

    Monday 00:00 UTC freezes the week's features. A historical game is usable
    only after its kickoff plus 24 hours. This is an explicit availability proxy,
    not a claim that retrospective metrics were actually retrieved at that time.
    """
    sources, coverage, features = [], [], []
    files = sorted((root / "data/advanced_game_stats").glob("*.csv"))
    files = [p for p in files if p.stem.isdecimal()
             and (through_season is None or int(p.stem) <= through_season)]
    if not files:
        raise ValueError("no advanced_game_stats season CSVs in starter pack")
    selected_seasons = {int(p.stem) for p in files}
    metadata = _table(root / "data/games.csv", (
        "id", "season", "start_date", "start_time_tbd", "status", "neutral_site",
        "home_team", "away_team", "home_classification", "away_classification",
        "home_points", "away_points"), root, sources)
    by_season = defaultdict(dict)
    all_ids = set()
    for row in metadata:
        identifier = row["id"]
        if not identifier or identifier in all_ids:
            raise ValueError("missing or duplicate starter-pack game id")
        all_ids.add(identifier)
        season = _integer(row["season"], "season")
        if season in selected_seasons:
            by_season[season][identifier] = row
    for path in files:
        season = int(path.stem)
        metadata_by_id = by_season[season]
        counts = Counter()
        games = {}
        for identifier, row in metadata_by_id.items():
            if row["home_classification"].lower() != "fbs" or row["away_classification"].lower() != "fbs":
                counts["non_fbs_games"] += 1
                continue
            counts["fbs_games"] += 1
            if row["status"] != "completed":
                counts["not_completed"] += 1
                continue
            if not row["home_points"] or not row["away_points"]:
                counts["missing_scores"] += 1
                continue
            if not row["start_date"] or (row["start_time_tbd"] and _boolean(row["start_time_tbd"], "start_time_tbd")):
                counts["unknown_kickoff"] += 1
                continue
            if not row["start_time_tbd"]:
                # Older exports omit this flag; their recorded date still
                # supports the weekly cutoff, with the same availability proxy.
                counts["unspecified_start_time_tbd"] += 1
            if not row["home_team"] or not row["away_team"] or row["home_team"] == row["away_team"]:
                raise ValueError("invalid starter-pack game teams")
            home_score = _integer(row["home_points"], "home_points")
            away_score = _integer(row["away_points"], "away_points")
            games[identifier] = {"game_id": identifier, "season": season,
                                 "home": row["home_team"], "away": row["away_team"],
                                 "neutral_site": _boolean(row["neutral_site"], "neutral_site"),
                                 "kickoff": _kickoff(row["start_date"]),
                                 "home_score": home_score, "away_score": away_score,
                                 "actual_home_margin": float(home_score - away_score)}
        stats = _table(path, ("gameId", "season", "team", "opponent", *METRICS), root, sources)
        observations, seen = {}, set()
        for row in stats:
            identifier, team = row["gameId"], row["team"]
            if (identifier, team) in seen:
                raise ValueError("duplicate starter-pack game/team stats")
            seen.add((identifier, team))
            if _integer(row["season"], "stats season") != season:
                raise ValueError("stats season disagrees with file")
            if identifier not in metadata_by_id:
                raise ValueError("stats game id has no same-season game")
            game = games.get(identifier)
            if game is None:
                continue
            if (team, row["opponent"]) not in {(game["home"], game["away"]), (game["away"], game["home"])}:
                raise ValueError("stats teams disagree with game identity")
            if any(not row[name] for name in METRICS):
                counts["missing_metric_rows"] += 1
                continue
            values = [float(row[name]) for name in METRICS]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("non-finite starter-pack metric")
            if any(not 0 <= value <= 1 for value in values[2:]):
                raise ValueError("success rate outside [0, 1]")
            observations[(identifier, team)] = values
        ordered = sorted(games.values(), key=lambda game: (game["kickoff"], game["game_id"]))
        matchup_counts = Counter((game["home"], game["away"], game["neutral_site"]) for game in ordered)
        history = defaultdict(lambda: {"count": 0, "sums": [0.0] * len(METRICS), "last": None})
        position = 0
        for game in ordered:
            kickoff = game["kickoff"]
            cutoff = (kickoff - timedelta(days=kickoff.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            while position < len(ordered) and ordered[position]["kickoff"] + timedelta(hours=AVAILABILITY_HOURS) < cutoff:
                prior = ordered[position]
                for team in (prior["home"], prior["away"]):
                    values = observations.get((prior["game_id"], team))
                    if values is None:
                        counts["unusable_history_team_games"] += 1
                        continue
                    aggregate = history[team]
                    aggregate["count"] += 1
                    aggregate["sums"] = [a + b for a, b in zip(aggregate["sums"], values)]
                    aggregate["last"] = prior["kickoff"].isoformat()
                position += 1
            home, away = history[game["home"]], history[game["away"]]
            if min(home["count"], away["count"]) < MIN_HISTORY:
                counts["insufficient_prior_games"] += 1
                continue
            differences = [h / home["count"] - a / away["count"] for h, a in zip(home["sums"], away["sums"])]
            features.append({**game, "kickoff": kickoff.isoformat(), "feature_cutoff": cutoff.isoformat(),
                             "season_matchup_count": matchup_counts[(game["home"], game["away"], game["neutral_site"])],
                             "home_history_games": home["count"], "away_history_games": away["count"],
                             "home_last_history_kickoff": home["last"], "away_last_history_kickoff": away["last"],
                             **dict(zip(FEATURES, [*differences, float(not game["neutral_site"])]))})
            counts["feature_games"] += 1
        counts["completed_games"] = len(games)
        counts["usable_stat_team_games"] = len(observations)
        coverage.append({"season": season, **dict(sorted(counts.items()))})
    return {"games": features, "sources": sources, "coverage": coverage}
