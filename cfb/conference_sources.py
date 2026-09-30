"""Validated, season-specific evidence for conference reference views.

This module is deliberately separate from rendering and from the CORS engine.
The provider snapshot contains teams, scores and chronology, but it does not
reliably carry the conference rule facts needed by standings or projections.
``ConferenceSupplement`` is the small immutable bridge for those facts.  It
must be validated against the exact snapshot before any derived view consumes
it.

The supplement is a source contract, not a 2025 configuration file.  Fixture
callers can construct one in memory; production adapters can later load the
same typed values from a retained, hash-checked evidence package.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for static type checkers
    from .season_snapshot import SeasonSnapshot
    from .season_source import SourceGame


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f<>]+$")


class ConferenceSourceError(ValueError):
    """Raised when a conference evidence supplement is not trustworthy."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ConferenceSourceError(f"{field} must be text")
    value = value.strip()
    if not value or _SAFE_TEXT.fullmatch(value) is None:
        raise ConferenceSourceError(f"{field} contains unsafe or empty text")
    return value


def _identity(value: Any, field: str) -> str:
    value = _text(value, field)
    if _SAFE_ID.fullmatch(value) is None:
        raise ConferenceSourceError(f"{field} is not a safe identity")
    return value


def _sha256(value: Any, field: str) -> str:
    value = _text(value, field).lower()
    if _SHA256.fullmatch(value) is None:
        raise ConferenceSourceError(f"{field} must be a SHA-256 digest")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConferenceSourceError(f"{field} must be an ISO timestamp") from exc
    else:
        raise ConferenceSourceError(f"{field} must be an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConferenceSourceError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field)


def _calendar_date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ConferenceSourceError(f"{field} must be an ISO date") from exc
    raise ConferenceSourceError(f"{field} must be an ISO date")


def _optional_date(value: Any, field: str) -> date | None:
    if value is None:
        return None
    return _calendar_date(value, field)


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ConferenceSourceError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ConferenceSourceError(f"{field} must be an integer")


def _tuple_text(values: Iterable[Any], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ConferenceSourceError(f"{field} must be a sequence of text")
    try:
        result = tuple(_text(item, f"{field} item") for item in values)
    except TypeError as exc:
        raise ConferenceSourceError(f"{field} must be a sequence of text") from exc
    if len(set(result)) != len(result):
        raise ConferenceSourceError(f"{field} contains duplicate values")
    return result


def _tuple_ids(values: Iterable[Any], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ConferenceSourceError(f"{field} must be a sequence of identities")
    try:
        result = tuple(_identity(item, f"{field} item") for item in values)
    except TypeError as exc:
        raise ConferenceSourceError(f"{field} must be a sequence of identities") from exc
    if not result:
        raise ConferenceSourceError(f"{field} cannot be empty")
    if len(set(result)) != len(result):
        raise ConferenceSourceError(f"{field} contains duplicate values")
    return result


@dataclass(frozen=True)
class SourceEvidence:
    """One hash-pinned source reference and its chronology."""

    evidence_id: str
    url: str
    source_sha256: str
    published_at: datetime
    effective_from: date
    known_at: datetime
    effective_to: date | None = None
    retrieved_at: datetime | None = None
    locator: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _identity(self.evidence_id, "evidence_id"))
        url = _text(self.url, "evidence url")
        if not (url.startswith("https://") or url.startswith("http://")):
            raise ConferenceSourceError("evidence url must use http or https")
        object.__setattr__(self, "url", url)
        object.__setattr__(
            self, "source_sha256", _sha256(self.source_sha256, "source_sha256")
        )
        published = _timestamp(self.published_at, "published_at")
        known = _timestamp(self.known_at, "known_at")
        if published > known:
            raise ConferenceSourceError("published_at cannot be later than known_at")
        object.__setattr__(self, "published_at", published)
        object.__setattr__(self, "known_at", known)
        effective_from = _calendar_date(self.effective_from, "effective_from")
        effective_to = _optional_date(self.effective_to, "effective_to")
        if effective_to is not None and effective_to < effective_from:
            raise ConferenceSourceError("effective_to cannot precede effective_from")
        object.__setattr__(self, "effective_from", effective_from)
        object.__setattr__(self, "effective_to", effective_to)
        retrieved = _optional_timestamp(self.retrieved_at, "retrieved_at")
        if retrieved is not None and retrieved < known:
            raise ConferenceSourceError("retrieved_at cannot precede known_at")
        object.__setattr__(self, "retrieved_at", retrieved)
        if self.locator is not None:
            object.__setattr__(self, "locator", _text(self.locator, "locator"))


@dataclass(frozen=True)
class ConferenceMember:
    """A complete snapshot team membership assertion."""

    team: str
    conference: str | None
    independent: bool
    evidence_ids: tuple[str, ...]
    classification: str = "FBS"

    def __post_init__(self) -> None:
        object.__setattr__(self, "team", _text(self.team, "member team"))
        if self.conference is not None:
            object.__setattr__(self, "conference", _text(self.conference, "member conference"))
        if not isinstance(self.independent, bool):
            raise ConferenceSourceError("member independent must be boolean")
        if self.independent and self.conference is not None:
            raise ConferenceSourceError("independent members cannot have a conference")
        if not self.independent and self.conference is None:
            raise ConferenceSourceError("conference members require a conference")
        object.__setattr__(self, "classification", _text(self.classification, "classification"))
        object.__setattr__(self, "evidence_ids", _tuple_ids(self.evidence_ids, "member evidence_ids"))


@dataclass(frozen=True)
class ConferenceGameDesignation:
    """Explicit league/qualification classification for one provider game."""

    provider_id: str
    home_team: str
    away_team: str
    conference_game: bool
    counts_for_standings: bool
    title_game: bool
    conference: str | None
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _identity(self.provider_id, "provider_id"))
        home = _text(self.home_team, "game home_team")
        away = _text(self.away_team, "game away_team")
        if home == away:
            raise ConferenceSourceError("a game cannot have the same home and away team")
        object.__setattr__(self, "home_team", home)
        object.__setattr__(self, "away_team", away)
        for field in ("conference_game", "counts_for_standings", "title_game"):
            if not isinstance(getattr(self, field), bool):
                raise ConferenceSourceError(f"game {field} must be boolean")
        if self.conference is not None:
            object.__setattr__(self, "conference", _text(self.conference, "game conference"))
        if self.conference_game and self.conference is None:
            raise ConferenceSourceError("conference games require a conference")
        if not self.conference_game and self.conference is not None:
            raise ConferenceSourceError("nonconference games cannot name a conference")
        if self.title_game and not self.conference_game:
            raise ConferenceSourceError("a title game must be a conference game")
        if self.title_game and self.counts_for_standings:
            raise ConferenceSourceError("title games cannot count for qualification standings")
        if self.counts_for_standings and not self.conference_game:
            raise ConferenceSourceError("nonconference games cannot count for standings")
        object.__setattr__(self, "evidence_ids", _tuple_ids(self.evidence_ids, "game evidence_ids"))


@dataclass(frozen=True)
class DivisionRule:
    name: str
    members: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "division name"))
        object.__setattr__(self, "members", _tuple_text(self.members, "division members"))
        if not self.members:
            raise ConferenceSourceError("division members cannot be empty")
        object.__setattr__(self, "evidence_ids", _tuple_ids(self.evidence_ids, "division evidence_ids"))


