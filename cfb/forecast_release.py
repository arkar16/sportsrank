"""Release artifacts for preserved issued forecasts and their evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TYPE_CHECKING

from .forecast_record import (
    FinalScore, ForecastAggregate, ForecastCandidate, ForecastDisposition,
    ForecastGrade, ForecastContractError, GameIdentity, GameTimingEvidence,
    PublicationReceipt, ScoreRevision, aggregate_grades, grade_forecast,
    select_graded_forecast,
)
from .season_source import SourceGame, is_completed, is_explicit_non_played
from .publication_records import ArchiveReference

if TYPE_CHECKING:
    from .forecast_publication import VerifiedForecastPublication
    from .season_snapshot import SeasonSnapshot


LEDGER_SCHEMA = "forecast-ledger/v1"
EVALUATION_SCHEMA = "forecast-evaluation/v1"
CURRENT_ARTIFACT_CONTRACT = 3


@dataclass(frozen=True)
class ForecastSourceCheckpoint:
    """Independently reconstructed source facts for one forecast checkpoint."""

    snapshot_digest: str
    rating_checkpoint: str
    rating_cutoff: str
    rating_rows: tuple[Mapping[str, Any], ...]
    games: tuple[GameIdentity, ...]
    model_version: str
    code_revision: str
    home_field_advantage: Decimal


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ForecastContractError("forecast release timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def game_identity(snapshot: "SeasonSnapshot", game: SourceGame) -> GameIdentity:
    if game.provider_id is None:
        raise ForecastContractError("issued forecasts require a stable provider Game id")
    return GameIdentity(
        str(game.provider_id), int(snapshot.year), int(game.week), game.home_team,
        game.away_team, game.home_classification, game.away_classification,
        bool(game.neutral_site),
    )


def _merge_unique(items: Iterable[Any], key) -> tuple[Any, ...]:
    merged: dict[str, Any] = {}
    for item in items:
        identity = key(item)
        if identity in merged and merged[identity] != item:
            raise ForecastContractError(f"conflicting forecast history for {identity}")
        merged[identity] = item
    return tuple(merged[name] for name in sorted(merged))


def _validate_candidate_graph(candidates: Sequence[ForecastCandidate]) -> None:
    by_id = {item.version_id: item for item in candidates}
    if len(by_id) != len(candidates):
        raise ForecastContractError("forecast history contains duplicate candidate ids")
    roots: Counter[str] = Counter()
    children: Counter[str] = Counter()
    for item in candidates:
        predecessor_id = item.predecessor_version_id
        if predecessor_id is None:
            roots[item.game.key] += 1
            continue
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            raise ForecastContractError("forecast replacement predecessor is missing")
        if predecessor.game != item.game:
            raise ForecastContractError("forecast replacement changes Game identity")
        children[predecessor_id] += 1
        if children[predecessor_id] > 1:
            raise ForecastContractError("forecast correction history forks")
    for game_key in {item.game.key for item in candidates}:
        if roots[game_key] != 1:
            raise ForecastContractError("forecast history must have one immutable root per Game")


def select_displayed_forecast(
    candidates: Sequence[ForecastCandidate],
    receipts: Sequence[PublicationReceipt],
    timing: GameTimingEvidence | None,
) -> ForecastCandidate:
    """Select the immutable value shown on the public forecast page."""

    rows = tuple(candidates)
    if not rows:
        raise ForecastContractError("forecast display requires candidate history")
    if timing is not None:
        selected = select_graded_forecast(rows, receipts, timing)
        if selected is not None:
            return selected
    issued_ids = {receipt.candidate_version_id for receipt in receipts}
    choices = tuple(item for item in rows if item.version_id in issued_ids) or rows
    child_ids = {
        item.predecessor_version_id
        for item in choices if item.predecessor_version_id is not None
    }
    terminals = tuple(item for item in choices if item.version_id not in child_ids)
    if len(terminals) != 1:
        raise ForecastContractError("forecast display history has no unique terminal version")
    return terminals[0]


def validate_forecast_sources(
    ledger: Mapping[str, Any],
    checkpoints: Sequence[ForecastSourceCheckpoint],
) -> None:
    """Bind candidate provenance to an archived snapshot and ranking calculation."""

    sources_by_digest: dict[str, list[ForecastSourceCheckpoint]] = defaultdict(list)
    for source in checkpoints:
        if source not in sources_by_digest[source.snapshot_digest]:
            sources_by_digest[source.snapshot_digest].append(source)
    candidates = tuple(ForecastCandidate.from_dict(item) for item in ledger["candidates"])
    issued_ids = {
        PublicationReceipt.from_dict(item).candidate_version_id
        for item in ledger.get("receipts", [])
    }
    _validate_candidate_graph(candidates)
    for candidate in candidates:
        provenance = candidate.provenance
        matching_sources = [
            source for source in sources_by_digest.get(provenance.source_snapshot_digest, [])
            if (
                source.rating_checkpoint,
                source.rating_cutoff,
                source.model_version,
                source.code_revision,
            ) == (
                provenance.rating_checkpoint,
                provenance.rating_cutoff,
                provenance.model_version,
                provenance.code_revision,
            )
        ]
        if not matching_sources:
            raise ForecastContractError("forecast source snapshot is not in cumulative run evidence")
        if len(matching_sources) != 1:
            raise ForecastContractError("forecast source checkpoint header is ambiguous")
        source = matching_sources[0]
        if provenance.source_kind != "season-snapshot":
            raise ForecastContractError("forecast source kind is not the Release season snapshot")
        source_games = {item.key: item for item in source.games}
        if source_games.get(candidate.game.key) != candidate.game:
            raise ForecastContractError("forecast Game identity disagrees with its source snapshot")
        rating_digest = "sha256:" + hashlib.sha256(canonical_json(list(source.rating_rows))).hexdigest()
        if provenance.rating_artifact_digest != rating_digest:
            raise ForecastContractError("forecast rating digest disagrees with its source checkpoint")
        ratings = {str(row["school"]): row for row in source.rating_rows}
        try:
            home = ratings[candidate.game.home_team]
            away = ratings[candidate.game.away_team]
            expected_values = (
                Decimal(str(home["cors"])), Decimal(str(away["cors"])),
                int(home["rank"]), int(away["rank"]),
                Decimal("0") if candidate.game.neutral_site else source.home_field_advantage,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ForecastContractError("forecast rating provenance cannot be reconstructed") from exc
        actual_values = (
            provenance.home_rating, provenance.away_rating,
            provenance.home_rank, provenance.away_rank,
            provenance.home_field_advantage,
        )
        if actual_values != expected_values:
            raise ForecastContractError("forecast ratings, ranks or home-field value disagree with source")
        if candidate.version_id not in issued_ids:
            expected_margin = (
                provenance.home_rating - provenance.away_rating
                + (Decimal("0") if candidate.game.neutral_site else provenance.home_field_advantage)
            )
            if candidate.precision != 2 or candidate.home_margin != expected_margin:
                raise ForecastContractError("new forecast value disagrees with natural source arithmetic")


def _read_ledger(path: Path, season: int) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": LEDGER_SCHEMA, "season": season, "candidates": [],
            "receipts": [], "publication_provenance": [], "timing_evidence": [],
            "score_history": [],
        }
    value = json.loads(path.read_bytes())
    if not validate_public_forecast_json(value) or value.get("schema_version") != LEDGER_SCHEMA:
        raise ForecastContractError("inherited forecast ledger is invalid")
    if value["season"] != season:
        raise ForecastContractError("inherited forecast ledger has another season")
    return value


def build_forecast_artifacts(
    snapshot: "SeasonSnapshot",
    *,
    inherited_ledger: str | Path,
    generated_candidates: Sequence[ForecastCandidate],
    forecast_publications: Sequence["VerifiedForecastPublication"],
    timing_evidence: Sequence[GameTimingEvidence],
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge immutable history, update score revisions and evaluate one season."""

    # Import lazily so public-safety validation has no publication dependency.
    from .forecast_publication import VerifiedForecastPublication

    if any(not isinstance(item, VerifiedForecastPublication) for item in forecast_publications):
        raise ForecastContractError("forecast publications must be verified capabilities")
    inherited = _read_ledger(Path(inherited_ledger), snapshot.year)
    old_candidates = tuple(ForecastCandidate.from_dict(item) for item in inherited["candidates"])
    old_receipts = tuple(PublicationReceipt.from_dict(item) for item in inherited["receipts"])
    old_timing = tuple(GameTimingEvidence.from_dict(item) for item in inherited["timing_evidence"])
    old_scores = tuple(FinalScore.from_dict(item) for item in inherited["score_history"])
    capability_candidates = tuple(candidate for publication in forecast_publications for candidate in publication.candidates)
    capability_receipts = tuple(receipt for publication in forecast_publications for receipt in publication.receipts)
    preserved = (*old_candidates, *capability_candidates)
    preserved_by_game: dict[str, list[ForecastCandidate]] = defaultdict(list)
    for item in preserved:
        preserved_by_game[item.game.key].append(item)
    accepted_generated: list[ForecastCandidate] = []
    for item in generated_candidates:
        prior = preserved_by_game.get(item.game.key, [])
        if not prior:
            accepted_generated.append(item)
        elif item.predecessor_version_id is None:
            # Ordinary rebuild: retain the already-issued values byte-for-byte.
            continue
        elif item.predecessor_version_id not in {candidate.version_id for candidate in prior}:
            raise ForecastContractError("forecast replacement predecessor is not preserved history")
        else:
            accepted_generated.append(item)
    candidates = _merge_unique((*preserved, *accepted_generated), lambda item: item.version_id)
    receipts = _merge_unique((*old_receipts, *capability_receipts), lambda item: item.receipt_id)
    _validate_candidate_graph(candidates)
    timings = _merge_unique((*old_timing, *timing_evidence), lambda item: item.game.key)
    timing_by_game = {item.game.key: item for item in timings}
    candidate_by_game: dict[str, list[ForecastCandidate]] = defaultdict(list)
    for item in candidates:
        if item.game.season == snapshot.year:
            candidate_by_game[item.game.key].append(item)

    score_by_game = {item.game.key: item for item in old_scores}
    observation = _utc(observed_at)
    score_source_digest = "sha256:" + snapshot.checksum.removeprefix("sha256:")
    if len(score_source_digest) != 71:
        score_source_digest = "sha256:" + hashlib.sha256(snapshot.checksum.encode()).hexdigest()
    from .forecast_record import EvidenceRef
    score_source = EvidenceRef("season-snapshot", f"snapshot:{snapshot.year}:{snapshot.checksum}", score_source_digest)
    for game in snapshot.games:
        if (not is_completed(game) or game.home_points is None or game.away_points is None
                or game.provider_id is None or game.home_team == game.away_team):
            continue
        identity = game_identity(snapshot, game)
        prior = score_by_game.get(identity.key)
        if prior is None:
            score_by_game[identity.key] = FinalScore(identity, (ScoreRevision(int(game.home_points), int(game.away_points), observation, score_source),))
        elif (prior.current.home_points, prior.current.away_points) != (int(game.home_points), int(game.away_points)):
            score_by_game[identity.key] = prior.corrected(int(game.home_points), int(game.away_points), observation, score_source)

    grades: list[ForecastGrade] = []
    game_rows: list[dict[str, Any]] = []
    omissions: Counter[str] = Counter()
    for game in sorted(snapshot.games, key=lambda item: (int(item.week), item.home_team, item.away_team)):
        if game.home_team == game.away_team:
            # Provider placeholder-vs-placeholder schedule rows have no stable
            # two-participant matchup identity and cannot enter evaluation.
            continue
        identity = game_identity(snapshot, game)
        disposition: ForecastDisposition
        grade = None
        if str(game.home_classification).lower() != "fbs" or str(game.away_classification).lower() != "fbs":
            disposition = ForecastDisposition.INELIGIBLE_CLASSIFICATION
        elif is_explicit_non_played(game):
            disposition = ForecastDisposition.CANCELED
        elif not is_completed(game):
            disposition = ForecastDisposition.PENDING
        elif game.home_points is None or game.away_points is None:
            disposition = ForecastDisposition.MISSING_FINAL_SCORE
        else:
            versions = candidate_by_game.get(identity.key, [])
            issued = [item for item in versions if any(receipt.candidate_version_id == item.version_id for receipt in receipts)]
            if not versions:
                disposition = ForecastDisposition.MISSING_FORECAST
            elif not issued:
                disposition = ForecastDisposition.UNVERIFIED_PUBLICATION
            elif identity.key not in timing_by_game:
                disposition = ForecastDisposition.UNRESOLVED_TEMPORAL_ORDER
            else:
                selected = select_graded_forecast(versions, receipts, timing_by_game[identity.key])
                if selected is None:
                    disposition = ForecastDisposition.UNRESOLVED_TEMPORAL_ORDER
                else:
                    final = score_by_game[identity.key]
                    grade = grade_forecast(selected, final)
                    grades.append(grade)
                    disposition = ForecastDisposition.EVALUATED
        if disposition != ForecastDisposition.EVALUATED:
            omissions[disposition.value] += 1
        game_rows.append({
            "game": identity.to_dict(),
            "disposition": disposition.value,
            "grade": grade.to_dict() if grade else None,
        })

    by_week: dict[int, list[ForecastGrade]] = defaultdict(list)
    for item in grades:
        by_week[item.game.week].append(item)
    provenance = [publication.to_provenance() for publication in forecast_publications]
    inherited_provenance = inherited["publication_provenance"]
    ledger = {
        "schema_version": LEDGER_SCHEMA, "season": snapshot.year,
        "candidates": [item.to_dict() for item in candidates],
        "receipts": [item.to_dict() for item in receipts],
        "publication_provenance": list({json.dumps(item, sort_keys=True): item for item in (*inherited_provenance, *provenance)}.values()),
        "timing_evidence": [item.to_dict() for item in timings],
        "score_history": [score_by_game[key].to_dict() for key in sorted(score_by_game)],
    }
    evaluation = {
        "schema_version": EVALUATION_SCHEMA, "season": snapshot.year,
        "games": game_rows,
        "weekly": {str(week): aggregate_grades(rows).to_dict() for week, rows in sorted(by_week.items())},
        "season_summary": aggregate_grades(grades).to_dict(),
        "omission_counts": dict(sorted(omissions.items())),
    }
    return ledger, evaluation


