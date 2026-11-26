"""Pregame evidence for the football archive's reconstructed CORS v1 forecasts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import NamedTuple

from .excitement import SCORING_VERSION, QualifiedFinal, QualifiedPregame, TimelinePoint, calculate_bev, calculate_full_aev
from .excitement_reference import ReferenceArtifact, estimate_aev, load_reference_artifact
from .historical_rebuild import _school
from .performance import _identity, _saved_margin
from .reference_archive import _rows
from .reference_markup import source_bytes

DATA = Path(__file__).with_name("excitement_data")
FIRST_SEASON = 2024


class Matchup(NamedTuple):
    week: int
    home: str
    away: str
    neutral: bool


@dataclass(frozen=True)
class Forecast:
    checkpoint: str
    home_rank: int
    away_rank: int
    home_margin: str
    forecast_sha256: str
    ranking_sha256: str

    def pregame(self) -> QualifiedPregame:
        return QualifiedPregame(self.home_rank, self.away_rank, float(self.home_margin),
                                self.forecast_sha256, self.ranking_sha256)

    def bev(self) -> str:
        return f"{calculate_bev(self.pregame()).value:.1f}"


def read_forecasts(directory: Path, year: int) -> dict[Matchup, Forecast]:
    """Join the retained spread to the preceding checkpoint, independent of results."""
    forecasts = {}
    for path in sorted((directory / "spread").glob(f"{year}_W*_FBS_spread.html")):
        raw = source_bytes(path.read_bytes())
        if b"CORS v1.0.0 spreads" not in raw:
            continue
        week = int(path.name.split("_")[1][1:])
        checkpoint = f"W{week - 1}" if week else "PRESEASON"
        ranking = directory / "rankings" / f"{year}_{checkpoint}_FBS_cors.html"
        if not ranking.is_file():
            continue
        ranking_bytes = source_bytes(ranking.read_bytes())
        if f"{year} {checkpoint} CORS v1.0.0 rankings".encode() not in ranking_bytes:
            continue
        standings = {_school(row["school"]): (int(row["rank"]), Decimal(row["cors"])) for row in _rows(ranking)}
        ranking_id = sha256(ranking_bytes).hexdigest()
        forecast_id = sha256(raw).hexdigest()
        for row in _rows(path):
            home, away, neutral = _identity(row, week)
            margin, _ = _saved_margin(row, home)
            key = Matchup(week, _school(home), _school(away), neutral)
            if key in forecasts:
                raise ValueError(f"ambiguous excitement forecast for {year} {key}")
            if key.home in standings and key.away in standings:
                hr, hc = standings[key.home]
                ar, ac = standings[key.away]
                if (hc, ac) != (Decimal(row["home_cors"]), Decimal(row["away_cors"])):
                    raise ValueError(f"spread and pregame ratings disagree for {year} {key}")
                forecasts[key] = Forecast(checkpoint, hr, ar, str(margin),
                                          forecast_id, ranking_id)
    return forecasts


def frozen_forecasts(path: Path, year: int, current: dict[Matchup, Forecast]):
    """Preserve the first captured inputs and score; reject silent corrections."""
    saved = {}
    if path.exists():
        ledger = json.loads(path.read_bytes())
        if (ledger["scoring_version"], ledger["season"], ledger["model"]) != (
            SCORING_VERSION, year, "CORS v1.0.0"
        ):
            raise ValueError(f"incompatible BEV record in {path}")
        for row in ledger["games"]:
            key = Matchup(*row["matchup"])
            forecast = Forecast(**row["forecast"])
            if key in saved or forecast.bev() != row["bev"]:
                raise ValueError(f"invalid frozen BEV for {year} {key}")
            saved[key] = forecast

    for key, forecast in current.items():
        existing = saved.setdefault(key, forecast)
        # Formatting and timestamps can change in a regenerated source page.
        # If the *inputs* change, the original needs an explicit correction.
        if (existing.checkpoint, existing.home_rank, existing.away_rank, float(existing.home_margin)) != (
            forecast.checkpoint, forecast.home_rank, forecast.away_rank, float(forecast.home_margin)
        ):
            raise ValueError(f"pregame inputs differ from the frozen BEV for {year} {key}")

    payload = {"scoring_version": SCORING_VERSION, "season": year, "model": "CORS v1.0.0",
               "basis": "pregame rating checkpoint", "market": "no qualified excitement line",
               "games": [{"matchup": list(key), "forecast": asdict(record), "bev": record.bev()}
                         for key, record in sorted(saved.items())]}
    return {key: saved[key] for key in current}, payload


def flow_inputs(row: dict, year: int, source: str):
    """Adapt a retained season's normalized scoring changes to scoring v1."""
    final = QualifiedFinal(row["game_id"], year, *row["final"], source,
                           overtime=row["overtime"], quarter_scores=tuple(map(tuple, row["quarters"])) if row["quarters"] else None)
    points = tuple(TimelinePoint(home, away, sequence,
                                 elapsed_minute=None if seconds is None else seconds / 60,
                                 overtime=seconds is None, period=period)
                   for sequence, (seconds, home, away, period) in enumerate(row.get("states", ())))
    return final, points