@dataclass(frozen=True)
class EligibilityRule:
    team: str
    eligible: bool
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "team", _text(self.team, "eligibility team"))
        if not isinstance(self.eligible, bool):
            raise ConferenceSourceError("eligibility eligible must be boolean")
        object.__setattr__(self, "evidence_ids", _tuple_ids(self.evidence_ids, "eligibility evidence_ids"))


@dataclass(frozen=True)
class SitePolicy:
    """Source-backed championship site treatment for a conference."""

    mode: str
    fixed_host: str | None
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        mode = _text(self.mode, "site mode").lower()
        if mode not in {"neutral", "seed_hosted", "fixed_hosted", "unresolved", "none"}:
            raise ConferenceSourceError("site mode is unsupported")
        object.__setattr__(self, "mode", mode)
        if self.fixed_host is not None:
            object.__setattr__(self, "fixed_host", _text(self.fixed_host, "fixed_host"))
        if mode == "fixed_hosted" and self.fixed_host is None:
            raise ConferenceSourceError("fixed_hosted policy requires fixed_host")
        if mode != "fixed_hosted" and self.fixed_host is not None:
            raise ConferenceSourceError("fixed_host is only valid for fixed_hosted policy")
        object.__setattr__(self, "evidence_ids", _tuple_ids(self.evidence_ids, "site evidence_ids"))