def validate_forecast_capabilities(
    ledger: Mapping[str, Any], publications: Sequence["VerifiedForecastPublication"]
) -> None:
    """Require current immutable capabilities for every persisted issued receipt."""

    from .forecast_publication import VerifiedForecastPublication
    if any(not isinstance(item, VerifiedForecastPublication) for item in publications):
        raise ForecastContractError("forecast validation requires verified capabilities")
    actual_receipts = {PublicationReceipt.from_dict(item).receipt_id: item for item in ledger["receipts"]}
    supplied_receipts = {receipt.receipt_id: receipt.to_dict() for publication in publications for receipt in publication.receipts}
    if actual_receipts != supplied_receipts:
        raise ForecastContractError("issued forecast receipts do not equal revalidated capabilities")
    actual_provenance_items = [
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in ledger["publication_provenance"]
    ]
    supplied_provenance_items = [
        json.dumps(publication.to_provenance(), sort_keys=True, separators=(",", ":"))
        for publication in publications
    ]
    if len(actual_provenance_items) != len(set(actual_provenance_items)):
        raise ForecastContractError("publication provenance contains duplicate records")
    if len(supplied_provenance_items) != len(set(supplied_provenance_items)):
        raise ForecastContractError("verified capabilities contain duplicate provenance")
    actual_provenance = set(actual_provenance_items)
    supplied_provenance = set(supplied_provenance_items)
    if actual_provenance != supplied_provenance:
        raise ForecastContractError("publication provenance does not equal revalidated capabilities")
    candidates = {
        candidate.version_id: candidate
        for candidate in (ForecastCandidate.from_dict(item) for item in ledger["candidates"])
    }
    for receipt in actual_receipts.values():
        candidate = candidates.get(receipt["candidate_version_id"])
        if candidate is None:
            raise ForecastContractError("issued receipt references a missing candidate")
        if receipt["candidate_artifact_digest"] != candidate.artifact_digest:
            raise ForecastContractError("issued receipt does not bind the exact candidate bytes")
    _validate_candidate_graph(tuple(candidates.values()))


