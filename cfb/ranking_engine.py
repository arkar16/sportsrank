"""Pure CFB ranking, records, and spread calculations.

The legacy implementation in :mod:`cfb.cors` writes files and changes the
process working directory.  This module is the calculation seam used by
Release generation.  It intentionally retains the CORS v0.4.0 constants and
formula, but accepts an immutable :class:`SeasonSnapshot` and returns plain
Python values so callers can render or validate without I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence
from io import StringIO

import pandas as pd

try:  # Package imports are used by tests and ``python -m cfb``.
    from .season_snapshot import SeasonSnapshot
    from .season_source import (
        SourceGame,
        SourceTeam,
        is_completed as source_is_completed,
        is_explicit_non_played,
    )
except ImportError:  # Preserve direct execution from cfb/ for legacy users.
    from season_snapshot import SeasonSnapshot
    from season_source import (
        SourceGame,
        SourceTeam,
        is_completed as source_is_completed,
        is_explicit_non_played,
    )


MODEL_VERSION = "v0.4.0"
CORS_VERSION = MODEL_VERSION
FCS_CORS = -10.0
HFA = 2.0
REGRESSION_FACTOR = 1.75
TIE_BREAK = "cors desc, wins desc, losses asc, school asc"
GENESIS_YEAR = 1897


class RankingContractError(ValueError):
    """Raised when a ranking request cannot satisfy the lifecycle contract."""


class RankingPhase(str, Enum):
    """The three public CORS lifecycle checkpoints.

    PRESEASON is intentionally separate from Week 0: it contains no scored
    games and is the carryover ranking used to price the Week 0 slate.
    """

    PRESEASON = "preseason"
    WEEK = "week"
    FINAL = "final"


@dataclass(frozen=True)
class PreviousFinal:
    """A prior final ranking used for Season Carryover.

    ``cors`` and ``wins_vs_expected`` are keyed by school.  The latter is
    optional in old static files and defaults to zero during readjustment.
    """

    cors: Mapping[str, float]
    wins_vs_expected: Mapping[str, float]
    # These fields are optional because older generated pages did not encode
    # provenance.  When present, lifecycle validation can enforce it.
    year: int | None = None
    classification: str | None = None


class RankingEngine:
    """Small state-free facade around the pure calculation functions."""

    def __init__(self, model_version: str = MODEL_VERSION) -> None:
        if model_version != MODEL_VERSION:
            raise ValueError(f"Unsupported CORS model version: {model_version}")
        self.model_version = model_version

    def records(self, snapshot: SeasonSnapshot, week: int) -> list[dict[str, Any]]:
        return records_for_week(snapshot, week)

    def ranking(
        self,
        snapshot: SeasonSnapshot,
        week: int,
        previous_cors: Mapping[str, float] | None = None,
        previous_wins_vs_expected: Mapping[str, float] | None = None,
        *,
        previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    ) -> list[dict[str, Any]]:
        return ranking_for_week(
            snapshot,
            week,
            previous_cors,
            previous_wins_vs_expected,
            previous_final=previous_final,
            model_version=self.model_version,
        )

    def preseason(
        self,
        snapshot: SeasonSnapshot,
        previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    ) -> list[dict[str, Any]]:
        return preseason_ranking(
            snapshot, previous_final, model_version=self.model_version
        )

    calculate_preseason = preseason

    def week(
        self,
        snapshot: SeasonSnapshot,
        week: int,
        previous_ranking: Sequence[Mapping[str, Any]] | Mapping[str, float] | PreviousFinal | None = None,
        previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    ) -> list[dict[str, Any]]:
        return week_ranking(
            snapshot,
            week,
            previous_ranking=previous_ranking,
            previous_final=previous_final,
            model_version=self.model_version,
        )

    calculate_week = week

    def final(
        self,
        snapshot: SeasonSnapshot,
        previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    ) -> list[dict[str, Any]]:
        return final_ranking(
            snapshot, previous_final, model_version=self.model_version
        )

    calculate_final = final

    def phase(
        self,
        snapshot: SeasonSnapshot,
        phase: RankingPhase | str,
        week: int | None = None,
        previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
        previous_ranking: Sequence[Mapping[str, Any]] | Mapping[str, float] | PreviousFinal | None = None,
        through_week: int | None = None,
    ) -> list[dict[str, Any]]:
        return ranking_for_phase(
            snapshot,
            phase,
            week=week,
            previous_final=previous_final,
            previous_ranking=previous_ranking,
            through_week=through_week,
            model_version=self.model_version,
        )

    calculate_phase = phase

    calculate = ranking

    def season(
        self,
        snapshot: SeasonSnapshot,
        target_week: int | None = None,
        previous_final: PreviousFinal | Mapping[str, float] | None = None,
    ) -> dict[int, list[dict[str, Any]]]:
        return season_rankings(snapshot, target_week, previous_final, model_version=self.model_version)

    calculate_season = season

    def spreads(self, snapshot: SeasonSnapshot, week: int, rankings: Sequence[Mapping[str, Any]], *, hfa: float = HFA) -> list[dict[str, Any]]:
        return spreads_for_week(snapshot, week, rankings, hfa=hfa)

    calculate_spreads = spreads
    records_for_week = records


CORSRankingEngine = RankingEngine


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _strict_number(value: Any, field: str) -> float:
    """Parse a ranking input without silently replacing bad values.

    The legacy ``_number`` helper remains useful for presentation and for
    unknown FCS opponents.  Carryover inputs are a trust boundary, however,
    so ``None``, booleans, malformed text, NaN, and infinities must fail
    closed instead of becoming the all-team ``-10`` fallback.
    """

    if isinstance(value, bool):
        raise RankingContractError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RankingContractError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise RankingContractError(f"{field} must be a finite number")
    return result


def _strict_cors_mapping(value: Any, field: str = "previous_final.cors") -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise RankingContractError(f"{field} must be a mapping keyed by school")
    result: dict[str, float] = {}
    for school, cors in value.items():
        if not isinstance(school, str) or not school.strip():
            raise RankingContractError(f"{field} contains an invalid school")
        if school in result:
            raise RankingContractError(f"{field} contains duplicate school {school!r}")
        result[school] = _strict_number(cors, f"{field}[{school!r}]")
    return result


def _strict_wve_mapping(value: Any, field: str = "previous_final.wins_vs_expected") -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RankingContractError(f"{field} must be a mapping keyed by school")
    result: dict[str, float] = {}
    for school, wins_vs_expected in value.items():
        if not isinstance(school, str) or not school.strip():
            raise RankingContractError(f"{field} contains an invalid school")
        if school in result:
            raise RankingContractError(f"{field} contains duplicate school {school!r}")
        result[school] = _strict_number(
            wins_vs_expected, f"{field}[{school!r}]"
        )
    return result


def _previous_final_from_rows(rows: Sequence[Any], source: str | Path = "previous FINAL") -> PreviousFinal:
    """Build a strict carryover model while retaining duplicate-row errors."""

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise RankingContractError(f"{source} must contain a ranking row list")
    cors: dict[str, float] = {}
    wins_vs_expected: dict[str, float] = {}
    has_wve = False
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise RankingContractError(f"{source} row {index} is not an object")
        school = row.get("school")
        if not isinstance(school, str) or not school.strip():
            raise RankingContractError(f"{source} row {index} has no valid school")
        if school in cors:
            raise RankingContractError(f"{source} contains duplicate school {school!r}")
        if "cors" not in row:
            raise RankingContractError(f"{source} row {index} has no cors value")
        cors[school] = _strict_number(row.get("cors"), f"{source} row {index} cors")
        if "wins_vs_expected" in row and row.get("wins_vs_expected") not in (None, ""):
            wins_vs_expected[school] = _strict_number(
                row.get("wins_vs_expected"),
                f"{source} row {index} wins_vs_expected",
            )
            has_wve = True
    # A few legacy FINAL pages predate this column.  Treat the omitted value
    # as zero, but never silently accept a malformed value that was present.
    if has_wve:
        wins_vs_expected = {school: wins_vs_expected.get(school, 0.0) for school in cors}
    return PreviousFinal(cors=cors, wins_vs_expected=wins_vs_expected)


def _coerce_previous_final(
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None,
) -> PreviousFinal:
    """Normalize carryover input without applying a fallback value."""

    if isinstance(previous_final, PreviousFinal):
        return PreviousFinal(
            cors=_strict_cors_mapping(previous_final.cors),
            wins_vs_expected=_strict_wve_mapping(previous_final.wins_vs_expected),
            year=previous_final.year,
            classification=previous_final.classification,
        )
    if isinstance(previous_final, (str, Path)):
        return load_previous_final_model(previous_final)
    if previous_final is None:
        raise RankingContractError("previous FINAL is required")
    # The compatibility mapping form represents only CORS values.  Missing
    # wins-vs-expected values are the documented legacy zero adjustment.
    return PreviousFinal(cors=_strict_cors_mapping(previous_final), wins_vs_expected={})


def validate_previous_final(
    snapshot: SeasonSnapshot,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None,
    *,
    require: bool = True,
) -> PreviousFinal:
    """Validate carryover against the exact FBS team set in ``snapshot``.

    Any post-genesis season must have exactly one finite CORS value per
    current-season team.  A mismatched, incomplete, duplicate, or malformed
    prior FINAL raises before ranking calculation begins.  ``require=False``
    exists only for the explicit 1897 genesis baseline and does not permit a
    fallback for later seasons.
    """

    teams = _team_map(snapshot)
    expected = set(teams)
    if previous_final is None and not require and snapshot.year == GENESIS_YEAR:
        return PreviousFinal(
            cors={school: 0.0 for school in expected},
            wins_vs_expected={school: 0.0 for school in expected},
            year=None,
            classification=snapshot.classification,
        )
    if previous_final is None:
        if snapshot.year > GENESIS_YEAR:
            raise RankingContractError(
                f"season {snapshot.year} requires a complete prior FINAL"
            )
        raise RankingContractError("previous FINAL is required")
    prior = _coerce_previous_final(previous_final)
    if prior.year is not None and int(prior.year) != snapshot.year - 1:
        raise RankingContractError(
            f"prior FINAL year {prior.year} does not precede season {snapshot.year}"
        )
    if prior.classification is not None and str(prior.classification).upper() != snapshot.classification.upper():
        raise RankingContractError(
            "prior FINAL classification does not match the season snapshot"
        )
    actual = set(prior.cors)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail: list[str] = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        raise RankingContractError(
            "prior FINAL team set does not match season snapshot (" + ", ".join(detail) + ")"
        )
    if prior.wins_vs_expected and set(prior.wins_vs_expected) != expected:
        missing = sorted(expected - set(prior.wins_vs_expected))
        extra = sorted(set(prior.wins_vs_expected) - expected)
        detail: list[str] = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        raise RankingContractError(
            "prior FINAL wins_vs_expected team set does not match snapshot ("
            + ", ".join(detail)
            + ")"
        )
    # Legacy FINALs may omit wins_vs_expected; a zero adjustment is explicit
    # and deterministic once the CORS team set has passed validation.
    wve = {school: prior.wins_vs_expected.get(school, 0.0) for school in expected}
    return PreviousFinal(
        cors=dict(prior.cors),
        wins_vs_expected=wve,
        year=prior.year,
        classification=prior.classification,
    )


def _require_identified_prior_final(
    snapshot: SeasonSnapshot,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None,
) -> None:
    """Require an explicit prior-FINAL carrier for post-genesis carryover.

    A raw school-to-CORS mapping can be a prior *ranking* from any checkpoint;
    it does not prove that the source was the preceding Season's FINAL.  The
    compatibility mapping form remains available to the low-level validator,
    but public carryover checkpoints require ``PreviousFinal`` or a file path
    so the lifecycle cannot be bypassed with arbitrary Week 0 ratings.
    """

    if snapshot.year > GENESIS_YEAR and previous_final is not None and not isinstance(
        previous_final, (PreviousFinal, str, Path)
    ):
        raise RankingContractError(
            "post-genesis carryover requires an identified PreviousFinal "
            "object or FINAL artifact path"
        )


def is_completed(game: SourceGame | Mapping[str, Any]) -> bool:
    """Return whether authoritative completion and finite scores are present."""

    return source_is_completed(game)


def _games(snapshot: SeasonSnapshot, through_week: int | None = None) -> tuple[SourceGame, ...]:
    """Completed games through ``through_week`` in stable source order."""

    if through_week is None:
        through_week = snapshot.complete_through_week
    return tuple(
        game
        for game in snapshot.games
        if int(game.week) <= int(through_week) and is_completed(game)
    )


def completed_games(snapshot: SeasonSnapshot, through_week: int | None = None) -> tuple[SourceGame, ...]:
    """Public completed-game filter used by calculations and validators."""

    return _games(snapshot, through_week)


def scheduled_games(snapshot: SeasonSnapshot, through_week: int | None = None) -> tuple[SourceGame, ...]:
    """Scheduled games through a boundary, including not-yet-scored games."""

    if through_week is None:
        through_week = snapshot.complete_through_week
    return tuple(
        game for game in snapshot.games
        if int(game.week) <= int(through_week) and not is_explicit_non_played(game)
    )


def scheduled_season_end_week(snapshot: SeasonSnapshot) -> int:
    """Return the scheduled season boundary present in the snapshot.

    CFBD's season-games response includes future scheduled games as well as
    completed games.  The greatest week in that immutable response is the
    only end-of-season signal available to an offline renderer; calendar
    guesses are intentionally not part of the release contract.
    """

    return max(
        (int(game.week) for game in snapshot.games if not is_explicit_non_played(game)),
        default=0,
    )


def season_is_complete(snapshot: SeasonSnapshot) -> bool:
    """Whether every scheduled game reaches the snapshot's completion edge."""

    active_games = tuple(game for game in snapshot.games if not is_explicit_non_played(game))
    if not active_games:
        return False
    scheduled_end = scheduled_season_end_week(snapshot)
    return int(snapshot.complete_through_week) >= scheduled_end and all(
        is_completed(game) for game in active_games
    )