@dataclass(frozen=True)
class ChampionshipRule:
    """Participant-selection rule; it intentionally has no tiebreak engine."""

    selection: str
    eligibility: tuple[EligibilityRule, ...]
    divisions: tuple[DivisionRule, ...]
    site: SitePolicy
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        selection = _text(self.selection, "championship selection").lower()
        if selection not in {"none", "top_two", "division_leaders"}:
            raise ConferenceSourceError("championship selection is unsupported")
        object.__setattr__(self, "selection", selection)
        if not isinstance(self.eligibility, tuple):
            object.__setattr__(self, "eligibility", tuple(self.eligibility))
        if not isinstance(self.divisions, tuple):
            object.__setattr__(self, "divisions", tuple(self.divisions))
        if any(not isinstance(item, EligibilityRule) for item in self.eligibility):
            raise ConferenceSourceError("championship eligibility must use EligibilityRule values")
        if any(not isinstance(item, DivisionRule) for item in self.divisions):
            raise ConferenceSourceError("championship divisions must use DivisionRule values")
        if not isinstance(self.site, SitePolicy):
            raise ConferenceSourceError("championship site must use a SitePolicy value")
        object.__setattr__(self, "evidence_ids", _tuple_ids(self.evidence_ids, "championship evidence_ids"))
        if selection == "none":
            if self.divisions or self.eligibility:
                raise ConferenceSourceError("no-title rule cannot carry selection members")
            if self.site.mode != "none":
                raise ConferenceSourceError("no-title rule requires site mode none")
        elif self.site.mode == "none":
            raise ConferenceSourceError("title rules cannot use site mode none")
        if selection == "top_two" and self.divisions:
            raise ConferenceSourceError("top_two rule cannot carry divisions")
        if selection == "division_leaders" and not self.divisions:
            raise ConferenceSourceError("division_leaders rule requires divisions")


@dataclass(frozen=True)
class ConferenceRule:
    conference: str
    standings_criterion: str
    championship: ChampionshipRule
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "conference", _text(self.conference, "rule conference"))
        criterion = _text(self.standings_criterion, "standings_criterion").lower()
        if criterion != "winning_percentage":
            raise ConferenceSourceError("only winning_percentage is supported as a primary criterion")
        object.__setattr__(self, "standings_criterion", criterion)
        if not isinstance(self.championship, ChampionshipRule):
            raise ConferenceSourceError("conference championship must be a ChampionshipRule")
        object.__setattr__(self, "evidence_ids", _tuple_ids(self.evidence_ids, "rule evidence_ids"))


@dataclass(frozen=True)
class DatedConfirmation:
    """An official resolution that may be used only after ``known_at``."""

    confirmation_id: str
    conference: str
    participants: tuple[str, ...]
    known_at: datetime
    evidence_ids: tuple[str, ...]
    kind: str = "participants"
    site_mode: str | None = None
    host_team: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "confirmation_id", _identity(self.confirmation_id, "confirmation_id"))
        object.__setattr__(self, "conference", _text(self.conference, "confirmation conference"))
        object.__setattr__(self, "participants", _tuple_text(self.participants, "confirmation participants"))
        if len(self.participants) != 2:
            raise ConferenceSourceError("a championship confirmation requires two participants")
        object.__setattr__(self, "known_at", _timestamp(self.known_at, "confirmation known_at"))
        object.__setattr__(self, "evidence_ids", _tuple_ids(self.evidence_ids, "confirmation evidence_ids"))
        kind = _text(self.kind, "confirmation kind").lower()
        if kind != "participants":
            raise ConferenceSourceError("unsupported confirmation kind")
        object.__setattr__(self, "kind", kind)
        if self.site_mode is not None:
            site_mode = _text(self.site_mode, "confirmation site_mode").lower()
            if site_mode not in {"neutral", "seed_hosted", "fixed_hosted", "unresolved"}:
                raise ConferenceSourceError("unsupported confirmation site_mode")
            object.__setattr__(self, "site_mode", site_mode)
        if self.host_team is not None:
            object.__setattr__(self, "host_team", _text(self.host_team, "confirmation host_team"))
        if self.site_mode == "fixed_hosted" and self.host_team is None:
            raise ConferenceSourceError("fixed_hosted confirmation requires host_team")
        if self.site_mode in {"neutral", "unresolved"} and self.host_team is not None:
            raise ConferenceSourceError("confirmation host_team conflicts with site_mode")


