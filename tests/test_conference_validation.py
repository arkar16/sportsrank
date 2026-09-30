"""Focused tests for the independent conference artifact validator."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb.conference_rendering import render_conference_artifacts
from cfb.conference_validation import validate_conference_artifacts
from tests.test_conference_rendering import _reference


ROOT = Path("cfb") / "years" / "2025" / "conferences"


def _materialize(site: Path, references) -> None:
    for relative, raw in render_conference_artifacts(references).items():
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


class ConferenceValidationTests(unittest.TestCase):
    def test_valid_graph_is_accepted_without_using_presentation_helpers(self) -> None:
        references = (_reference("W0"), _reference("FINAL", final=True))
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            _materialize(site, references)
            with patch(
                "cfb.conference_rendering.render_conference_artifacts",
                side_effect=AssertionError("validator must not render"),
            ):
                self.assertEqual(validate_conference_artifacts(site, references), [])

    def test_json_extra_duplicate_and_nonfinite_values_fail_closed(self) -> None:
        references = (_reference("W0"), _reference("FINAL", final=True))
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            _materialize(site, references)
            path = site / ROOT / "2025_FBS_conferences.json"
            original = path.read_text(encoding="utf-8")

            path.write_text(original.replace('"sport":"cfb"', '"sport":"cfb","extra":1', 1), encoding="utf-8")
            self.assertTrue(validate_conference_artifacts(site, references))

            path.write_text('{"sport":"cfb","sport":"cfb"}', encoding="utf-8")
            failures = validate_conference_artifacts(site, references)
            self.assertTrue(any("duplicate JSON key" in failure for failure in failures))

            path.write_text(original.replace("80.0", "NaN", 1), encoding="utf-8")
            failures = validate_conference_artifacts(site, references)
            self.assertTrue(any("non-finite" in failure or "malformed" in failure for failure in failures))

    def test_html_values_headers_navigation_and_executable_markup_fail_closed(self) -> None:
        references = (_reference("W0"), _reference("FINAL", final=True))
        relative = ROOT / "W0_FBS_comparison.html"
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            _materialize(site, references)
            path = site / relative
            original = path.read_text(encoding="utf-8")

            path.write_text(original.replace("80.0", "81.0", 1), encoding="utf-8")
            self.assertTrue(validate_conference_artifacts(site, references))

            path.write_text(
                original.replace('./FINAL_FBS_comparison.html', './missing.html', 1),
                encoding="utf-8",
            )
            self.assertTrue(validate_conference_artifacts(site, references))

            path.write_text(original.replace("</body>", "<script>alert(1)</script></body>"), encoding="utf-8")
            failures = validate_conference_artifacts(site, references)
            self.assertTrue(any("executable" in failure for failure in failures))

            path.write_text(original.replace("</body>", "<table></table></body>"), encoding="utf-8")
            self.assertTrue(validate_conference_artifacts(site, references))

            path.write_text(
                original.replace("<table>", "<table hidden>", 1),
                encoding="utf-8",
            )
            failures = validate_conference_artifacts(site, references)
            self.assertTrue(any("hidden or inert" in failure for failure in failures))

            path.write_text(
                original.replace(
                    "</head>",
                    "<style>.table-scroll{display:none}</style></head>",
                    1,
                ),
                encoding="utf-8",
            )
            failures = validate_conference_artifacts(site, references)
            self.assertTrue(any("stylesheet differs" in failure for failure in failures))

            path.write_text(
                original.replace("<table>", '<table style="display:none">', 1),
                encoding="utf-8",
            )
            failures = validate_conference_artifacts(site, references)
            self.assertTrue(any("inline styles" in failure for failure in failures))

    def test_host_identity_and_unknown_phase_json_are_checked(self) -> None:
        reference = _reference("W0")
        hosted = replace(
            reference.projections[0],
            site_state="hosted",
            host_team="Alpha",
            neutral_site=False,
        )
        reference = replace(reference, projections=(hosted, reference.projections[1]))
        references = (reference,)
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            _materialize(site, references)
            self.assertEqual(validate_conference_artifacts(site, references), [])
            comparison = site / ROOT / "W0_FBS_comparison.html"
            comparison.write_text(
                comparison.read_text(encoding="utf-8").replace("Hosted by Alpha", "Hosted", 1),
                encoding="utf-8",
            )
            self.assertTrue(validate_conference_artifacts(site, references))


if __name__ == "__main__":
    unittest.main()