@dataclass(frozen=True)
class GameAppeal:
    bev: str
    final: tuple[int, int] | None
    aev: float | None = None
    tier: str | None = None
    source_game: str | None = None
    support: int | None = None
    reference_distance: float | None = None

    @property
    def estimated(self) -> bool:
        return self.tier in ("quarter", "halftime", "final") and self.aev is not None


def season_scores(directory: Path, season, reference):
    year = season.year
    ledger_path = directory / "excitement/bev-v1.json"
    forecasts, ledger = frozen_forecasts(ledger_path, year, read_forecasts(directory, year))
    if not forecasts:
        if ledger["games"]:
            raise ValueError(f"no matching sources for the retained {year} BEV records")
        return {}, ledger
    evidence = {}
    source = ""
    flow_path = DATA / f"{year}.json"
    if flow_path.is_file():
        data = json.loads(flow_path.read_bytes())
        if data["season"] != year or data["schema"] != "espn-scoring-summary/v1":
            raise ValueError(f"incompatible AEV evidence for {year}")
        source = data["source"]
        for row in data["games"]:
            key = Matchup(*row["matchup"])
            if key in evidence:
                raise ValueError(f"duplicate AEV evidence for {year} {key}")
            evidence[key] = row
    finals = {}
    for game in season.games:
        if game.home_score is not None and game.away_score is not None:
            key = Matchup(game.week, game.home, game.away, game.neutral_site)
            if key in finals and key in forecasts:
                raise ValueError(f"ambiguous CORS final for {year} {key}")
            finals[key] = (game.home_score, game.away_score)
    scores = {}
    for key, record in forecasts.items():
        final_score = finals.get(key)
        scores[key] = GameAppeal(record.bev(), final_score)
        row = evidence.get(key)
        if final_score is None or row is None:
            continue
        if tuple(row["final"]) != final_score:
            raise ValueError(f"AEV evidence disagrees with the CORS final for {year} {key}")
        final, points = flow_inputs(row, year, source)
        if points:
            result = calculate_full_aev(record.pregame(), final, points)
        elif reference and ("quarter" if final.quarter_scores and final.home_score != final.away_score else "final") in reference.tiers:
            result = estimate_aev(record.pregame(), final, reference.artifact,
                                  qualification_reasons=(row["reduced_because"],))
        else:
            continue
        scores[key] = GameAppeal(record.bev(), final_score, result.value, result.evidence.evidence_tier,
                                 final.game_id, result.evidence.reference_support)
    return scores, ledger


@dataclass(frozen=True)
class EstimatePolicy:
    artifact: ReferenceArtifact
    tiers: frozenset[str]


@lru_cache(maxsize=1)
def site_reference() -> EstimatePolicy | None:
    """Only a pinned, reviewed reference can supply estimated scores to a build."""
    # Activation requires sufficient whole-season evidence and owner acceptance.
    policy_path = DATA / "reference-policy.json"
    if not policy_path.is_file():
        return None
    policy = json.loads(policy_path.read_bytes())
    tiers = frozenset(policy["tiers"])
    if not tiers or not tiers <= {"quarter", "final"}:
        raise ValueError("unknown AEV reference tier")
    return EstimatePolicy(load_reference_artifact(DATA / policy["file"], expected_checksum=policy["checksum"]), tiers)
