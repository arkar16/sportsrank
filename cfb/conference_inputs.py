"""Strict JSON input for snapshot-bound conference evidence supplements.

The source supplement is private input.  Its wire payload is the canonical
``conference_sources._supplement_payload`` shape wrapped with one separately
declared checksum::

    {"supplement": <canonical payload>, "checksum": <sha256>}

The checksum is checked against the reconstructed typed value and the typed
value is then validated against the supplied immutable season snapshot.  No
caller-provided digest is used as an authority for either step.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import Any

try:
    from .conference_sources import (
        ChampionshipRule,
        ConferenceGameDesignation,
        ConferenceMember,
        ConferenceRule,
        ConferenceSourceError,
        ConferenceSupplement,
        DatedConfirmation,
        DivisionRule,
        EligibilityRule,
        SitePolicy,
        SourceEvidence,
        ValidatedConferenceSupplement,
        _supplement_payload,
        validate_conference_supplement,
    )
except ImportError:  # pragma: no cover - supports direct execution from cfb/
    from conference_sources import (
        ChampionshipRule,
        ConferenceGameDesignation,
        ConferenceMember,
        ConferenceRule,
        ConferenceSourceError,
        ConferenceSupplement,
        DatedConfirmation,
        DivisionRule,
        EligibilityRule,
        SitePolicy,
        SourceEvidence,
        ValidatedConferenceSupplement,
        _supplement_payload,
        validate_conference_supplement,
    )


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ConferenceInputError(ValueError):
    """Raised when a supplement document is malformed or not source-bound."""


def _duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConferenceInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ConferenceInputError(f"non-finite JSON constant: {value}")


def _object(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConferenceInputError(f"{field} must be a JSON object")
    actual = set(value)
    missing = sorted(keys - actual)
    unknown = sorted(actual - keys)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise ConferenceInputError(f"{field} has invalid fields ({'; '.join(details)})")
    return dict(value)


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConferenceInputError(f"{field} must be a JSON array")
    return value


def _string(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str:
        raise ConferenceInputError(f"{field} must be a string")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ConferenceInputError(f"{field} must be a boolean")
    return value


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ConferenceInputError(f"{field} must be an integer")
    return value


def _digest(value: Any, field: str) -> str:
    text = _string(value, field)
    assert text is not None
    if _SHA256.fullmatch(text) is None:
        raise ConferenceInputError(f"{field} must be a lowercase SHA-256 digest")
    return text


def _ids(value: Any, field: str) -> tuple[str, ...]:
    values = _array(value, field)
    result = tuple(_string(item, f"{field} item") for item in values)
    if any(item is None or not item for item in result):
        raise ConferenceInputError(f"{field} contains an empty identity")
    return result  # type: ignore[return-value]


def _text_values(value: Any, field: str) -> tuple[str, ...]:
    values = _array(value, field)
    result = tuple(_string(item, f"{field} item") for item in values)
    if any(item is None or not item for item in result):
        raise ConferenceInputError(f"{field} contains empty text")
    return result  # type: ignore[return-value]


_EVIDENCE_FIELDS = {
    "evidence_id",
    "url",
    "source_sha256",
    "published_at",
    "published_at_upper_bound",
    "published_date",
    "published_local_datetime",
    "published_precision",
    "published_raw",
    "effective_from",
    "effective_to",
    "known_at",
    "known_at_upper_bound",
    "known_date",
    "known_local_datetime",
    "known_precision",
    "known_raw",
    "timing_basis",
    "timing_reason",
    "retrieved_at",
    "locator",
}


def _evidence(value: Any, index: int) -> SourceEvidence:
    field = f"supplement.evidence[{index}]"
    item = _object(value, _EVIDENCE_FIELDS, field)
    return SourceEvidence(
        evidence_id=_string(item["evidence_id"], f"{field}.evidence_id"),
        url=_string(item["url"], f"{field}.url"),
        source_sha256=_digest(item["source_sha256"], f"{field}.source_sha256"),
        published_at=_string(item["published_at"], f"{field}.published_at", nullable=True),
        effective_from=_string(item["effective_from"], f"{field}.effective_from"),
        known_at=_string(item["known_at"], f"{field}.known_at", nullable=True),
        effective_to=_string(item["effective_to"], f"{field}.effective_to", nullable=True),
        retrieved_at=_string(item["retrieved_at"], f"{field}.retrieved_at", nullable=True),
        locator=_string(item["locator"], f"{field}.locator", nullable=True),
        published_precision=_string(
            item["published_precision"], f"{field}.published_precision"
        ),
        known_precision=_string(item["known_precision"], f"{field}.known_precision"),
        published_raw=_string(item["published_raw"], f"{field}.published_raw", nullable=True),
        known_raw=_string(item["known_raw"], f"{field}.known_raw", nullable=True),
        published_at_upper_bound=_string(
            item["published_at_upper_bound"],
            f"{field}.published_at_upper_bound",
            nullable=True,
        ),
        known_at_upper_bound=_string(
            item["known_at_upper_bound"],
            f"{field}.known_at_upper_bound",
            nullable=True,
        ),
        published_date=_string(item["published_date"], f"{field}.published_date", nullable=True),
        published_local_datetime=_string(
            item["published_local_datetime"],
            f"{field}.published_local_datetime",
            nullable=True,
        ),
        known_date=_string(item["known_date"], f"{field}.known_date", nullable=True),
        known_local_datetime=_string(
            item["known_local_datetime"],
            f"{field}.known_local_datetime",
            nullable=True,
        ),
        timing_basis=_string(item["timing_basis"], f"{field}.timing_basis", nullable=True),
        timing_reason=_string(item["timing_reason"], f"{field}.timing_reason", nullable=True),
    )


_MEMBER_FIELDS = {"team", "conference", "independent", "classification", "evidence_ids"}


def _member(value: Any, index: int) -> ConferenceMember:
    field = f"supplement.members[{index}]"
    item = _object(value, _MEMBER_FIELDS, field)
    return ConferenceMember(
        team=_string(item["team"], f"{field}.team"),
        conference=_string(item["conference"], f"{field}.conference", nullable=True),
        independent=_boolean(item["independent"], f"{field}.independent"),
        classification=_string(item["classification"], f"{field}.classification"),
        evidence_ids=_ids(item["evidence_ids"], f"{field}.evidence_ids"),
    )


_GAME_FIELDS = {
    "provider_id",
    "home_team",
    "away_team",
    "conference_game",
    "counts_for_standings",
    "title_game",
    "conference",
    "evidence_ids",
    "phase",
    "phase_evidence_ids",
}


def _game(value: Any, index: int) -> ConferenceGameDesignation:
    field = f"supplement.games[{index}]"
    item = _object(value, _GAME_FIELDS, field)
    return ConferenceGameDesignation(
        provider_id=_string(item["provider_id"], f"{field}.provider_id"),
        home_team=_string(item["home_team"], f"{field}.home_team"),
        away_team=_string(item["away_team"], f"{field}.away_team"),
        conference_game=_boolean(item["conference_game"], f"{field}.conference_game"),
        counts_for_standings=_boolean(
            item["counts_for_standings"], f"{field}.counts_for_standings"
        ),
        title_game=_boolean(item["title_game"], f"{field}.title_game"),
        conference=_string(item["conference"], f"{field}.conference", nullable=True),
        evidence_ids=_ids(item["evidence_ids"], f"{field}.evidence_ids"),
        phase=_string(item["phase"], f"{field}.phase"),
        phase_evidence_ids=_ids(
            item["phase_evidence_ids"], f"{field}.phase_evidence_ids"
        ),
    )


_ELIGIBILITY_FIELDS = {"team", "eligible", "evidence_ids", "reason"}
_DIVISION_FIELDS = {"name", "members", "evidence_ids"}
_SITE_FIELDS = {"mode", "fixed_host", "evidence_ids"}
_CHAMPIONSHIP_FIELDS = {
    "selection",
    "eligibility",
    "divisions",
    "site",
    "reason",
    "evidence_ids",
}
_RULE_FIELDS = {"conference", "standings_criterion", "evidence_ids", "championship"}


def _eligibility(value: Any, rule_index: int, index: int) -> EligibilityRule:
    field = f"supplement.rules[{rule_index}].championship.eligibility[{index}]"
    item = _object(value, _ELIGIBILITY_FIELDS, field)
    eligible = item["eligible"]
    if eligible is not None:
        eligible = _boolean(eligible, f"{field}.eligible")
    return EligibilityRule(
        team=_string(item["team"], f"{field}.team"),
        eligible=eligible,
        evidence_ids=_ids(item["evidence_ids"], f"{field}.evidence_ids"),
        reason=_string(item["reason"], f"{field}.reason", nullable=True),
    )


def _division(value: Any, rule_index: int, index: int) -> DivisionRule:
    field = f"supplement.rules[{rule_index}].championship.divisions[{index}]"
    item = _object(value, _DIVISION_FIELDS, field)
    return DivisionRule(
        name=_string(item["name"], f"{field}.name"),
        members=_text_values(item["members"], f"{field}.members"),
        evidence_ids=_ids(item["evidence_ids"], f"{field}.evidence_ids"),
    )


def _site(value: Any, rule_index: int) -> SitePolicy:
    field = f"supplement.rules[{rule_index}].championship.site"
    item = _object(value, _SITE_FIELDS, field)
    return SitePolicy(
        mode=_string(item["mode"], f"{field}.mode"),
        fixed_host=_string(item["fixed_host"], f"{field}.fixed_host", nullable=True),
        evidence_ids=_ids(item["evidence_ids"], f"{field}.evidence_ids"),
    )


def _championship(value: Any, rule_index: int) -> ChampionshipRule:
    field = f"supplement.rules[{rule_index}].championship"
    item = _object(value, _CHAMPIONSHIP_FIELDS, field)
    eligibility = tuple(
        _eligibility(row, rule_index, index)
        for index, row in enumerate(_array(item["eligibility"], f"{field}.eligibility"))
    )
    divisions = tuple(
        _division(row, rule_index, index)
        for index, row in enumerate(_array(item["divisions"], f"{field}.divisions"))
    )
    return ChampionshipRule(
        selection=_string(item["selection"], f"{field}.selection"),
        eligibility=eligibility,
        divisions=divisions,
        site=_site(item["site"], rule_index),
        evidence_ids=_ids(item["evidence_ids"], f"{field}.evidence_ids"),
        reason=_string(item["reason"], f"{field}.reason", nullable=True),
    )


def _rule(value: Any, index: int) -> ConferenceRule:
    field = f"supplement.rules[{index}]"
    item = _object(value, _RULE_FIELDS, field)
    return ConferenceRule(
        conference=_string(item["conference"], f"{field}.conference"),
        standings_criterion=_string(
            item["standings_criterion"], f"{field}.standings_criterion"
        ),
        evidence_ids=_ids(item["evidence_ids"], f"{field}.evidence_ids"),
        championship=_championship(item["championship"], index),
    )


_CONFIRMATION_FIELDS = {
    "confirmation_id",
    "conference",
    "participants",
    "known_at",
    "known_at_upper_bound",
    "known_date",
    "known_local_datetime",
    "known_precision",
    "known_raw",
    "timing_basis",
    "timing_reason",
    "evidence_ids",
    "kind",
    "site_mode",
    "host_team",
}


def _confirmation(value: Any, index: int) -> DatedConfirmation:
    field = f"supplement.confirmations[{index}]"
    item = _object(value, _CONFIRMATION_FIELDS, field)
    return DatedConfirmation(
        confirmation_id=_string(item["confirmation_id"], f"{field}.confirmation_id"),
        conference=_string(item["conference"], f"{field}.conference"),
        participants=_text_values(item["participants"], f"{field}.participants"),
        known_at=_string(item["known_at"], f"{field}.known_at", nullable=True),
        evidence_ids=_ids(item["evidence_ids"], f"{field}.evidence_ids"),
        kind=_string(item["kind"], f"{field}.kind"),
        site_mode=_string(item["site_mode"], f"{field}.site_mode", nullable=True),
        host_team=_string(item["host_team"], f"{field}.host_team", nullable=True),
        known_precision=_string(item["known_precision"], f"{field}.known_precision"),
        known_raw=_string(item["known_raw"], f"{field}.known_raw", nullable=True),
        known_at_upper_bound=_string(
            item["known_at_upper_bound"],
            f"{field}.known_at_upper_bound",
            nullable=True,
        ),
        known_date=_string(item["known_date"], f"{field}.known_date", nullable=True),
        known_local_datetime=_string(
            item["known_local_datetime"],
            f"{field}.known_local_datetime",
            nullable=True,
        ),
        timing_basis=_string(item["timing_basis"], f"{field}.timing_basis", nullable=True),
        timing_reason=_string(item["timing_reason"], f"{field}.timing_reason", nullable=True),
    )


_SUPPLEMENT_FIELDS = {
    "schema_version",
    "season",
    "snapshot_checksum",
    "content_identity",
    "evidence",
    "members",
    "games",
    "rules",
    "confirmations",
}
_DOCUMENT_FIELDS = {"supplement", "checksum"}


def _supplement(value: Any, declared_checksum: str) -> ConferenceSupplement:
    item = _object(value, _SUPPLEMENT_FIELDS, "supplement")
    supplement = ConferenceSupplement(
        season=_integer(item["season"], "supplement.season"),
        snapshot_checksum=_digest(item["snapshot_checksum"], "supplement.snapshot_checksum"),
        content_identity=_string(item["content_identity"], "supplement.content_identity"),
        evidence=tuple(
            _evidence(row, index)
            for index, row in enumerate(_array(item["evidence"], "supplement.evidence"))
        ),
        members=tuple(
            _member(row, index)
            for index, row in enumerate(_array(item["members"], "supplement.members"))
        ),
        games=tuple(
            _game(row, index)
            for index, row in enumerate(_array(item["games"], "supplement.games"))
        ),
        rules=tuple(
            _rule(row, index)
            for index, row in enumerate(_array(item["rules"], "supplement.rules"))
        ),
        confirmations=tuple(
            _confirmation(row, index)
            for index, row in enumerate(
                _array(item["confirmations"], "supplement.confirmations")
            )
        ),
        declared_checksum=declared_checksum,
        schema_version=_integer(item["schema_version"], "supplement.schema_version"),
    )
    if _supplement_payload(supplement) != item:
        raise ConferenceInputError(
            "supplement fields are not in the canonical source wire representation"
        )
    return supplement


def parse_conference_supplement(
    document: Mapping[str, Any],
    snapshot: Any,
) -> ValidatedConferenceSupplement:
    """Parse and snapshot-validate one canonical supplement document."""

    try:
        root = _object(document, _DOCUMENT_FIELDS, "supplement document")
        declared_checksum = _digest(root["checksum"], "document.checksum")
        supplement = _supplement(root["supplement"], declared_checksum)
        computed_checksum = supplement.checksum
        if declared_checksum != computed_checksum:
            raise ConferenceInputError(
                "document checksum does not match canonical supplement content"
            )
        return validate_conference_supplement(supplement, snapshot)
    except ConferenceInputError:
        raise
    except ConferenceSourceError as exc:
        raise ConferenceInputError(f"supplement source validation failed: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ConferenceInputError(f"supplement input is invalid: {exc}") from exc


def load_conference_supplement(
    path: str | Path,
    snapshot: Any,
) -> ValidatedConferenceSupplement:
    """Load, parse, checksum, and snapshot-validate a supplement JSON file."""

    document_path = Path(path)
    if document_path.is_symlink():
        raise ConferenceInputError("supplement input cannot be a symlink")
    if not document_path.is_file():
        raise ConferenceInputError("supplement input file is missing")
    try:
        raw = document_path.read_bytes()
        text = raw.decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=_duplicate_object,
            parse_constant=_reject_constant,
        )
    except ConferenceInputError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ConferenceInputError(f"supplement JSON is malformed: {exc}") from exc
    return parse_conference_supplement(document, snapshot)


__all__ = [
    "ConferenceInputError",
    "load_conference_supplement",
    "parse_conference_supplement",
]
