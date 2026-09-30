"""Frozen comparable-game references and AEV* routing for ADR-0021."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from math import isfinite, sqrt
from pathlib import Path
from statistics import mean
from typing import Iterable, Literal, Mapping, Sequence

from cfb.excitement import (
    AevComponents,
    ExcitementInputError,
    ExcitementScore,
    NormalizedTimeline,
    QualifiedFinal,
    QualifiedPregame,
    ScoreEvidence,
    TimelinePoint,
    TimelineQualificationError,
    SCORING_VERSION,
    _surprise,
    calculate_full_aev,
    matchup_quality,
    normalize_timeline,
)


REFERENCE_SCHEMA_VERSION = 1
REFERENCE_ALGORITHM_VERSION = "sportsrank-comparable-aev-v1"
REFERENCE_FEATURE_VERSION = "winner-oriented-final-quarter-v1"
Tier = Literal["quarter", "final"]


class ReferenceArtifactError(ExcitementInputError):
    """A reference artifact is absent, corrupt, or incompatible."""


@dataclass(frozen=True)
class ReferenceGame:
    """One qualified full-data game's frozen matching features and targets."""

    game_id: str
    season: int
    source_snapshot_id: str
    absolute_final_margin: int
    total_points: int
    overtime: bool
    drama_target: float
    mixed_target: float
    q1_winner_margin: int | None = None
    q2_winner_margin: int | None = None
    q3_winner_margin: int | None = None

    def __post_init__(self) -> None:
        for field in ("game_id", "source_snapshot_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
                raise ReferenceArtifactError(f"{field} must be a safe non-empty scalar string")
        if isinstance(self.season, bool) or not isinstance(self.season, int) or self.season < 1869:
            raise ReferenceArtifactError("reference season is invalid")
        for field in ("absolute_final_margin", "total_points"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReferenceArtifactError(f"{field} must be a non-negative integer")
        if self.absolute_final_margin > self.total_points:
            raise ReferenceArtifactError("absolute final margin cannot exceed total points")
        if (self.total_points - self.absolute_final_margin) % 2:
            raise ReferenceArtifactError("final margin and total cannot represent integer scores")
        if not isinstance(self.overtime, bool):
            raise ReferenceArtifactError("reference overtime must be verified true or false")
        if self.overtime and self.absolute_final_margin == 0:
            raise ReferenceArtifactError("overtime reference cannot have a tied final")
        quarter = (self.q1_winner_margin, self.q2_winner_margin, self.q3_winner_margin)
        if any(value is None for value in quarter) and not all(value is None for value in quarter):
            raise ReferenceArtifactError("quarter features must be all present or all absent")
        if self.absolute_final_margin == 0 and all(value is not None for value in quarter):
            raise ReferenceArtifactError("historical ties must use the final-score tier")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in quarter if value is not None):
            raise ReferenceArtifactError("quarter features must be integers")
        if any(abs(value) > self.total_points for value in quarter if value is not None):
            raise ReferenceArtifactError("quarter margin cannot exceed final total points")
        for field in ("drama_target", "mixed_target"):
            raw_value = getattr(self, field)
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ReferenceArtifactError(f"{field} must be numeric")
            value = float(raw_value)
            maximum = 92 if field == "drama_target" else 94
            if not isfinite(value) or not 0 <= value <= maximum:
                raise ReferenceArtifactError(f"{field} is outside the bounded drama target")
            object.__setattr__(self, field, value)
        expected_mixed = self.drama_target + (2 if self.overtime else 0)
        if abs(self.mixed_target - expected_mixed) > 1e-9:
            raise ReferenceArtifactError("mixed_target must equal drama_target plus observed OT bonus")

    @property
    def has_quarters(self) -> bool:
        return self.q1_winner_margin is not None


@dataclass(frozen=True)
class FeatureScale:
    tier: Tier
    overtime_pool: Literal["true", "false", "all"]
    divisors: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.tier not in ("quarter", "final"):
            raise ReferenceArtifactError("unknown feature tier")
        if self.overtime_pool not in ("true", "false", "all"):
            raise ReferenceArtifactError("unknown overtime pool")
        expected = 5 if self.tier == "quarter" else 2
        if len(self.divisors) != expected or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            or float(value) < 1
            for value in self.divisors
        ):
            raise ReferenceArtifactError("feature scale has invalid divisors")
        object.__setattr__(self, "divisors", tuple(float(value) for value in self.divisors))