def season_end_week(snapshot: SeasonSnapshot, *, completed_only: bool = True) -> int:
    """Derive a season checkpoint or scheduled season boundary.

    ``completed_only=True`` preserves the legacy helper's meaning.  Release
    completion checks use :func:`scheduled_season_end_week` and
    :func:`season_is_complete` so a partial snapshot cannot be mislabeled as
    a final season.
    """

    if completed_only:
        return max(0, int(snapshot.complete_through_week))
    return scheduled_season_end_week(snapshot)


end_week = season_end_week
scheduled_end_week = scheduled_season_end_week
is_season_complete = season_is_complete


def _teams(snapshot: SeasonSnapshot) -> tuple[SourceTeam, ...]:
    # Sorting gives deterministic base order without mutating the snapshot.
    return tuple(sorted(snapshot.teams, key=lambda team: (team.school, team.conference)))


def _team_map(snapshot: SeasonSnapshot) -> dict[str, SourceTeam]:
    result: dict[str, SourceTeam] = {}
    for team in snapshot.teams:
        if team.school in result:
            raise ValueError(f"Duplicate FBS team in snapshot: {team.school}")
        result[team.school] = team
    return result


def records_for_week(snapshot: SeasonSnapshot, week: int) -> list[dict[str, Any]]:
    """Compute records from completed games through ``week``.

    Games against FCS and other non-FBS opponents count toward the FBS team's
    record when one side is a snapshot team.  A game with an unknown FBS side
    is ignored rather than inventing a team in the ranking set.
    """

    teams = _team_map(snapshot)
    stats = {
        school: {"wins": 0, "losses": 0, "ties": 0}
        for school in teams
    }
    for game in _games(snapshot, week):
        home_fbs = game.home_team in teams
        away_fbs = game.away_team in teams
        if not home_fbs and not away_fbs:
            continue
        home_score = float(game.home_points)  # is_completed checked above
        away_score = float(game.away_points)
        if home_fbs and away_fbs:
            if home_score > away_score:
                stats[game.home_team]["wins"] += 1
                stats[game.away_team]["losses"] += 1
            elif away_score > home_score:
                stats[game.away_team]["wins"] += 1
                stats[game.home_team]["losses"] += 1
            else:
                stats[game.home_team]["ties"] += 1
                stats[game.away_team]["ties"] += 1
        else:
            fbs_team = game.home_team if home_fbs else game.away_team
            fbs_score = home_score if home_fbs else away_score
            opponent_score = away_score if home_fbs else home_score
            if fbs_score > opponent_score:
                stats[fbs_team]["wins"] += 1
            elif opponent_score > fbs_score:
                stats[fbs_team]["losses"] += 1
            else:
                stats[fbs_team]["ties"] += 1

    rows: list[dict[str, Any]] = []
    for team in _teams(snapshot):
        values = stats[team.school]
        wins, losses, ties = values["wins"], values["losses"], values["ties"]
        # CORS v0.4.0 historically uses wins/(wins+losses) internally.  The
        # legacy record presentation, however, includes ties in its
        # pre-1996 denominator.  Keep those concerns separate: this row's
        # win_pct is presentation data while _cors_value below retains the
        # established calculation denominator.
        denominator = wins + losses + ties if snapshot.year < 1996 else wins + losses
        record = f"{wins}-{losses}-{ties}" if snapshot.year < 1996 else f"{wins}-{losses}"
        rows.append(
            {
                "school": team.school,
                "conference": team.conference,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "record": record,
                "win_pct": round(wins / denominator, 2) if denominator else 0.0,
            }
        )
    return rows