def validate_evaluation_semantics(
    ledger: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    snapshot: "SeasonSnapshot | None" = None,
    score_source_runs: Sequence[tuple["SeasonSnapshot", datetime]] = (),
) -> None:
    """Recalculate every published grade and aggregate from immutable records."""

    candidates = {item.version_id: item for item in (ForecastCandidate.from_dict(value) for value in ledger["candidates"])}
    scores = {item.game.key: item for item in (FinalScore.from_dict(value) for value in ledger["score_history"])}
    receipts = tuple(PublicationReceipt.from_dict(value) for value in ledger["receipts"])
    timings = {item.game.key: item for item in (GameTimingEvidence.from_dict(value) for value in ledger["timing_evidence"])}
    candidates_by_game: dict[str, list[ForecastCandidate]] = defaultdict(list)
    for candidate in candidates.values():
        candidates_by_game[candidate.game.key].append(candidate)
    grades: list[ForecastGrade] = []
    seen_games: set[str] = set()
    omissions: Counter[str] = Counter()
    source_games: dict[str, SourceGame] = {}
    if snapshot is not None:
        source_games = {
            game_identity(snapshot, game).key: game
            for game in snapshot.games
            if game.provider_id is not None and game.home_team != game.away_team
        }
    if score_source_runs:
        score_sources: dict[
            tuple[str, datetime], tuple[SourceGame, str, GameIdentity]
        ] = {}
        for source_snapshot, observed_at in score_source_runs:
            for source_game in source_snapshot.games:
                if source_game.provider_id is None or source_game.home_team == source_game.away_team:
                    continue
                source_identity = game_identity(source_snapshot, source_game)
                score_sources[(source_identity.key, observed_at)] = (
                    source_game, str(source_snapshot.checksum), source_identity
                )
        for final_score in scores.values():
            for revision in final_score.revisions:
                source_record = score_sources.get((final_score.game.key, revision.observed_at))
                if source_record is None:
                    raise ForecastContractError("score revision is not bound to an archived source observation")
                source_game, source_checksum, source_identity = source_record
                expected_digest = "sha256:" + source_checksum.removeprefix("sha256:")
                if len(expected_digest) != 71:
                    expected_digest = "sha256:" + hashlib.sha256(source_checksum.encode()).hexdigest()
                expected_identifier = f"snapshot:{final_score.game.season}:{source_checksum}"
                if (
                    revision.source.kind != "season-snapshot"
                    or revision.source.reference != expected_identifier
                    or revision.source.digest != expected_digest
                    or final_score.game != source_identity
                    or source_game.home_points != revision.home_points
                    or source_game.away_points != revision.away_points
                ):
                    raise ForecastContractError("score revision disagrees with its archived source observation")
    if snapshot is not None:
        for source_key, source_game in source_games.items():
            if (
                not is_completed(source_game)
                or source_game.home_points is None
                or source_game.away_points is None
            ):
                continue
            final_score = scores.get(source_key)
            if (
                final_score is None
                or final_score.game != game_identity(snapshot, source_game)
                or final_score.current.home_points != source_game.home_points
                or final_score.current.away_points != source_game.away_points
            ):
                raise ForecastContractError("current final score disagrees with the source snapshot")
    for row in evaluation["games"]:
        row_game = GameIdentity.from_dict(row["game"])
        if row_game.key in seen_games:
            raise ForecastContractError("evaluation contains a duplicate Game")
        seen_games.add(row_game.key)
        source_game = source_games.get(row_game.key) if snapshot is not None else None
        if snapshot is not None:
            if source_game is None or game_identity(snapshot, source_game) != row_game:
                raise ForecastContractError("evaluation Game differs from the source snapshot")
        raw_grade = row["grade"]
        if row["disposition"] == ForecastDisposition.EVALUATED.value:
            if raw_grade is None:
                raise ForecastContractError("evaluated game has no grade")
            grade = ForecastGrade.from_dict(raw_grade)
            if grade.game != row_game:
                raise ForecastContractError("evaluation row Game differs from its grade")
            if source_game is not None and (
                str(source_game.home_classification).lower() != "fbs"
                or str(source_game.away_classification).lower() != "fbs"
                or is_explicit_non_played(source_game)
                or not is_completed(source_game)
                or source_game.home_points is None
                or source_game.away_points is None
            ):
                raise ForecastContractError("evaluated game is not an eligible completed source Game")
            candidate = candidates.get(grade.forecast_version_id)
            score = scores.get(grade.game.key)
            if candidate is None or score is None or grade_forecast(candidate, score) != grade:
                raise ForecastContractError("published forecast grade disagrees with immutable inputs")
            timing = timings.get(grade.game.key)
            if timing is None or select_graded_forecast(candidates_by_game[grade.game.key], receipts, timing) != candidate:
                raise ForecastContractError("published grade does not use the selected qualifying forecast")
            grades.append(grade)
        elif raw_grade is not None:
            raise ForecastContractError("omitted game must not contain a grade")
        else:
            if source_game is not None:
                if str(source_game.home_classification).lower() != "fbs" or str(source_game.away_classification).lower() != "fbs":
                    expected_disposition = ForecastDisposition.INELIGIBLE_CLASSIFICATION
                elif is_explicit_non_played(source_game):
                    expected_disposition = ForecastDisposition.CANCELED
                elif not is_completed(source_game):
                    expected_disposition = ForecastDisposition.PENDING
                elif source_game.home_points is None or source_game.away_points is None:
                    expected_disposition = ForecastDisposition.MISSING_FINAL_SCORE
                else:
                    versions = candidates_by_game.get(row_game.key, [])
                    issued = [item for item in versions if any(receipt.candidate_version_id == item.version_id for receipt in receipts)]
                    if not versions:
                        expected_disposition = ForecastDisposition.MISSING_FORECAST
                    elif not issued:
                        expected_disposition = ForecastDisposition.UNVERIFIED_PUBLICATION
                    elif row_game.key not in timings:
                        expected_disposition = ForecastDisposition.UNRESOLVED_TEMPORAL_ORDER
                    elif select_graded_forecast(versions, receipts, timings[row_game.key]) is None:
                        expected_disposition = ForecastDisposition.UNRESOLVED_TEMPORAL_ORDER
                    else:
                        raise ForecastContractError("qualifying forecast is omitted from evaluation")
                if row["disposition"] != expected_disposition.value:
                    raise ForecastContractError("evaluation disposition disagrees with source Game state")
            omissions[row["disposition"]] += 1
    if snapshot is not None and seen_games != set(source_games):
        raise ForecastContractError("evaluation does not cover every source Game")
    if aggregate_grades(grades).to_dict() != evaluation["season_summary"]:
        raise ForecastContractError("season forecast aggregate is invalid")
    weekly: dict[str, list[ForecastGrade]] = defaultdict(list)
    for grade in grades:
        weekly[str(grade.game.week)].append(grade)
    expected_weekly = {week: aggregate_grades(rows).to_dict() for week, rows in sorted(weekly.items())}
    if expected_weekly != evaluation["weekly"]:
        raise ForecastContractError("weekly forecast aggregates are invalid")
    if dict(sorted(omissions.items())) != evaluation["omission_counts"]:
        raise ForecastContractError("forecast omission counts are invalid")