@dataclass(frozen=True)
class ReferenceArtifact:
    schema_version: int
    algorithm_version: str
    scoring_version: str
    feature_version: str
    artifact_id: str
    source_snapshot_ids: tuple[str, ...]
    games: tuple[ReferenceGame, ...]
    scales: tuple[FeatureScale, ...]
    checksum: str


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _features(game: ReferenceGame, tier: Tier) -> tuple[float, ...]:
    base = (float(game.absolute_final_margin), float(game.total_points))
    if tier == "final":
        return base
    if not game.has_quarters:
        raise ReferenceArtifactError("quarter tier requested for reference without quarter evidence")
    return base + (
        float(game.q1_winner_margin),
        float(game.q2_winner_margin),
        float(game.q3_winner_margin),
    )


def _scale(games: Sequence[ReferenceGame], tier: Tier) -> tuple[float, ...]:
    columns = tuple(zip(*(_features(game, tier) for game in games)))
    return tuple(max(1.0, _percentile(column, 0.75) - _percentile(column, 0.25)) for column in columns)


def _artifact_payload(artifact: ReferenceArtifact) -> dict[str, object]:
    return {
        "schema_version": artifact.schema_version,
        "algorithm_version": artifact.algorithm_version,
        "scoring_version": artifact.scoring_version,
        "feature_version": artifact.feature_version,
        "artifact_id": artifact.artifact_id,
        "source_snapshot_ids": list(artifact.source_snapshot_ids),
        "games": [asdict(game) for game in artifact.games],
        "scales": [asdict(scale) for scale in artifact.scales],
    }