def _margin_of_victory(team: str, games: Iterable[SourceGame]) -> float:
    margins: list[float] = []
    for game in games:
        if game.home_team == team:
            margins.append(float(game.home_points) - float(game.away_points))
        elif game.away_team == team:
            margins.append(float(game.away_points) - float(game.home_points))
    return sum(margins) / len(margins) if margins else 0.0


def _sos(team: str, games: Iterable[SourceGame], previous_cors: Mapping[str, float]) -> float:
    opponents: list[float] = []
    for game in games:
        if game.home_team == team:
            opponents.append(_number(previous_cors.get(game.away_team), FCS_CORS))
        elif game.away_team == team:
            opponents.append(_number(previous_cors.get(game.home_team), FCS_CORS))
    return round(sum(opponents) / len(opponents), 2) if opponents else 0.0


def _pythagorean_expected(team: str, games: Iterable[SourceGame]) -> float:
    points_for = points_against = 0.0
    count = 0
    for game in games:
        if game.home_team == team:
            points_for += float(game.home_points)
            points_against += float(game.away_points)
            count += 1
        elif game.away_team == team:
            points_for += float(game.away_points)
            points_against += float(game.home_points)
            count += 1
    if count == 0:
        return 0.0
    exponent = 2.37
    if points_against == 0:
        expectation = 1.0 if points_for > 0 else 0.0
    else:
        numerator = points_for**exponent
        expectation = numerator / (numerator + points_against**exponent)
    return round(expectation * count, 2)


