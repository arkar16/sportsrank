"""Pregame evidence for the football archive's reconstructed CORS v1 forecasts."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from functools import cached_property, lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import NamedTuple

from .excitement import SCORING_VERSION, QualifiedFinal, QualifiedPregame, TimelinePoint, calculate_bev, calculate_full_aev, matchup_quality
from .excitement_reference import ReferenceArtifact, estimate_aev, load_reference_artifact
from .sparse_excitement import MarginPool, pregame_prior
from .historical_rebuild import _school
from .performance import _identity, _saved_margin
from .reference_archive import _rows
from .reference_markup import source_bytes

DATA = Path(__file__).with_name("excitement_data")


class Matchup(NamedTuple):
    week: int
    home: str
    away: str
    neutral: bool
    rematch: tuple[int, ...] = ()


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
            key = Matchup(*row["matchup"][:4])
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
               "games": [{"matchup": list(key[:4]), "forecast": asdict(record), "bev": record.bev()}
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
    bev_basis: str | None = None

    @property
    def estimated(self) -> bool:
        return self.tier in ("quarter", "final") and self.aev is not None


def score_for(scores, key, final=None):
    return scores.get(key) or (scores.get(key._replace(rematch=tuple(final))) if final else None)


def _prior(key, game, rankings, year):
    from .cors_v1 import FCS_INIT_MEAN, OTHER_INIT_MEAN, FCS_MIN_YEAR

    eligible = [(int(k[1:]), k) for k in rankings if k.startswith("W") and int(k[1:]) < key.week]
    checkpoint = max(eligible)[1] if eligible else "PRESEASON"
    rows = rankings.get(checkpoint, {})
    other = (FCS_INIT_MEAN if year >= FCS_MIN_YEAR and
             "fcs" in (game.home_division.lower(), game.away_division.lower()) else OTHER_INIT_MEAN)
    prior = pregame_prior(rows.get(key.home), rows.get(key.away), field_size=len(rows) or 100,
                          close=14, missing_rating=other)
    return prior, checkpoint


@lru_cache(maxsize=1)
def _tie_pool():
    raw = (Path(__file__).with_name("excitement_data") / "regulation-ties-v1.json").read_bytes()
    if sha256(raw).hexdigest() != "0297f169b0e0b15e1ea89c5707818278e5b0b9cf71ea1b85e128784846a3589e":
        raise ValueError("football regulation tie reference changed")
    return MarginPool(json.loads(raw))


def _sparse_final(reference, final, quality, margin, identity, overtime):
    home, away = final
    if home == away == 0:
        return 80 + 3 * quality, None
    if home == away:
        drama, support = _tie_pool().estimate(home, identity)
        return drama + 3 * quality, support
    drama, support = reference.final_pools[overtime].estimate(abs(home-away), identity)
    winner_margin = (margin if home > away else -margin) if margin is not None else 0
    return drama + 3 * quality + 3 * min(1, max(0, -winner_margin / 14)), support


def season_scores(directory: Path, season, reference):
    year = season.year
    ledger_path = directory / "excitement/bev-v1.json"
    forecasts, ledger = frozen_forecasts(ledger_path, year, read_forecasts(directory, year))
    if not forecasts and ledger["games"]:
        raise ValueError(f"no matching sources for the retained {year} BEV records")
    evidence = {}
    source = ""
    flow_path = DATA / f"{year}.json"
    if flow_path.is_file():
        data = json.loads(flow_path.read_bytes())
        if data["season"] != year or data["schema"] != "espn-scoring-summary/v1":
            raise ValueError(f"incompatible AEV evidence for {year}")
        source = data["source"]
        for row in data["games"]:
            key = Matchup(*row["matchup"][:4])
            if key in evidence:
                raise ValueError(f"duplicate AEV evidence for {year} {key}")
            evidence[key] = row
    by_key = Counter(Matchup(g.week, g.home, g.away, g.neutral_site) for g in season.games)
    games = {}
    for game in season.games:
        key = Matchup(game.week, game.home, game.away, game.neutral_site)
        if by_key[key] > 1:
            if key in forecasts or key in evidence:
                raise ValueError(f"ambiguous excitement source for {year} {key}")
            key = key._replace(rematch=(game.home_score, game.away_score))
        if key in games:
            raise ValueError(f"duplicate football result identity for {year} {key}")
        games[key] = game
    standings = {k: {r.school: (r.rank, r.rating) for r in rows} for k, rows in season.rankings.items()}
    scores = {}
    baselines = []
    for key in dict.fromkeys((*games, *forecasts)):
        game = games.get(key)
        final_score = ((game.home_score, game.away_score) if game and
                       game.home_score is not None and game.away_score is not None else None)
        record = forecasts.get(key)
        if record:
            bev, basis = record.bev(), None
            pre = record.pregame()
            quality = matchup_quality(pre.home_rank, pre.away_rank)
            margin = pre.cors_home_margin
        else:
            prior, cutoff = _prior(key, game, standings, year)
            bev, quality, margin = f"{prior.bev:.1f}", prior.quality, None
            basis = "Pregame baseline"
            baselines.append({"matchup": list(key[:4]), "checkpoint": cutoff, "bev": bev})
            pre = None
        score = GameAppeal(bev, final_score, bev_basis=basis)
        scores[key] = score
        if final_score is None:
            continue
        row = evidence.get(key)
        game_id = row["game_id"] if row else None
        if row:
            if tuple(row["final"]) != final_score:
                raise ValueError(f"AEV evidence disagrees with the CORS final for {year} {key}")
            final, points = flow_inputs(row, year, source)
            if points or (reference and final.quarter_scores and final.home_score != final.away_score
                          and "quarter" in reference.tiers):
                # The scoring core exposes its quality component. Replace it with the
                # expectation when the checkpoint has no qualified pair of ranks.
                inputs = pre or QualifiedPregame(1, 1, None, "baseline", "pregame")
                result = (calculate_full_aev(inputs, final, points) if points else
                          estimate_aev(inputs, final, reference.artifact,
                                       qualification_reasons=(row["reduced_because"],)))
                value = result.value if pre else result.value + 3 * (quality - result.components.quality)
                scores[key] = replace(score, aev=value, tier=result.evidence.evidence_tier,
                                      source_game=game_id, support=result.evidence.reference_support)
                continue
        if reference and "final" in reference.tiers:
            value, support = _sparse_final(reference, final_score, quality, margin, game_id,
                                           row["overtime"] if row else None)
            scores[key] = replace(score, aev=value, tier="final", source_game=game_id, support=support)
    if baselines:
        # One pregame score per matchup at its checkpoint, including same-week rematches.
        current = {tuple(r["matchup"]): r for r in baselines}
        if ledger_path.exists():
            old = json.loads(ledger_path.read_bytes()).get("pregame_baselines_v1", [])
            for r in old:
                key = tuple(r["matchup"])
                # A change to a recorded pregame input needs an explicit corrected version.
                if key in current and current[key] != r:
                    raise ValueError(f"changed frozen football pregame baseline for {year} {key}")
        ledger["pregame_baselines_v1"] = [current[k] for k in sorted(current)]
    return scores, ledger


@dataclass(frozen=True)
class EstimatePolicy:
    artifact: ReferenceArtifact
    tiers: frozenset[str]

    @cached_property
    def final_pools(self):
        return {
            ot: MarginPool((r.game_id, r.absolute_final_margin,
                            r.mixed_target if ot is None else r.drama_target + 2 * ot)
                           for r in self.artifact.games if ot is None or r.overtime == ot)
            for ot in (None, False, True)
        }


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