def validate_public_forecast_json(value: Any) -> bool:
    """Strict allowlist validator for sanitized public forecast JSON."""

    try:
        if not isinstance(value, Mapping):
            return False
        schema = value.get("schema_version")
        if schema == "forecast-candidate/v1":
            return ForecastCandidate.from_dict(value).to_dict() == value
        if schema == LEDGER_SCHEMA:
            names = {"schema_version", "season", "candidates", "receipts", "publication_provenance", "timing_evidence", "score_history"}
            if set(value) != names or not isinstance(value["season"], int) or isinstance(value["season"], bool):
                return False
            if not all(isinstance(value[name], list) for name in ("candidates", "receipts", "publication_provenance", "timing_evidence", "score_history")):
                return False
            for item in value["candidates"]:
                if ForecastCandidate.from_dict(item).to_dict() != item: return False
            for item in value["receipts"]:
                if PublicationReceipt.from_dict(item).to_dict() != item: return False
            for item in value["timing_evidence"]:
                if GameTimingEvidence.from_dict(item).to_dict() != item: return False
            for item in value["score_history"]:
                if FinalScore.from_dict(item).to_dict() != item: return False
            if not isinstance(value["publication_provenance"], list):
                return False
            provenance_fields = {
                "schema_version", "repository", "intent", "package",
                "provider_result", "provider_source", "verification",
                "verification_source",
            }
            for item in value["publication_provenance"]:
                if not isinstance(item, Mapping) or set(item) != provenance_fields or item.get("schema_version") != "forecast-publication-evidence/v1":
                    return False
                if not isinstance(item["repository"], str):
                    return False
                for name in provenance_fields - {"schema_version", "repository"}:
                    reference = ArchiveReference.from_dict(item[name])
                    if reference.repository != item["repository"]:
                        return False
            return True
        if schema == EVALUATION_SCHEMA:
            names = {"schema_version", "season", "games", "weekly", "season_summary", "omission_counts"}
            if set(value) != names or not isinstance(value["games"], list) or not isinstance(value["season"], int) or isinstance(value["season"], bool) or not isinstance(value["weekly"], Mapping):
                return False
            if ForecastAggregate.from_dict(value["season_summary"]).to_dict() != value["season_summary"]: return False
            for aggregate in value["weekly"].values():
                if ForecastAggregate.from_dict(aggregate).to_dict() != aggregate: return False
            allowed = {item.value for item in ForecastDisposition}
            for row in value["games"]:
                if set(row) != {"game", "disposition", "grade"} or row["disposition"] not in allowed:
                    return False
                if GameIdentity.from_dict(row["game"]).to_dict() != row["game"]: return False
                if row["grade"] is not None and ForecastGrade.from_dict(row["grade"]).to_dict() != row["grade"]: return False
            if not isinstance(value["omission_counts"], Mapping):
                return False
            return all(key in allowed and isinstance(count, int) and not isinstance(count, bool) and count >= 0 for key, count in value["omission_counts"].items())
    except (AttributeError, KeyError, TypeError, ValueError, ForecastContractError):
        return False
    return False


__all__ = [
    "CURRENT_ARTIFACT_CONTRACT", "EVALUATION_SCHEMA", "ForecastSourceCheckpoint", "LEDGER_SCHEMA", "build_forecast_artifacts",
    "canonical_json", "game_identity", "select_displayed_forecast", "validate_forecast_capabilities",
    "validate_evaluation_semantics", "validate_forecast_sources", "validate_public_forecast_json",
]