def _cors_value(
    team: str,
    week: int,
    stats: Mapping[str, Any],
    games: tuple[SourceGame, ...],
    previous_cors: Mapping[str, float],
) -> float:
    wins = int(stats["wins"])
    losses = int(stats["losses"])
    denominator = wins + losses
    win_pct = round(wins / denominator, 2) if denominator else 0.0
    cors_max = 50 + (math.log10(31) * 4) * 1.2
    norm_sos = _sos(team, games, previous_cors) / cors_max
    win_pct_m = round((win_pct * 100) / 2, 2)
    mov = _margin_of_victory(team, games)
    if mov > 0:
        net_mov = math.log10(1 + mov) * 4
    else:
        net_mov = -math.log10(1 + abs(mov)) * 4
    scaled_sos = 0.8 + (norm_sos * (1.2 - 0.8))
    previous = _strict_number(previous_cors.get(team), f"previous ranking cors for {team!r}")
    # The week divisor is a deliberate v0.4.0 behavior.  Week zero is a real
    # scored checkpoint, so its divisor is explicitly one rather than a
    # carryover-only special case.
    value = (((win_pct_m + net_mov) * scaled_sos) + (previous / max(1, week))) / 2
    return round(value, 2)


def _sort_rankings(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -_number(row.get("cors")),
            -int(row.get("wins", 0)),
            int(row.get("losses", 0)),
            str(row.get("school", "")),
        ),
    )
    for rank, row in enumerate(ordered, 1):
        row["rank"] = rank
    return ordered