@dataclass(frozen=True)
class ConferenceSupplement:
    """Immutable source supplement bound to one season snapshot identity."""

    season: int
    snapshot_checksum: str
    content_identity: str
    evidence: tuple[SourceEvidence, ...]
    members: tuple[ConferenceMember, ...]
    games: tuple[ConferenceGameDesignation, ...]
    rules: tuple[ConferenceRule, ...]
    confirmations: tuple[DatedConfirmation, ...] = ()
    declared_checksum: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "season", _strict_int(self.season, "supplement season"))
        if self.season < 1900:
            raise ConferenceSourceError("supplement season is outside supported range")
        object.__setattr__(
            self, "snapshot_checksum", _sha256(self.snapshot_checksum, "snapshot_checksum")
        )
        object.__setattr__(self, "content_identity", _identity(self.content_identity, "content_identity"))
        for field, cls in (
            ("evidence", SourceEvidence),
            ("members", ConferenceMember),
            ("games", ConferenceGameDesignation),
            ("rules", ConferenceRule),
            ("confirmations", DatedConfirmation),
        ):
            value = getattr(self, field)
            if not isinstance(value, tuple):
                value = tuple(value)
                object.__setattr__(self, field, value)
            if any(not isinstance(item, cls) for item in value):
                raise ConferenceSourceError(f"supplement {field} contain unexpected values")
        if not self.evidence:
            raise ConferenceSourceError("supplement requires at least one evidence reference")
        if not self.members:
            raise ConferenceSourceError("supplement requires complete membership")
        if not self.games:
            raise ConferenceSourceError("supplement requires complete game designations")
        if not self.rules and any(not member.independent for member in self.members):
            raise ConferenceSourceError("conference members require conference rules")
        if self.declared_checksum is not None:
            object.__setattr__(
                self, "declared_checksum", _sha256(self.declared_checksum, "declared_checksum")
            )
        if _strict_int(self.schema_version, "supplement schema_version") != 1:
            raise ConferenceSourceError("unsupported supplement schema version")

    @property
    def checksum(self) -> str:
        return _canonical_digest(_supplement_payload(self))

    def validate(self, snapshot: "SeasonSnapshot") -> "ValidatedConferenceSupplement":
        return validate_conference_supplement(self, snapshot)


@dataclass(frozen=True)
class ValidatedConferenceSupplement:
    """The exact supplement identity after snapshot-bound validation."""

    supplement: ConferenceSupplement
    supplement_checksum: str
    snapshot_checksum: str

    @property
    def season(self) -> int:
        return self.supplement.season

    @property
    def content_identity(self) -> str:
        return self.supplement.content_identity


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _time_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _evidence_payload(item: SourceEvidence) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "url": item.url,
        "source_sha256": item.source_sha256,
        "published_at": _time_text(item.published_at),
        "effective_from": _date_text(item.effective_from),
        "effective_to": _date_text(item.effective_to),
        "known_at": _time_text(item.known_at),
        "retrieved_at": _time_text(item.retrieved_at),
        "locator": item.locator,
    }


