"""Focused tests for the strict private conference supplement input boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from cfb.conference_inputs import (
    ConferenceInputError,
    load_conference_supplement,
    parse_conference_supplement,
)
from cfb.conference_sources import _supplement_payload
from tests.test_conference_sources import _snapshot, _supplement


def _document(snapshot):
    supplement = _supplement(snapshot)
    return {
        "supplement": _supplement_payload(supplement),
        "checksum": supplement.checksum,
    }


def _write(directory: Path, document: object, *, text: str | None = None) -> Path:
    path = directory / "conference-supplement.json"
    path.write_text(
        text if text is not None else json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


class ConferenceInputTests(unittest.TestCase):
    def test_round_trip_reconstructs_and_validates_snapshot_bound_identity(self) -> None:
        snapshot = _snapshot()
        document = _document(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            path = _write(Path(directory), document)
            loaded = load_conference_supplement(path, snapshot)
            parsed = parse_conference_supplement(document, snapshot)
        self.assertEqual(loaded.supplement_checksum, document["checksum"])
        self.assertEqual(loaded.snapshot_checksum, snapshot.checksum)
        self.assertEqual(loaded.content_identity, "fixture-conference-v1")
        self.assertEqual(parsed.supplement_checksum, loaded.supplement_checksum)

    def test_unknown_and_missing_fields_fail_at_every_wire_boundary(self) -> None:
        snapshot = _snapshot()
        document = _document(snapshot)

        root_extra = deepcopy(document)
        root_extra["unexpected"] = True
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(root_extra, snapshot)

        nested_extra = deepcopy(document)
        nested_extra["supplement"]["members"][0]["unexpected"] = True
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(nested_extra, snapshot)

        nested_missing = deepcopy(document)
        del nested_missing["supplement"]["rules"][0]["championship"]["site"]["mode"]
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(nested_missing, snapshot)

    def test_duplicate_nonfinite_and_nonintegral_values_fail_closed(self) -> None:
        snapshot = _snapshot()
        document = _document(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            payload = json.dumps(document["supplement"], sort_keys=True, separators=(",", ":"))
            path.write_text(
                '{"supplement":' + payload +
                ',"checksum":"' + document["checksum"] +
                '","checksum":"' + document["checksum"] + '"}',
                encoding="utf-8",
            )
            with self.assertRaises(ConferenceInputError):
                load_conference_supplement(path, snapshot)

            nonfinite = deepcopy(document)
            nonfinite["supplement"]["season"] = float("nan")
            path = _write(Path(directory), nonfinite)
            with self.assertRaises(ConferenceInputError):
                load_conference_supplement(path, snapshot)

            fractional = deepcopy(document)
            fractional["supplement"]["season"] = 2025.5
            with self.assertRaises(ConferenceInputError):
                parse_conference_supplement(fractional, snapshot)

    def test_declared_checksum_is_recomputed_and_tampering_is_rejected(self) -> None:
        snapshot = _snapshot()
        document = _document(snapshot)

        wrong_checksum = deepcopy(document)
        wrong_checksum["checksum"] = "0" * 64
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(wrong_checksum, snapshot)

        changed_value = deepcopy(document)
        changed_value["supplement"]["content_identity"] = "fixture-conference-tampered"
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(changed_value, snapshot)

        changed_nested = deepcopy(document)
        changed_nested["supplement"]["games"][0]["counts_for_standings"] = False
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(changed_nested, snapshot)

    def test_temporal_precision_and_bound_contradictions_fail_closed(self) -> None:
        snapshot = _snapshot()
        document = _document(snapshot)

        precision = deepcopy(document)
        precision["supplement"]["evidence"][0]["published_precision"] = "date"
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(precision, snapshot)

        bound = deepcopy(document)
        bound["supplement"]["evidence"][0]["known_at_upper_bound"] = (
            "2025-02-02T12:00:00+00:00"
        )
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(bound, snapshot)

    def test_local_datetime_provenance_round_trips_without_timezone_invention(self) -> None:
        snapshot = _snapshot()
        supplement = _supplement(snapshot)
        local_evidence = replace(
            supplement.evidence[0],
            published_at="2025-01-01T12:00:00",
            published_precision="local_datetime",
            known_at="2025-02-01T12:00:00",
            known_precision="local_datetime",
        )
        supplement = replace(supplement, evidence=(local_evidence,))
        document = {
            "supplement": _supplement_payload(supplement),
            "checksum": supplement.checksum,
        }
        parsed = parse_conference_supplement(document, snapshot)
        evidence = parsed.supplement.evidence[0]
        self.assertEqual(evidence.published_precision, "local_datetime")
        self.assertEqual(evidence.known_precision, "local_datetime")
        self.assertEqual(evidence.published_local_datetime.isoformat(), "2025-01-01T12:00:00")
        self.assertEqual(evidence.known_local_datetime.isoformat(), "2025-02-01T12:00:00")

    def test_snapshot_binding_and_complete_coverage_are_enforced_after_parsing(self) -> None:
        snapshot = _snapshot()
        document = _document(snapshot)

        wrong_snapshot = _snapshot(add_unknown_fbs=True)
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(document, wrong_snapshot)

        incomplete = deepcopy(document)
        del incomplete["supplement"]["games"][-1]
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(incomplete, snapshot)

    def test_symlink_and_invalid_utf8_files_are_rejected(self) -> None:
        snapshot = _snapshot()
        document = _document(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = _write(root, document)
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(ConferenceInputError):
                load_conference_supplement(link, snapshot)

            invalid = root / "invalid.json"
            invalid.write_bytes(b"\xff")
            with self.assertRaises(ConferenceInputError):
                load_conference_supplement(invalid, snapshot)


if __name__ == "__main__":
    unittest.main()