def _validate_previous_ranking(
    snapshot: SeasonSnapshot,
    previous_cors: Mapping[str, float] | None,
    previous_wins_vs_expected: Mapping[str, float] | None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Validate the previous checkpoint used by a numbered Week."""

    if previous_cors is None:
        raise RankingContractError(
            "numbered Week requires the immediately preceding ranking"
        )
    teams = _team_map(snapshot)
    expected = set(teams)
    cors = _strict_cors_mapping(previous_cors, "previous ranking cors")
    if set(cors) != expected:
        missing = sorted(expected - set(cors))
        extra = sorted(set(cors) - expected)
        detail: list[str] = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        raise RankingContractError(
            "previous ranking team set does not match season snapshot ("
            + ", ".join(detail)
            + ")"
        )
    wve = _strict_wve_mapping(previous_wins_vs_expected, "previous ranking wins_vs_expected")
    if wve and set(wve) != expected:
        missing = sorted(expected - set(wve))
        extra = sorted(set(wve) - expected)
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        raise RankingContractError(
            "previous ranking wins_vs_expected team set does not match snapshot ("
            + ", ".join(detail)
            + ")"
        )
    return cors, {school: wve.get(school, 0.0) for school in expected}


def _preseason_rows(
    snapshot: SeasonSnapshot,
    previous: PreviousFinal,
    *,
    regression_factor: float = REGRESSION_FACTOR,
) -> list[dict[str, Any]]:
    """Create PRESEASON rows without reading any scored Week 0 game."""

    factor = _strict_number(regression_factor, "regression_factor")
    teams = _team_map(snapshot)
    rows: list[dict[str, Any]] = []
    for team in _teams(snapshot):
        school = team.school
        carried = _strict_number(previous.cors[school], f"prior FINAL cors for {school!r}")
        regression = _strict_number(
            previous.wins_vs_expected.get(school, 0.0),
            f"prior FINAL wins_vs_expected for {school!r}",
        )
        cors = round(carried - (regression * factor), 2)
        if snapshot.year < 1996:
            record = "0-0-0"
        else:
            record = "0-0"
        rows.append(
            {
                "school": school,
                "conference": team.conference,
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "record": record,
                "win_pct": 0.0,
                "cors": cors,
                "mov": 0.0,
                "sos": 0.0,
                "expected_wins": 0.0,
                "wins_vs_expected": 0.0,
            }
        )
    return _sort_rankings(rows)


def preseason_ranking(
    snapshot: SeasonSnapshot,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    *,
    model_version: str = MODEL_VERSION,
    regression_factor: float = REGRESSION_FACTOR,
) -> list[dict[str, Any]]:
    """Calculate the distinct PRESEASON carryover checkpoint.

    PRESEASON never consumes scored Week 0 inputs.  Its only ratings input is
    the validated prior FINAL and its provisional ``1.75`` adjustment.
    """

    if model_version != MODEL_VERSION:
        raise ValueError(f"Unsupported CORS model version: {model_version}")
    _team_map(snapshot)
    _require_identified_prior_final(snapshot, previous_final)
    if snapshot.year == GENESIS_YEAR and previous_final is None:
        prior = validate_previous_final(snapshot, None, require=False)
    else:
        prior = validate_previous_final(snapshot, previous_final)
    return _preseason_rows(snapshot, prior, regression_factor=regression_factor)


def _ranking_for_scored_week(
    snapshot: SeasonSnapshot,
    week: int,
    previous_cors: Mapping[str, float],
    previous_wins_vs_expected: Mapping[str, float],
    *,
    model_version: str = MODEL_VERSION,
) -> list[dict[str, Any]]:
    """Calculate a normal scored checkpoint, including Week 0."""

    if model_version != MODEL_VERSION:
        raise ValueError(f"Unsupported CORS model version: {model_version}")
    stats_rows = records_for_week(snapshot, week)
    games = _games(snapshot, week)
    rows: list[dict[str, Any]] = []
    for stats in stats_rows:
        school = stats["school"]
        expected = _pythagorean_expected(school, games)
        wins_vs_expected = round(int(stats["wins"]) - expected, 2)
        cors = _cors_value(school, week, stats, games, previous_cors)
        rows.append(
            {
                **stats,
                "cors": cors,
                "mov": round(_margin_of_victory(school, games), 2),
                "sos": _sos(school, games, previous_cors),
                "expected_wins": expected,
                "wins_vs_expected": wins_vs_expected,
            }
        )
    return _sort_rankings(rows)


def ranking_for_week(
    snapshot: SeasonSnapshot,
    week: int,
    previous_cors: Mapping[str, float] | None = None,
    previous_wins_vs_expected: Mapping[str, float] | None = None,
    *,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    model_version: str = MODEL_VERSION,
    regression_factor: float = REGRESSION_FACTOR,
) -> list[dict[str, Any]]:
    """Calculate one numbered, scored Week through its completed boundary.

    Week 0 is a real scored week and receives divisor ``1`` in the CORS
    formula, but it can only be reached through a validated prior FINAL and
    its derived PRESEASON table.  Arbitrary ``previous_cors`` mappings are
    intentionally rejected for Week 0; they remain a compatibility input for
    later numbered weeks only.  The old all-team ``-10`` fallback is not used.
    """

    if model_version != MODEL_VERSION:
        raise ValueError(f"Unsupported CORS model version: {model_version}")
    try:
        week = int(week)
    except (TypeError, ValueError) as exc:
        raise RankingContractError("week must be a non-negative integer") from exc
    if week < 0:
        raise RankingContractError("week must be non-negative")
    _team_map(snapshot)
    if week > snapshot.complete_through_week:
        raise RankingContractError(
            f"Week {week} is beyond the completed snapshot boundary "
            f"({snapshot.complete_through_week})"
        )
    if week == 0:
        # A positional PreviousFinal is retained as a safe compatibility form;
        # a raw mapping in ``previous_cors`` is specifically not a prior FINAL.
        if isinstance(previous_cors, PreviousFinal):
            if previous_final is not None:
                raise RankingContractError(
                    "Week 0 received both previous_cors and previous_final"
                )
            previous_final = previous_cors
            previous_cors = None
        if previous_cors is not None or previous_wins_vs_expected is not None:
            raise RankingContractError(
                "Week 0 requires a validated previous_final; "
                "arbitrary previous-ranking mappings are not accepted"
            )
        if snapshot.year == GENESIS_YEAR and previous_final is None:
            prior = validate_previous_final(snapshot, None, require=False)
        else:
            _require_identified_prior_final(snapshot, previous_final)
            prior = validate_previous_final(snapshot, previous_final)
        preseason = _preseason_rows(
            snapshot,
            prior,
            regression_factor=regression_factor,
        )
        return _ranking_for_scored_week(
            snapshot,
            0,
            {row["school"]: float(row["cors"]) for row in preseason},
            {row["school"]: float(row["wins_vs_expected"]) for row in preseason},
            model_version=model_version,
        )
    if previous_final is not None:
        raise RankingContractError(
            "previous_final is only a Week 0 carryover input; "
            "later Weeks require their immediately preceding ranking"
        )
    cors, wve = _validate_previous_ranking(
        snapshot, previous_cors, previous_wins_vs_expected
    )
    return _ranking_for_scored_week(
        snapshot,
        week,
        cors,
        wve,
        model_version=model_version,
    )


def season_rankings(
    snapshot: SeasonSnapshot,
    target_week: int | None = None,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    *,
    model_version: str = MODEL_VERSION,
) -> dict[int, list[dict[str, Any]]]:
    """Calculate scored Week 0 through ``target_week`` using one snapshot.

    The separate PRESEASON table is available from :func:`preseason_ranking`.
    This compatibility return shape remains a mapping keyed by numbered Week,
    but it now follows the explicit lifecycle and validates carryover before
    any calculation.
    """

    if target_week is None:
        target_week = max(0, snapshot.complete_through_week)
    target_week = int(target_week)
    if target_week < 0:
        raise RankingContractError("target_week must be non-negative")
    if target_week > snapshot.complete_through_week:
        raise RankingContractError(
            "target_week cannot exceed snapshot complete_through_week"
        )
    _require_identified_prior_final(snapshot, previous_final)
    if snapshot.year == GENESIS_YEAR and previous_final is None:
        prior = validate_previous_final(snapshot, None, require=False)
    else:
        prior = validate_previous_final(snapshot, previous_final)
    preseason = _preseason_rows(snapshot, prior)
    prior_cors = {row["school"]: float(row["cors"]) for row in preseason}
    prior_wve = {row["school"]: float(row["wins_vs_expected"]) for row in preseason}
    rankings: dict[int, list[dict[str, Any]]] = {}
    for week in range(target_week + 1):
        # Use the private scored-week primitive so this already-validated
        # lifecycle loop cannot accidentally re-enter a public compatibility
        # path.  Week 0's inputs are the validated PRESEASON rows above.
        current = _ranking_for_scored_week(
            snapshot,
            week,
            prior_cors,
            prior_wve,
            model_version=model_version,
        )
        rankings[week] = current
        prior_cors = {row["school"]: float(row["cors"]) for row in current}
        prior_wve = {row["school"]: float(row["wins_vs_expected"]) for row in current}
    return rankings


def _previous_ranking_values(
    snapshot: SeasonSnapshot,
    previous_ranking: Sequence[Mapping[str, Any]] | Mapping[str, float] | PreviousFinal,
) -> tuple[dict[str, float], dict[str, float]]:
    """Normalize a prior numbered-Week table for the next Week."""

    if isinstance(previous_ranking, PreviousFinal):
        return _validate_previous_ranking(
            snapshot,
            previous_ranking.cors,
            previous_ranking.wins_vs_expected,
        )
    if isinstance(previous_ranking, Mapping):
        return _validate_previous_ranking(snapshot, previous_ranking, None)
    if not isinstance(previous_ranking, Sequence) or isinstance(
        previous_ranking, (str, bytes)
    ):
        raise RankingContractError(
            "previous ranking must be rows or a school-to-CORS mapping"
        )
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(previous_ranking):
        if not isinstance(row, Mapping):
            raise RankingContractError(f"previous ranking row {index} is not an object")
        rows.append(row)
    cors: dict[str, float] = {}
    wve: dict[str, float] = {}
    has_wve = False
    for index, row in enumerate(rows):
        school = row.get("school")
        if not isinstance(school, str) or not school.strip():
            raise RankingContractError(f"previous ranking row {index} has no school")
        if school in cors:
            raise RankingContractError(f"previous ranking contains duplicate school {school!r}")
        if "cors" not in row:
            raise RankingContractError(f"previous ranking row {index} has no cors value")
        cors[school] = _strict_number(row.get("cors"), f"previous ranking row {index} cors")
        if "wins_vs_expected" in row and row.get("wins_vs_expected") not in (None, ""):
            wve[school] = _strict_number(
                row.get("wins_vs_expected"),
                f"previous ranking row {index} wins_vs_expected",
            )
            has_wve = True
    if has_wve:
        wve = {school: wve.get(school, 0.0) for school in cors}
    return _validate_previous_ranking(snapshot, cors, wve)


def week_ranking(
    snapshot: SeasonSnapshot,
    week: int,
    previous_ranking: Sequence[Mapping[str, Any]] | Mapping[str, float] | PreviousFinal | None = None,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    *,
    model_version: str = MODEL_VERSION,
) -> list[dict[str, Any]]:
    """Calculate an explicit numbered Week checkpoint.

    ``previous_ranking`` should normally be the immediately preceding scored
    Week.  For Week 0, callers may pass ``previous_final`` and the function
    derives PRESEASON first; this keeps the lifecycle boundary explicit while
    making the artifact builder straightforward.
    """

    try:
        week = int(week)
    except (TypeError, ValueError) as exc:
        raise RankingContractError("week must be a non-negative integer") from exc
    if week < 0:
        raise RankingContractError("week must be non-negative")
    _team_map(snapshot)
    if week > snapshot.complete_through_week:
        raise RankingContractError(
            f"Week {week} is beyond the completed snapshot boundary "
            f"({snapshot.complete_through_week})"
        )
    if week == 0:
        if previous_ranking is not None:
            raise RankingContractError(
                "Week 0 cannot accept an arbitrary previous ranking; "
                "provide previous_final so PRESEASON is derived and validated"
            )
        if snapshot.year == GENESIS_YEAR and previous_final is None:
            prior = validate_previous_final(snapshot, None, require=False)
        else:
            _require_identified_prior_final(snapshot, previous_final)
            prior = validate_previous_final(snapshot, previous_final)
        preseason = _preseason_rows(snapshot, prior)
        return _ranking_for_scored_week(
            snapshot,
            0,
            {row["school"]: float(row["cors"]) for row in preseason},
            {row["school"]: float(row["wins_vs_expected"]) for row in preseason},
            model_version=model_version,
        )
    if previous_ranking is None:
        if previous_final is not None:
            all_rankings = season_rankings(
                snapshot,
                target_week=week - 1,
                previous_final=previous_final,
                model_version=model_version,
            )
            previous_ranking = all_rankings[week - 1]
        else:
            raise RankingContractError(
                "numbered Week requires its immediately preceding ranking"
            )
    cors, wve = _previous_ranking_values(snapshot, previous_ranking)
    return ranking_for_week(
        snapshot,
        week,
        cors,
        wve,
        model_version=model_version,
    )


def final_ranking(
    snapshot: SeasonSnapshot,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    *,
    model_version: str = MODEL_VERSION,
) -> list[dict[str, Any]]:
    """Calculate FINAL only from a complete historical Season Snapshot."""

    if not season_is_complete(snapshot):
        raise RankingContractError(
            "FINAL requires a complete snapshot with every scheduled game scored"
        )
    scheduled_end = scheduled_season_end_week(snapshot)
    rankings = season_rankings(
        snapshot,
        target_week=scheduled_end,
        previous_final=previous_final,
        model_version=model_version,
    )
    return rankings[scheduled_end]


def ranking_for_phase(
    snapshot: SeasonSnapshot,
    phase: RankingPhase | str,
    week: int | None = None,
    previous_final: PreviousFinal | Mapping[str, float] | str | Path | None = None,
    previous_ranking: Sequence[Mapping[str, Any]] | Mapping[str, float] | PreviousFinal | None = None,
    *,
    through_week: int | None = None,
    model_version: str = MODEL_VERSION,
) -> list[dict[str, Any]]:
    """Dispatch one explicit PRESEASON, numbered Week, or FINAL calculation."""

    if through_week is not None:
        if week is not None and int(week) != int(through_week):
            raise RankingContractError("week and through_week disagree")
        week = through_week
    try:
        selected = phase if isinstance(phase, RankingPhase) else RankingPhase(str(phase).lower())
    except ValueError as exc:
        raise RankingContractError(
            "phase must be PRESEASON, WEEK, or FINAL"
        ) from exc
    if selected is RankingPhase.PRESEASON:
        if week is not None:
            raise RankingContractError("PRESEASON does not accept a week")
        return preseason_ranking(
            snapshot, previous_final, model_version=model_version
        )
    if selected is RankingPhase.FINAL:
        if week is not None:
            raise RankingContractError("FINAL does not accept a week")
        return final_ranking(
            snapshot, previous_final, model_version=model_version
        )
    if week is None:
        raise RankingContractError("WEEK requires a non-negative week")
    return week_ranking(
        snapshot,
        week,
        previous_ranking=previous_ranking,
        previous_final=previous_final,
        model_version=model_version,
    )


# Descriptive aliases make the lifecycle seam discoverable without removing
# the established helper names used by older integrations.
calculate_preseason = preseason_ranking
calculate_week = week_ranking
calculate_final = final_ranking
calculate_phase = ranking_for_phase
calculate_ranking_phase = ranking_for_phase
preseason = preseason_ranking
week = week_ranking
final = final_ranking
ranking_phase = ranking_for_phase


def ranking_engine(
    snapshot: SeasonSnapshot,
    week: int,
    previous_final: PreviousFinal | Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Small functional alias for integrations that prefer a verb-free name."""

    return ranking_for_week(snapshot, week, previous_final=previous_final)


def spreads_for_week(
    snapshot: SeasonSnapshot,
    week: int,
    rankings: Sequence[Mapping[str, Any]],
    *,
    hfa: float = HFA,
) -> list[dict[str, Any]]:
    """Produce finite spreads for valid FBS-vs-FBS games in one week.

    The prediction uses the previous completed ranking for a game's week when
    callers provide it.  Scheduled, unscored games remain eligible for a
    spread; they do not affect records or CORS.
    """

    rating = {str(row["school"]): _number(row.get("cors")) for row in rankings}
    valid_teams = set(rating)
    rows: list[dict[str, Any]] = []
    for game in sorted(
        (
            game for game in snapshot.games
            if int(game.week) == int(week) and not is_explicit_non_played(game)
        ),
        key=lambda value: (
            value.home_team,
            value.away_team,
            int(value.week),
        ),
    ):
        if game.home_team not in valid_teams or game.away_team not in valid_teams:
            continue
        value = (rating[game.home_team] - rating[game.away_team])
        if not game.neutral_site:
            value += float(hfa)
        value = round(value * 2) / 2
        # Keep the legacy presentation while exposing a numeric field for
        # machine validation.
        sign = "-" if value > 0 else "+"
        display = f"{game.home_team} {sign}{abs(value):g}"
        rows.append(
            {
                "week": int(game.week),
                "home_team": game.home_team,
                "away_team": game.away_team,
                "neutral_site": bool(game.neutral_site),
                "home_cors": rating[game.home_team],
                "away_cors": rating[game.away_team],
                "spread_value": float(value),
                "spread": display,
            }
        )
    return rows


def load_previous_final_rows(path: str | Path | None) -> list[dict[str, Any]]:
    """Read legacy HTML or machine-readable final ranking rows.

    Old final pages contain a pandas table after a simple HTML title block;
    JSON inputs may be either a list of rows or ``{"rankings": [...]}``.
    """

    if path is None:
        return []
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        import json

        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, Mapping):
            value = value.get("rankings", value.get("rows", []))
        if not isinstance(value, list):
            raise ValueError(f"Previous final JSON must contain a row list: {path}")
        rows: list[dict[str, Any]] = []
        for index, row in enumerate(value):
            if not isinstance(row, Mapping):
                raise RankingContractError(
                    f"Previous final JSON row {index} is not an object: {path}"
                )
            rows.append(dict(row))
        return rows
    # Legacy generated pages predate an explicit encoding and contain a few
    # Latin-1 school names.  Replacement keeps table structure readable while
    # avoiding any mutation of the Published Site.
    html = path.read_text(encoding="utf-8", errors="replace")
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError as exc:
        raise ValueError(f"Previous final has no ranking table: {path}") from exc
    if not tables:
        return []
    frame = tables[0]
    frame = frame.loc[:, [column for column in frame.columns if str(column) != "Unnamed: 0"]]
    return frame.where(pd.notna(frame), None).to_dict(orient="records")