def _supplement_payload(supplement: ConferenceSupplement) -> dict[str, Any]:
    return {
        "schema_version": supplement.schema_version,
        "season": supplement.season,
        "snapshot_checksum": supplement.snapshot_checksum,
        "content_identity": supplement.content_identity,
        "evidence": [_evidence_payload(item) for item in supplement.evidence],
        "members": [
            {
                "team": item.team,
                "conference": item.conference,
                "independent": item.independent,
                "classification": item.classification,
                "evidence_ids": list(item.evidence_ids),
            }
            for item in supplement.members
        ],
        "games": [
            {
                "provider_id": item.provider_id,
                "home_team": item.home_team,
                "away_team": item.away_team,
                "conference_game": item.conference_game,
                "counts_for_standings": item.counts_for_standings,
                "title_game": item.title_game,
                "conference": item.conference,
                "evidence_ids": list(item.evidence_ids),
            }
            for item in supplement.games
        ],
        "rules": [
            {
                "conference": item.conference,
                "standings_criterion": item.standings_criterion,
                "evidence_ids": list(item.evidence_ids),
                "championship": {
                    "selection": item.championship.selection,
                    "eligibility": [
                        {
                            "team": row.team,
                            "eligible": row.eligible,
                            "evidence_ids": list(row.evidence_ids),
                        }
                        for row in item.championship.eligibility
                    ],
                    "divisions": [
                        {
                            "name": row.name,
                            "members": list(row.members),
                            "evidence_ids": list(row.evidence_ids),
                        }
                        for row in item.championship.divisions
                    ],
                    "site": {
                        "mode": item.championship.site.mode,
                        "fixed_host": item.championship.site.fixed_host,
                        "evidence_ids": list(item.championship.site.evidence_ids),
                    },
                    "evidence_ids": list(item.championship.evidence_ids),
                },
            }
            for item in supplement.rules
        ],
        "confirmations": [
            {
                "confirmation_id": item.confirmation_id,
                "conference": item.conference,
                "participants": list(item.participants),
                "known_at": _time_text(item.known_at),
                "evidence_ids": list(item.evidence_ids),
                "kind": item.kind,
                "site_mode": item.site_mode,
                "host_team": item.host_team,
            }
            for item in supplement.confirmations
        ],
    }


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_game_index(snapshot: "SeasonSnapshot") -> dict[str, Any]:
    games: dict[str, Any] = {}
    for index, game in enumerate(snapshot.games):
        provider_id = getattr(game, "provider_id", None)
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ConferenceSourceError(f"snapshot game {index} has no stable provider_id")
        _identity(provider_id, f"snapshot game {index} provider_id")
        if provider_id in games:
            raise ConferenceSourceError(f"snapshot contains duplicate provider_id {provider_id!r}")
        games[provider_id] = game
    return games


def snapshot_content_checksum(snapshot: "SeasonSnapshot") -> str:
    """Recompute the canonical Season Snapshot checksum from immutable content."""

    try:
        from .season_snapshot import _checksum
    except ImportError:  # pragma: no cover - direct cfb/ execution
        from season_snapshot import _checksum
    metadata = dict(getattr(snapshot, "metadata", {}) or {})
    state = {
        "schema_version": metadata.get("schema_version", 3),
        "sport": getattr(snapshot, "sport", None),
        "classification": getattr(snapshot, "classification", None),
        "year": getattr(snapshot, "year", None),
        "teams_fetched_at": metadata.get("teams_fetched_at"),
        "games_fetched_at": metadata.get("games_fetched_at"),
        "complete_through_week": metadata.get(
            "complete_through_week", getattr(snapshot, "complete_through_week", -1)
        ),
        # Schema 4 includes these provenance values in the canonical digest.
        # Carry them through so a supplement cannot bind to a snapshot whose
        # calendar or correction identity changed.
        "calendar_provenance": metadata.get("calendar_provenance"),
        "correction_registry_provenance": metadata.get(
            "correction_registry_provenance"
        ),
        "migration_provenance": metadata.get("migration_provenance"),
    }
    try:
        return _checksum(state, tuple(snapshot.teams), tuple(snapshot.games))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ConferenceSourceError("snapshot content cannot be checksummed") from exc


def _require_evidence(ids: Iterable[str], evidence: Mapping[str, SourceEvidence], field: str) -> None:
    for evidence_id in ids:
        if evidence_id not in evidence:
            raise ConferenceSourceError(f"{field} references unknown evidence {evidence_id!r}")


