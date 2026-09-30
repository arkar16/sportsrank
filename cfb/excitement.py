"""Pure, versioned ADR-0021 BEV and full-data AEV calculations.

Inputs to this module are already-qualified domain values.  Source adapters own
provider parsing and eligibility; this boundary deliberately accepts only
small scalar provenance identifiers and never raw provider payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite
from typing import Iterable, Literal


SCORING_VERSION = "sportsrank-excitement-v1"


class ExcitementInputError(ValueError):
    """Raised when purportedly-qualified scoring evidence is invalid."""


class TimelineQualificationError(ExcitementInputError):
    """Raised when a timeline cannot support the full-data AEV calculation."""


def _finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExcitementInputError(f"{field} must be numeric")
    value = float(value)
    if not isfinite(value):
        raise ExcitementInputError(f"{field} must be finite")
    return value


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExcitementInputError(f"{field} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ExcitementInputError(f"{field} contains a control character")
    return value


def _clip(value: float) -> float:
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class QualifiedMarketReference:
    """A pregame-eligible market margin, oriented to the home team."""

    home_margin: float
    policy_id: str
    source_snapshot_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "home_margin", _finite(self.home_margin, "home_margin"))
        _identifier(self.policy_id, "policy_id")
        _identifier(self.source_snapshot_id, "source_snapshot_id")


@dataclass(frozen=True)
class QualifiedPregame:
    """Pregame evidence fixed to one team orientation (home minus away)."""

    home_rank: int
    away_rank: int
    cors_home_margin: float | None
    forecast_id: str
    source_snapshot_id: str
    market: QualifiedMarketReference | None = None

    def __post_init__(self) -> None:
        if isinstance(self.home_rank, bool) or not isinstance(self.home_rank, int) or self.home_rank < 1:
            raise ExcitementInputError("home_rank must be a positive integer")
        if isinstance(self.away_rank, bool) or not isinstance(self.away_rank, int) or self.away_rank < 1:
            raise ExcitementInputError("away_rank must be a positive integer")
        if self.cors_home_margin is not None:
            object.__setattr__(
                self, "cors_home_margin", _finite(self.cors_home_margin, "cors_home_margin")
            )
        _identifier(self.forecast_id, "forecast_id")
        _identifier(self.source_snapshot_id, "source_snapshot_id")


@dataclass(frozen=True)
class QualifiedFinal:
    """A verified ordinary completed game's final and limited evidence."""

    game_id: str
    season: int
    home_score: int
    away_score: int
    source_snapshot_id: str
    verified_completed: bool = True
    normal_format: bool = True
    overtime: bool | None = None
    quarter_scores: tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None = None

    def __post_init__(self) -> None:
        _identifier(self.game_id, "game_id")
        _identifier(self.source_snapshot_id, "source_snapshot_id")
        if isinstance(self.season, bool) or not isinstance(self.season, int) or self.season < 1869:
            raise ExcitementInputError("season is invalid")
        for field, value in (("home_score", self.home_score), ("away_score", self.away_score)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ExcitementInputError(f"{field} must be a non-negative integer")
        if self.verified_completed is not True:
            raise ExcitementInputError("final is not verified complete")
        if self.normal_format is not True:
            raise ExcitementInputError("unsupported game format")
        if self.overtime is not None and not isinstance(self.overtime, bool):
            raise ExcitementInputError("overtime must be true, false, or unknown")
        if self.overtime is True and self.home_score == self.away_score:
            raise ExcitementInputError("verified overtime final cannot remain tied")
        if self.quarter_scores is not None:
            if len(self.quarter_scores) != 3:
                raise ExcitementInputError("quarter_scores must contain Q1-Q3 cumulative scores")
            prior = (0, 0)
            for scores in self.quarter_scores:
                if len(scores) != 2 or any(
                    isinstance(score, bool) or not isinstance(score, int) or score < 0
                    for score in scores
                ):
                    raise ExcitementInputError("quarter scores must be non-negative integers")
                if scores[0] < prior[0] or scores[1] < prior[1]:
                    raise ExcitementInputError("cumulative quarter scores cannot decrease")
                if scores[0] > self.home_score or scores[1] > self.away_score:
                    raise ExcitementInputError("quarter score exceeds final score")
                prior = scores
            object.__setattr__(self, "quarter_scores", tuple(tuple(scores) for scores in self.quarter_scores))


@dataclass(frozen=True)
class TimelinePoint:
    """One oriented score state; overtime points are untimed and sequence ordered."""

    home_score: int
    away_score: int
    sequence: int
    elapsed_minute: float | None = None
    overtime: bool = False

    def __post_init__(self) -> None:
        for field, value in (("home_score", self.home_score), ("away_score", self.away_score)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TimelineQualificationError(f"{field} must be a non-negative integer")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise TimelineQualificationError("sequence must be a non-negative integer")
        if self.overtime:
            if self.elapsed_minute is not None:
                raise TimelineQualificationError("overtime points must not fabricate regulation time")
        elif self.elapsed_minute is None:
            raise TimelineQualificationError("regulation point requires elapsed_minute")
        else:
            minute = _finite(self.elapsed_minute, "elapsed_minute")
            if not 0 <= minute <= 60:
                raise TimelineQualificationError("elapsed_minute must be between 0 and 60")


@dataclass(frozen=True)
class NormalizedTimeline:
    regulation: tuple[TimelinePoint, ...]
    overtime: tuple[TimelinePoint, ...]
    observed_states: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class ScoreEvidence:
    scoring_version: str
    forecast_id: str
    pregame_snapshot_id: str
    market_basis: Literal["market", "missing"]
    market_policy_id: str | None
    market_snapshot_id: str | None
    surprise_basis: Literal["market", "cors", "missing"] | None = None
    final_snapshot_id: str | None = None
    evidence_tier: Literal["full", "quarter", "final"] | None = None
    reference_artifact_id: str | None = None
    reference_checksum: str | None = None
    reference_algorithm_version: str | None = None
    reference_pool: str | None = None
    reference_support: int | None = None
    reference_cutoff_distance: float | None = None
    reference_neighbor_ids: tuple[str, ...] = ()
    reference_scale: tuple[float, ...] = ()
    qualification_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class BevComponents:
    quality: float
    competitiveness: float
    market_boost: float


@dataclass(frozen=True)
class AevComponents:
    tension: float | None
    lead_changes: float | None
    comeback: float | None
    overtime: float | None
    quality: float
    surprise: float
    drama_points: float


@dataclass(frozen=True)
class ExcitementScore:
    value: float
    components: BevComponents | AevComponents
    evidence: ScoreEvidence
    estimated: bool = False


def matchup_quality(home_rank: int, away_rank: int) -> float:
    if (
        isinstance(home_rank, bool)
        or not isinstance(home_rank, int)
        or home_rank < 1
        or isinstance(away_rank, bool)
        or not isinstance(away_rank, int)
        or away_rank < 1
    ):
        raise ExcitementInputError("CORS ranks must be positive integers")
    return exp(-((home_rank + away_rank - 2) / 100))


def calculate_bev(pregame: QualifiedPregame) -> ExcitementScore:
    if pregame.cors_home_margin is None:
        raise ExcitementInputError("BEV requires a qualified CORS forecast margin")
    quality = matchup_quality(pregame.home_rank, pregame.away_rank)
    scaled_margin = abs(pregame.cors_home_margin) / 14
    competitiveness = exp(-(scaled_margin * scaled_margin))
    boost = 0.0
    if pregame.market is not None:
        market_margin = pregame.market.home_margin
        closer = _clip((abs(market_margin) - abs(pregame.cors_home_margin)) / 14)
        upset = (
            _clip(abs(pregame.cors_home_margin) / 2)
            if pregame.cors_home_margin * market_margin < 0
            else 0.0
        )
        boost = competitiveness * (3 * closer + 2 * upset)
    base = 60 * quality + 40 * competitiveness
    remaining = 100 - base
    value = base + boost * remaining / (5 + remaining)
    evidence = ScoreEvidence(
        scoring_version=SCORING_VERSION,
        forecast_id=pregame.forecast_id,
        pregame_snapshot_id=pregame.source_snapshot_id,
        market_basis="market" if pregame.market else "missing",
        market_policy_id=pregame.market.policy_id if pregame.market else None,
        market_snapshot_id=pregame.market.source_snapshot_id if pregame.market else None,
    )
    return ExcitementScore(value, BevComponents(quality, competitiveness, boost), evidence)


def normalize_timeline(
    points: Iterable[TimelinePoint], final: QualifiedFinal
) -> NormalizedTimeline:
    if final.overtime is None:
        raise TimelineQualificationError("full AEV requires verified overtime status")
    supplied = tuple(points)
    if not supplied:
        raise TimelineQualificationError("timeline is empty")
    seen_sequence: dict[int, TimelinePoint] = {}
    for point in supplied:
        prior = seen_sequence.get(point.sequence)
        if prior is not None and prior != point:
            raise TimelineQualificationError("one sequence identifies conflicting score states")
        seen_sequence[point.sequence] = point
    unique = tuple(seen_sequence[key] for key in sorted(seen_sequence))
    encountered_overtime = False
    for point in unique:
        if point.overtime:
            encountered_overtime = True
        elif encountered_overtime:
            raise TimelineQualificationError("regulation event follows an overtime event")
    regulation = tuple(point for point in unique if not point.overtime)
    overtime = tuple(point for point in unique if point.overtime)
    if not regulation or regulation[0].elapsed_minute != 0:
        raise TimelineQualificationError("verified regulation timeline must begin at minute 0")
    if regulation[0].home_score != 0 or regulation[0].away_score != 0:
        raise TimelineQualificationError("verified regulation timeline must begin 0-0")
    last_minute = -1.0
    last_scores = (0, 0)
    for point in regulation:
        assert point.elapsed_minute is not None
        if point.elapsed_minute < last_minute:
            raise TimelineQualificationError("regulation events are out of clock order")
        if point.home_score < last_scores[0] or point.away_score < last_scores[1]:
            raise TimelineQualificationError("scores cannot decrease")
        last_minute = point.elapsed_minute
        last_scores = (point.home_score, point.away_score)
    if last_minute != 60:
        raise TimelineQualificationError("full AEV requires a verified 60-minute regulation timeline")
    if final.quarter_scores is not None:
        for boundary, expected_scores in zip((15, 30, 45), final.quarter_scores):
            observed = regulation[0]
            for point in regulation:
                assert point.elapsed_minute is not None
                if point.elapsed_minute > boundary:
                    break
                observed = point
            if (observed.home_score, observed.away_score) != expected_scores:
                raise TimelineQualificationError(
                    f"timeline disagrees with cumulative score after quarter {boundary // 15}"
                )
    for point in overtime:
        if point.home_score < last_scores[0] or point.away_score < last_scores[1]:
            raise TimelineQualificationError("overtime scores cannot decrease")
        last_scores = (point.home_score, point.away_score)
    if bool(overtime) != final.overtime:
        raise TimelineQualificationError("timeline overtime evidence disagrees with final")
    if overtime and regulation[-1].home_score != regulation[-1].away_score:
        raise TimelineQualificationError("overtime requires a tied score after regulation")
    if last_scores != (final.home_score, final.away_score):
        raise TimelineQualificationError("timeline does not agree with verified final score")
    observed = tuple((point.home_score, point.away_score) for point in unique)
    # Same-clock transitions have zero duration and cannot inflate tension, but
    # remain ordered evidence for lead-change and comeback components.
    return NormalizedTimeline(regulation, overtime, observed)


def _surprise(
    pregame: QualifiedPregame, final: QualifiedFinal
) -> tuple[float, Literal["market", "cors", "missing"]]:
    if final.home_score == final.away_score:
        if pregame.market is not None:
            return 0.0, "market"
        return 0.0, "cors" if pregame.cors_home_margin is not None else "missing"
    home_won = final.home_score > final.away_score
    if pregame.market is not None:
        reference = pregame.market.home_margin
        winner_was_underdog = (home_won and reference < 0) or (not home_won and reference > 0)
        return (_clip(abs(reference) / 14) if winner_was_underdog else 0.0), "market"
    if pregame.cors_home_margin is None:
        return 0.0, "missing"
    reference = pregame.cors_home_margin
    winner_was_underdog = (home_won and reference < 0) or (not home_won and reference > 0)
    return (_clip(abs(reference) / 14) if winner_was_underdog else 0.0), "cors"


def calculate_full_aev(
    pregame: QualifiedPregame,
    final: QualifiedFinal,
    timeline: Iterable[TimelinePoint] | NormalizedTimeline,
) -> ExcitementScore:
    if isinstance(timeline, NormalizedTimeline):
        normalized = normalize_timeline(timeline.regulation + timeline.overtime, final)
        if normalized != timeline:
            raise TimelineQualificationError("normalized timeline contains unbound score states")
    else:
        normalized = normalize_timeline(timeline, final)
    tension_integral = 0.0
    for current, following in zip(normalized.regulation, normalized.regulation[1:]):
        start = float(current.elapsed_minute)
        end = float(following.elapsed_minute)
        midpoint = (start + end) / 2
        weight = 1 + 3 * midpoint / 60
        margin = current.home_score - current.away_score
        tension_integral += exp(-abs(margin) / 14) * weight * (end - start)
    tension = tension_integral / 150
    lead_changes = 0
    last_nonzero_lead = 0
    for home_score, away_score in normalized.observed_states:
        lead = (home_score > away_score) - (home_score < away_score)
        if lead and last_nonzero_lead and lead != last_nonzero_lead:
            lead_changes += 1
        if lead:
            last_nonzero_lead = lead
    lead_component = _clip(lead_changes / 3)
    if final.home_score == final.away_score:
        comeback = 0.0
    else:
        home_won = final.home_score > final.away_score
        deficits = [
            max(0, away - home) if home_won else max(0, home - away)
            for home, away in normalized.observed_states
        ]
        comeback = _clip(max(deficits) / 21)
    overtime = 1.0 if final.overtime else 0.0
    quality = matchup_quality(pregame.home_rank, pregame.away_rank)
    surprise, surprise_basis = _surprise(pregame, final)
    drama = 80 * tension + 8 * lead_component + 4 * comeback
    value = drama + 2 * overtime + 3 * quality + 3 * surprise
    evidence = ScoreEvidence(
        scoring_version=SCORING_VERSION,
        forecast_id=pregame.forecast_id,
        pregame_snapshot_id=pregame.source_snapshot_id,
        market_basis="market" if pregame.market else "missing",
        market_policy_id=pregame.market.policy_id if pregame.market else None,
        market_snapshot_id=pregame.market.source_snapshot_id if pregame.market else None,
        surprise_basis=surprise_basis,
        final_snapshot_id=final.source_snapshot_id,
        evidence_tier="full",
    )
    components = AevComponents(tension, lead_component, comeback, overtime, quality, surprise, drama)
    return ExcitementScore(value, components, evidence)