def load_previous_final(path: str | Path | None) -> dict[str, float]:
    """Return prior final CORS values keyed by school."""

    return dict(load_previous_final_model(path).cors)


def load_previous_final_model(path: str | Path | None) -> PreviousFinal:
    """Load both carryover CORS and optional regression inputs strictly.

    Parsing intentionally happens before a caller can calculate a ranking, and
    duplicate schools/non-finite values remain observable rather than being
    collapsed into a dictionary or replaced by ``-10``/zero.
    """

    rows = load_previous_final_rows(path)
    if path is None:
        return PreviousFinal(cors={}, wins_vs_expected={})
    model = _previous_final_from_rows(rows, source=path)
    # Generated public FINAL paths carry enough provenance to reject an
    # accidentally supplied season/classification even when team names happen
    # to overlap.  Ad-hoc JSON fixtures remain provenance-neutral and are
    # checked against the snapshot's exact team set by validate_previous_final.
    name = Path(path).name
    match = re.fullmatch(r"(\d{4})_FINAL_([^_]+)_cors\.html", name)
    if match:
        return PreviousFinal(
            cors=model.cors,
            wins_vs_expected=model.wins_vs_expected,
            year=int(match.group(1)),
            classification=match.group(2),
        )
    return model


# Compatibility aliases kept intentionally small and pure.
compute_records = records_for_week
compute_ranking = ranking_for_week
compute_rankings = season_rankings
compute_spreads = spreads_for_week
parse_previous_final = load_previous_final
calculate_records = records_for_week
calculate_ranking = ranking_for_week
calculate_rankings = season_rankings
calculate_season = season_rankings
calculate_spreads = spreads_for_week
load_prior_final = load_previous_final