def _checksum(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def fit_reference_artifact(
    games: Iterable[ReferenceGame], *, artifact_id: str
) -> ReferenceArtifact:
    """Freeze qualified training games and every deterministic pool scale."""

    if (
        not isinstance(artifact_id, str)
        or not artifact_id.strip()
        or any(ord(character) < 32 for character in artifact_id)
    ):
        raise ReferenceArtifactError("artifact_id must be a non-empty string")
    ordered = tuple(sorted(games, key=lambda game: (game.season, game.game_id)))
    if not ordered:
        raise ReferenceArtifactError("cannot fit an empty reference artifact")
    if len({game.game_id for game in ordered}) != len(ordered):
        raise ReferenceArtifactError("reference game IDs must be unique")
    scales: list[FeatureScale] = []
    for tier in ("final", "quarter"):
        tier_games = tuple(game for game in ordered if tier == "final" or game.has_quarters)
        for pool_name, pool_games in (
            ("all", tier_games),
            ("false", tuple(game for game in tier_games if not game.overtime)),
            ("true", tuple(game for game in tier_games if game.overtime)),
        ):
            if pool_games:
                scales.append(FeatureScale(tier, pool_name, _scale(pool_games, tier)))
    artifact = ReferenceArtifact(
        REFERENCE_SCHEMA_VERSION,
        REFERENCE_ALGORITHM_VERSION,
        SCORING_VERSION,
        REFERENCE_FEATURE_VERSION,
        artifact_id,
        tuple(sorted({game.source_snapshot_id for game in ordered})),
        ordered,
        tuple(scales),
        "",
    )
    return replace(artifact, checksum=_checksum(_artifact_payload(artifact)))


def reference_game_from_timeline(
    pregame: QualifiedPregame,
    final: QualifiedFinal,
    timeline: Iterable[TimelinePoint] | NormalizedTimeline,
) -> ReferenceGame:
    """Derive a reference row only from a timeline accepted by full AEV validation."""

    score = calculate_full_aev(pregame, final, timeline)
    if not isinstance(score.components, AevComponents):  # defensive type narrowing
        raise ReferenceArtifactError("full AEV did not produce AEV components")
    components = score.components
    if components.tension is None or components.lead_changes is None or components.comeback is None:
        raise ReferenceArtifactError("reference target requires complete measured drama")
    drama = 80 * components.tension + 8 * components.lead_changes + 4 * components.comeback
    absolute_margin = abs(final.home_score - final.away_score)
    quarter_margins: tuple[int | None, int | None, int | None] = (None, None, None)
    if final.quarter_scores is not None and absolute_margin != 0:
        home_won = final.home_score > final.away_score
        quarter_margins = tuple(
            (home - away) if home_won else (away - home)
            for home, away in final.quarter_scores
        )
    return ReferenceGame(
        game_id=final.game_id,
        season=final.season,
        source_snapshot_id=final.source_snapshot_id,
        absolute_final_margin=absolute_margin,
        total_points=final.home_score + final.away_score,
        overtime=bool(final.overtime),
        drama_target=drama,
        mixed_target=drama + (2 if final.overtime else 0),
        q1_winner_margin=quarter_margins[0],
        q2_winner_margin=quarter_margins[1],
        q3_winner_margin=quarter_margins[2],
    )


def dump_reference_artifact(artifact: ReferenceArtifact, path: Path) -> None:
    validate_reference_artifact(artifact)
    payload = _artifact_payload(artifact) | {"checksum": artifact.checksum}
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReferenceArtifactError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _require_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise ReferenceArtifactError(f"{context} has unexpected or missing fields")


def load_reference_artifact(
    path: Path, *, expected_checksum: str | None = None
) -> ReferenceArtifact:
    if not path.is_file():
        raise ReferenceArtifactError(f"reference artifact is missing: {path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
        _require_keys(
            payload,
            {
                "schema_version",
                "algorithm_version",
                "scoring_version",
                "feature_version",
                "artifact_id",
                "source_snapshot_ids",
                "games",
                "scales",
                "checksum",
            },
            "reference artifact",
        )
        game_keys = set(ReferenceGame.__dataclass_fields__)
        scale_keys = set(FeatureScale.__dataclass_fields__)
        for game in payload["games"]:
            _require_keys(game, game_keys, "reference game")
        for scale in payload["scales"]:
            _require_keys(scale, scale_keys, "feature scale")
        games = tuple(ReferenceGame(**game) for game in payload["games"])
        scales = tuple(
            FeatureScale(scale["tier"], scale["overtime_pool"], tuple(scale["divisors"]))
            for scale in payload["scales"]
        )
        artifact = ReferenceArtifact(
            payload["schema_version"],
            payload["algorithm_version"],
            payload["scoring_version"],
            payload["feature_version"],
            payload["artifact_id"],
            tuple(payload["source_snapshot_ids"]),
            games,
            scales,
            payload["checksum"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ReferenceArtifactError("reference artifact is malformed") from error
    validate_reference_artifact(artifact)
    if expected_checksum is not None and artifact.checksum != expected_checksum:
        raise ReferenceArtifactError("reference artifact does not match trusted checksum")
    return artifact


def validate_reference_artifact(artifact: ReferenceArtifact) -> None:
    if artifact.schema_version != REFERENCE_SCHEMA_VERSION:
        raise ReferenceArtifactError("incompatible reference schema version")
    if artifact.algorithm_version != REFERENCE_ALGORITHM_VERSION:
        raise ReferenceArtifactError("incompatible reference algorithm version")
    if artifact.scoring_version != SCORING_VERSION:
        raise ReferenceArtifactError("incompatible excitement scoring version")
    if artifact.feature_version != REFERENCE_FEATURE_VERSION:
        raise ReferenceArtifactError("incompatible reference feature version")
    if not artifact.games:
        raise ReferenceArtifactError("reference artifact contains no games")
    if tuple(sorted(artifact.games, key=lambda game: (game.season, game.game_id))) != artifact.games:
        raise ReferenceArtifactError("reference games are not canonically ordered")
    if tuple(sorted(set(artifact.source_snapshot_ids))) != artifact.source_snapshot_ids:
        raise ReferenceArtifactError("source snapshot identities are not canonical")
    if set(artifact.source_snapshot_ids) != {game.source_snapshot_id for game in artifact.games}:
        raise ReferenceArtifactError("source snapshot identities do not match reference games")
    expected = fit_reference_artifact(artifact.games, artifact_id=artifact.artifact_id)
    if artifact.scales != expected.scales or artifact.checksum != expected.checksum:
        raise ReferenceArtifactError("reference artifact integrity check failed")


def _target_features(final: QualifiedFinal) -> tuple[Tier, tuple[float, ...]]:
    absolute_margin = abs(final.home_score - final.away_score)
    base = (float(absolute_margin), float(final.home_score + final.away_score))
    if final.quarter_scores is None or absolute_margin == 0:
        return "final", base
    home_won = final.home_score > final.away_score
    oriented = tuple(
        (home - away) if home_won else (away - home)
        for home, away in final.quarter_scores
    )
    return "quarter", base + tuple(float(value) for value in oriented)


def estimate_aev(
    pregame: QualifiedPregame,
    final: QualifiedFinal,
    artifact: ReferenceArtifact,
    *,
    exclude_game_id: str | None = None,
    qualification_reasons: Iterable[str] = (),
) -> ExcitementScore:
    """Estimate AEV from frozen references, retaining tier and support evidence."""

    validate_reference_artifact(artifact)
    tier, target = _target_features(final)
    target_is_reference = any(game.game_id == final.game_id for game in artifact.games)
    candidates = tuple(
        game
        for game in artifact.games
        if game.game_id not in {final.game_id, exclude_game_id}
        and (tier == "final" or game.has_quarters)
    )
    if not candidates:
        raise ReferenceArtifactError("no eligible reference games after target exclusion")
    if final.overtime is None:
        pool_name = "all"
        pool = candidates
        target_field = "mixed_target"
    else:
        same_status = tuple(game for game in candidates if game.overtime == final.overtime)
        if same_status:
            pool_name = "true" if final.overtime else "false"
            pool = same_status
        else:
            pool_name = "all"
            pool = candidates
        target_field = "drama_target"
    scale = (
        _scale(pool, tier)
        if target_is_reference
        else next(
            (
                record.divisors
                for record in artifact.scales
                if record.tier == tier and record.overtime_pool == pool_name
            ),
            None,
        )
    )
    if scale is None:
        # Target exclusion can empty a fitted status pool; the accepted fallback is all-status.
        pool_name = "all"
        pool = candidates
        scale = _scale(pool, tier) if target_is_reference else next(
            record.divisors
            for record in artifact.scales
            if record.tier == tier and record.overtime_pool == "all"
        )
    distances = sorted(
        (
            sqrt(sum(((left - right) / divisor) ** 2 for left, right, divisor in zip(_features(game, tier), target, scale))),
            game.game_id,
            game,
        )
        for game in pool
    )
    cutoff = distances[min(49, len(distances) - 1)][0]
    neighbors = tuple(game for distance, _, game in distances if distance <= cutoff)
    estimated_drama = mean(float(getattr(game, target_field)) for game in neighbors)
    quality = matchup_quality(pregame.home_rank, pregame.away_rank)
    surprise, surprise_basis = _surprise(pregame, final)
    observed_overtime = 2.0 if final.overtime else 0.0
    value = estimated_drama + (observed_overtime if final.overtime is not None else 0) + 3 * quality + 3 * surprise
    evidence = ScoreEvidence(
        scoring_version=SCORING_VERSION,
        forecast_id=pregame.forecast_id,
        pregame_snapshot_id=pregame.source_snapshot_id,
        market_basis="market" if pregame.market else "missing",
        market_policy_id=pregame.market.policy_id if pregame.market else None,
        market_snapshot_id=pregame.market.source_snapshot_id if pregame.market else None,
        surprise_basis=surprise_basis,
        final_snapshot_id=final.source_snapshot_id,
        evidence_tier=tier,
        reference_artifact_id=artifact.artifact_id,
        reference_checksum=artifact.checksum,
        reference_algorithm_version=artifact.algorithm_version,
        reference_pool=pool_name,
        reference_support=len(neighbors),
        reference_cutoff_distance=cutoff,
        reference_neighbor_ids=tuple(game.game_id for game in neighbors),
        reference_scale=scale,
        qualification_reasons=tuple(qualification_reasons),
    )
    components = AevComponents(None, None, None, float(final.overtime) if final.overtime is not None else None, quality, surprise, estimated_drama)
    return ExcitementScore(value, components, evidence, estimated=True)


def calculate_aev(
    pregame: QualifiedPregame,
    final: QualifiedFinal,
    timeline: Iterable[TimelinePoint] | NormalizedTimeline | None,
    reference: ReferenceArtifact | None,
) -> ExcitementScore:
    """Use qualified full flow or visibly route absent/invalid flow to AEV*."""

    reasons: tuple[str, ...]
    if timeline is not None:
        try:
            score = calculate_full_aev(pregame, final, timeline)
            return replace(score, evidence=replace(score.evidence, evidence_tier="full"))
        except TimelineQualificationError as error:
            reasons = (str(error),)
    else:
        reasons = ("score timeline is unavailable",)
    if reference is None:
        raise ReferenceArtifactError(
            "full-data timeline is unavailable or invalid and no compatible reference artifact was supplied: "
            + "; ".join(reasons)
        )
    return estimate_aev(
        pregame,
        final,
        reference,
        exclude_game_id=final.game_id,
        qualification_reasons=reasons,
    )