def validate_conference_supplement(
    supplement: ConferenceSupplement,
    snapshot: "SeasonSnapshot",
) -> ValidatedConferenceSupplement:
    """Validate all identities and completeness against one exact snapshot.

    The function intentionally receives the snapshot object rather than loose
    team/game lists.  This keeps the binding point explicit and prevents a
    caller from claiming complete coverage against a different roster or game
    set.
    """

    if not isinstance(supplement, ConferenceSupplement):
        raise ConferenceSourceError("supplement must be a ConferenceSupplement")
    season = _strict_int(getattr(snapshot, "year", None), "snapshot year")
    if supplement.season != season:
        raise ConferenceSourceError("supplement season does not match snapshot")
    if str(getattr(snapshot, "sport", "")).lower() != "cfb":
        raise ConferenceSourceError("conference supplement requires a CFB snapshot")
    if str(getattr(snapshot, "classification", "")).upper() != "FBS":
        raise ConferenceSourceError("conference supplement requires an FBS snapshot")
    stored_snapshot_checksum = _sha256(getattr(snapshot, "checksum", None), "snapshot checksum")
    computed_snapshot_checksum = snapshot_content_checksum(snapshot)
    if stored_snapshot_checksum != computed_snapshot_checksum:
        raise ConferenceSourceError("snapshot stored checksum does not match its content")
    if supplement.snapshot_checksum != computed_snapshot_checksum:
        raise ConferenceSourceError("supplement snapshot checksum does not match snapshot")
    computed = supplement.checksum
    if supplement.declared_checksum is not None and supplement.declared_checksum != computed:
        raise ConferenceSourceError("supplement declared_checksum does not match content")

    evidence: dict[str, SourceEvidence] = {}
    urls: dict[str, str] = {}
    for item in supplement.evidence:
        if item.evidence_id in evidence:
            raise ConferenceSourceError(f"duplicate evidence_id {item.evidence_id!r}")
        if item.url in urls and urls[item.url] != item.source_sha256:
            raise ConferenceSourceError("one source URL has conflicting byte identities")
        evidence[item.evidence_id] = item
        urls[item.url] = item.source_sha256

    members: dict[str, ConferenceMember] = {}
    for item in supplement.members:
        if item.team in members:
            raise ConferenceSourceError(f"duplicate membership for {item.team!r}")
        if item.classification.upper() != "FBS":
            raise ConferenceSourceError(f"membership {item.team!r} is not FBS")
        _require_evidence(item.evidence_ids, evidence, f"membership {item.team!r}")
        members[item.team] = item
    snapshot_teams = tuple(getattr(snapshot, "teams", ()))
    expected_teams: dict[str, Any] = {}
    for index, team in enumerate(snapshot_teams):
        name = getattr(team, "school", None)
        if not isinstance(name, str) or not name.strip():
            raise ConferenceSourceError(f"snapshot team {index} has no stable school")
        if name in expected_teams:
            raise ConferenceSourceError(f"snapshot has duplicate team {name!r}")
        expected_teams[name] = team
    if set(members) != set(expected_teams):
        missing = sorted(set(expected_teams) - set(members))
        extra = sorted(set(members) - set(expected_teams))
        raise ConferenceSourceError(
            f"membership is not complete (missing={missing}, extra={extra})"
        )
    for name, member in members.items():
        snapshot_conference = str(getattr(expected_teams[name], "conference", "")).strip()
        if member.independent:
            if snapshot_conference != "FBS Independents":
                raise ConferenceSourceError(
                    f"independent membership disagrees with snapshot for {name!r}"
                )
        elif member.conference != snapshot_conference:
            raise ConferenceSourceError(
                f"membership conference disagrees with snapshot for {name!r}"
            )

    rules: dict[str, ConferenceRule] = {}
    for item in supplement.rules:
        if item.conference in rules:
            raise ConferenceSourceError(f"duplicate rule for {item.conference!r}")
        if item.conference in {member.conference for member in members.values() if member.conference}:
            rules[item.conference] = item
        else:
            raise ConferenceSourceError(f"rule names no member conference {item.conference!r}")
        _require_evidence(item.evidence_ids, evidence, f"rule {item.conference}")
        champ = item.championship
        _require_evidence(champ.evidence_ids, evidence, f"championship {item.conference}")
        for row in champ.eligibility:
            _require_evidence(row.evidence_ids, evidence, f"eligibility {row.team}")
        for division in champ.divisions:
            _require_evidence(division.evidence_ids, evidence, f"division {division.name}")
        _require_evidence(champ.site.evidence_ids, evidence, f"site {item.conference}")

    member_conferences = {member.conference for member in members.values() if member.conference}
    if member_conferences != set(rules):
        raise ConferenceSourceError("every conference member group requires exactly one rule")

    for conference, rule in rules.items():
        conference_members = {
            name for name, member in members.items() if member.conference == conference
        }
        champ = rule.championship
        eligibility = {row.team: row for row in champ.eligibility}
        if champ.selection == "none":
            if eligibility:
                raise ConferenceSourceError("no-title rule cannot define eligibility")
        elif set(eligibility) != conference_members:
            raise ConferenceSourceError(
                f"eligibility for {conference} is not complete"
            )
        elif any(team not in conference_members for team in eligibility):
            raise ConferenceSourceError("eligibility names a team outside its conference")
        division_names: set[str] = set()
        division_members: list[str] = []
        for division in champ.divisions:
            if division.name in division_names:
                raise ConferenceSourceError(f"duplicate division {division.name!r}")
            division_names.add(division.name)
            division_members.extend(division.members)
            if any(team not in conference_members for team in division.members):
                raise ConferenceSourceError("division names a team outside its conference")
        if champ.selection == "division_leaders":
            if set(division_members) != conference_members or len(division_members) != len(conference_members):
                raise ConferenceSourceError(
                    f"divisions for {conference} do not cover each member exactly once"
                )
        elif division_members:
            raise ConferenceSourceError("divisions are only valid for division_leaders selection")
        if champ.site.mode == "fixed_hosted" and champ.site.fixed_host not in conference_members:
            raise ConferenceSourceError("fixed championship host is outside its conference")

    game_index = _snapshot_game_index(snapshot)
    designations: dict[str, ConferenceGameDesignation] = {}
    for item in supplement.games:
        if item.provider_id in designations:
            raise ConferenceSourceError(f"duplicate game designation {item.provider_id!r}")
        if item.provider_id not in game_index:
            raise ConferenceSourceError(f"designation references unknown game {item.provider_id!r}")
        source_game = game_index[item.provider_id]
        if (item.home_team, item.away_team) != (
            getattr(source_game, "home_team", None),
            getattr(source_game, "away_team", None),
        ):
            raise ConferenceSourceError(
                f"designation {item.provider_id!r} does not match snapshot teams"
            )
        for team, classification in (
            (item.home_team, getattr(source_game, "home_classification", None)),
            (item.away_team, getattr(source_game, "away_classification", None)),
        ):
            if team not in members and str(classification or "").upper() != "FCS":
                raise ConferenceSourceError(
                    f"designation references a non-member that is not FCS {team!r}"
                )
        if item.conference_game:
            home_member = members[item.home_team]
            away_member = members[item.away_team]
            if home_member.independent or away_member.independent:
                raise ConferenceSourceError("independent teams cannot have conference games")
            if home_member.conference != item.conference or away_member.conference != item.conference:
                raise ConferenceSourceError("conference designation disagrees with membership")
        _require_evidence(item.evidence_ids, evidence, f"game {item.provider_id}")
        designations[item.provider_id] = item
    if set(designations) != set(game_index):
        missing = sorted(set(game_index) - set(designations))
        extra = sorted(set(designations) - set(game_index))
        raise ConferenceSourceError(
            f"game designation coverage is not complete (missing={missing}, extra={extra})"
        )

    confirmations: set[str] = set()
    for item in supplement.confirmations:
        if item.confirmation_id in confirmations:
            raise ConferenceSourceError(f"duplicate confirmation {item.confirmation_id!r}")
        confirmations.add(item.confirmation_id)
        if item.conference not in rules:
            raise ConferenceSourceError("confirmation names an unknown conference")
        conference_members = {
            name for name, member in members.items() if member.conference == item.conference
        }
        if any(team not in conference_members for team in item.participants):
            raise ConferenceSourceError("confirmation participant is outside its conference")
        if item.host_team is not None and item.host_team not in conference_members:
            raise ConferenceSourceError("confirmation host is outside its conference")
        if item.host_team is not None and item.host_team not in item.participants:
            raise ConferenceSourceError("confirmation host is not a participant")
        _require_evidence(item.evidence_ids, evidence, f"confirmation {item.confirmation_id}")
        if any(evidence[evidence_id].known_at > item.known_at for evidence_id in item.evidence_ids):
            raise ConferenceSourceError("confirmation cannot be known before its evidence")

    return ValidatedConferenceSupplement(
        supplement=supplement,
        supplement_checksum=computed,
        snapshot_checksum=computed_snapshot_checksum,
    )


__all__ = [
    "ChampionshipRule",
    "ConferenceGameDesignation",
    "ConferenceMember",
    "ConferenceRule",
    "ConferenceSourceError",
    "ConferenceSupplement",
    "DatedConfirmation",
    "DivisionRule",
    "EligibilityRule",
    "SitePolicy",
    "SourceEvidence",
    "ValidatedConferenceSupplement",
    "snapshot_content_checksum",
    "validate_conference_supplement",
]