__all__ = [
    "CORS_VERSION",
    "MODEL_VERSION",
    "FCS_CORS",
    "HFA",
    "REGRESSION_FACTOR",
    "GENESIS_YEAR",
    "TIE_BREAK",
    "RankingContractError",
    "RankingPhase",
    "PreviousFinal",
    "validate_previous_final",
    "RankingEngine",
    "CORSRankingEngine",
    "completed_games",
    "scheduled_games",
    "scheduled_season_end_week",
    "scheduled_end_week",
    "season_is_complete",
    "is_season_complete",
    "season_end_week",
    "end_week",
    "records_for_week",
    "ranking_for_week",
    "preseason_ranking",
    "week_ranking",
    "final_ranking",
    "ranking_for_phase",
    "calculate_preseason",
    "calculate_week",
    "calculate_final",
    "calculate_phase",
    "calculate_ranking_phase",
    "preseason",
    "week",
    "final",
    "ranking_phase",
    "season_rankings",
    "spreads_for_week",
    "load_previous_final",
    "load_previous_final_rows",
    "load_previous_final_model",
    "compute_records",
    "compute_ranking",
    "compute_rankings",
    "compute_spreads",
    "parse_previous_final",
    "calculate_records",
    "calculate_ranking",
    "calculate_rankings",
    "calculate_season",
    "calculate_spreads",
    "load_prior_final",
]
