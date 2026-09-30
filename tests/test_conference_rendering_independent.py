"""Independent acceptance checks for static conference artifacts.

The fixtures below are literal derived values.  They do not use the renderer
to derive expected ordering, routes, or public payload semantics.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import re
from statistics import median
from pathlib import Path
import tempfile
import unittest

from cfb.conference_reference import (
    ChampionshipProjection,
    ConferenceReference,
    ConferenceStandings,
    InterconferenceRecord,
    RatingComparison,
    Record,
    StandingsRow,
)
from cfb.conference_rendering import (
    ConferenceRenderingError,
    build_conference_artifacts,
    conference_slug,
    render_conference_artifacts,
)
from cfb.conference_validation import validate_conference_artifacts
from cfb.public_safety import assert_public_bytes


UTC = timezone.utc
ROOT = "cfb/years/2025/conferences"
CHECKPOINTS = ("PRESEASON", "W1", "FINAL")
CONFERENCES = (
    ("ACC", "acc"),
    ("American Athletic", "american"),
    ("Big 12", "big-12"),
    ("Big Ten", "big-ten"),
    ("Conference USA", "conference-usa"),
    ("Mid-American Conference", "mac"),
    ("Mountain West", "mountain-west"),
    ("Pac-12", "pac-12"),
    ("SEC", "sec"),
    ("Sun Belt", "sun-belt"),
)


def _materialize(site: Path, artifacts: dict[str, bytes]) -> None:
    for relative, raw in artifacts.items():
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def _row(
    school: str,
    conference: str,
    cors: float | None,
    rank: int | None,
    conference_record: Record,
    overall_record: Record,
    position: int | None,
    display_order: int,
) -> StandingsRow:
    return StandingsRow(
        school=school,
        conference=conference,
        conference_record=conference_record,
        overall_record=overall_record,
        cors=cors,
        national_rank=rank,
        position=position,
        display_order=display_order,
    )


def _rows(conference: str, checkpoint: str) -> tuple[StandingsRow, ...]:
    offset = {"PRESEASON": 0.0, "W1": 1.0, "FINAL": 2.0}[checkpoint]
    if conference == "ACC":
        return (
            _row("Alpha", conference, 80.0 + offset, 1, Record(2, 0, 0), Record(2, 0, 0), 1, 1),
            _row("Alpine", conference, 70.0 + offset, 2, Record(1, 1, 0), Record(1, 1, 0), 2, 2),
            _row("Zulu", conference, 70.0 + offset, 3, Record(1, 1, 0), Record(1, 1, 0), 2, 3),
            _row("Missing", conference, None, None, Record(), Record(), None, 4),
        )
    return (
        _row(
            f"{conference} One",
            conference,
            60.0 + offset,
            4,
            Record(1, 0, 0),
            Record(1, 0, 0),
            1,
            1,
        ),
        _row(
            f"{conference} Two",
            conference,
            50.0 + offset,
            5,
            Record(0, 1, 0),
            Record(0, 1, 0),
            2,
            2,
        ),
    )


def _comparison(conference: str, rows: tuple[StandingsRow, ...]) -> RatingComparison:
    ratings = tuple(row.cors for row in rows)
    if any(value is None for value in ratings):
        return RatingComparison(
            conference=conference,
            available=False,
            reason="missing_rating",
            member_count=len(rows),
            mean=None,
            median=None,
            top_count=None,
            top_mean=None,
            remainder_count=None,
            remainder_mean=None,
            top_gap=None,
        )
    values = tuple(float(value) for value in ratings)
    ordered = tuple(sorted(values, reverse=True))
    top_count = math.ceil(len(ordered) / 4)
    remainder = ordered[top_count:]
    top_mean = sum(ordered[:top_count]) / top_count
    remainder_mean = sum(remainder) / len(remainder)
    return RatingComparison(
        conference=conference,
        available=True,
        reason=None,
        member_count=len(values),
        mean=sum(values) / len(values),
        median=float(median(values)),
        top_count=top_count,
        top_mean=top_mean,
        remainder_count=len(remainder),
        remainder_mean=remainder_mean,
        top_gap=top_mean - remainder_mean,
    )


def _projection(conference: str, rows: tuple[StandingsRow, ...]) -> ChampionshipProjection:
    if conference == "Big 12":
        return ChampionshipProjection(
            conference=conference,
            status="not_applicable",
            participants=(),
            contenders=(),
            selection_basis=None,
            site_state="not_applicable",
            host_team=None,
            neutral_site=None,
            confirmation_id=None,
            reason="no_championship",
        )
    if conference == "SEC":
        return ChampionshipProjection(
            conference=conference,
            status="unavailable",
            participants=(),
            contenders=(),
            selection_basis=None,
            site_state="unavailable",
            host_team=None,
            neutral_site=None,
            confirmation_id=None,
            reason="ambiguous_game_timing",
        )
    hosted = conference == "ACC"
    return ChampionshipProjection(
        conference=conference,
        status="projected",
        participants=(rows[0].school, rows[1].school),
        contenders=(),
        selection_basis="standings",
        site_state="hosted" if hosted else "neutral",
        host_team=rows[0].school if hosted else None,
        neutral_site=False if hosted else True,
        confirmation_id=None,
    )


def _interconference() -> tuple[InterconferenceRecord, ...]:
    records: list[InterconferenceRecord] = []
    names = tuple(name for name, _slug in CONFERENCES)
    for index, conference in enumerate(names):
        for opponent in names[index + 1:]:
            if conference == "ACC" and opponent == "SEC":
                left_regular, left_unknown = Record(1, 0, 0), Record(1, 0, 0)
                right_regular, right_unknown = Record(0, 1, 0), Record(0, 1, 0)
            else:
                left_regular = left_unknown = Record()
                right_regular = right_unknown = Record()
            records.append(
                InterconferenceRecord(
                    conference=conference,
                    opponent=opponent,
                    opponent_kind="conference",
                    regular=left_regular,
                    postseason=Record(),
                    unknown=left_unknown,
                    combined=Record(
                        left_regular.wins + left_unknown.wins,
                        left_regular.losses + left_unknown.losses,
                        left_regular.ties + left_unknown.ties,
                    ),
                )
            )
            records.append(
                InterconferenceRecord(
                    conference=opponent,
                    opponent=conference,
                    opponent_kind="conference",
                    regular=right_regular,
                    postseason=Record(),
                    unknown=right_unknown,
                    combined=Record(
                        right_regular.wins + right_unknown.wins,
                        right_regular.losses + right_unknown.losses,
                        right_regular.ties + right_unknown.ties,
                    ),
                )
            )
        independent_regular = Record(1, 0, 0) if conference == "ACC" else Record()
        records.append(
            InterconferenceRecord(
                conference=conference,
                opponent="FBS Independents",
                opponent_kind="independent",
                regular=independent_regular,
                postseason=Record(),
                unknown=Record(),
                combined=independent_regular,
            )
        )
        records.append(
            InterconferenceRecord(
                conference=conference,
                opponent="FCS",
                opponent_kind="fcs",
                regular=Record(),
                postseason=Record(),
                unknown=Record(),
                combined=Record(),
            )
        )
    return tuple(records)


def _reference(checkpoint: str) -> ConferenceReference:
    phase, target_week, cutoff = {
        "PRESEASON": ("preseason", None, datetime(2025, 8, 1, tzinfo=UTC)),
        "W1": ("week", 1, datetime(2025, 9, 10, tzinfo=UTC)),
        "FINAL": ("final", 22, datetime(2026, 1, 1, tzinfo=UTC)),
    }[checkpoint]
    standings = tuple(
        ConferenceStandings(
            conference,
            _rows(conference, checkpoint),
        )
        for conference, _slug in CONFERENCES
    )
    independent = _row(
        "Independent",
        "FBS Independents",
        35.0,
        11,
        Record(),
        Record(1, 0, 0),
        None,
        1,
    )
    return ConferenceReference(
        season=2025,
        snapshot_checksum="a" * 64,
        supplement_checksum="b" * 64,
        content_identity="renderer-independent-fixture-v1",
        dataset_id="renderer-independent",
        model_version="conference-renderer-test-v1",
        checkpoint=checkpoint,
        phase=phase,
        target_week=target_week,
        cutoff=cutoff,
        standings=standings,
        independent_rows=(independent,),
        comparisons=tuple(
            _comparison(conference, _rows(conference, checkpoint))
            for conference, _slug in CONFERENCES
        ),
        interconference=_interconference(),
        projections=tuple(
            _projection(conference, _rows(conference, checkpoint))
            for conference, _slug in CONFERENCES
        ),
    )


class IndependentConferenceRenderingTests(unittest.TestCase):
    def test_all_routes_values_navigation_and_public_safety(self) -> None:
        references = tuple(_reference(checkpoint) for checkpoint in CHECKPOINTS)
        artifacts = render_conference_artifacts(references)
        expected = {
            f"{ROOT}/2025_FBS_conferences.html",
            f"{ROOT}/2025_FBS_conferences.json",
            *(
                f"{ROOT}/{checkpoint}_FBS_comparison.html"
                for checkpoint in CHECKPOINTS
            ),
            *(
                f"{ROOT}/{slug}/{checkpoint}_FBS_standings.html"
                for _name, slug in CONFERENCES
                for checkpoint in CHECKPOINTS
            ),
        }
        self.assertEqual(set(artifacts), expected)

        for path, raw in artifacts.items():
            assert_public_bytes(path, raw)
            if path.endswith(".html"):
                text = raw.decode("utf-8")
                self.assertNotIn("<script", text.lower())
                self.assertNotIn("javascript:", text.lower())
                self.assertNotIn("https://", text.lower())
                self.assertNotIn("http://", text.lower())

        overview = artifacts[f"{ROOT}/2025_FBS_conferences.html"].decode()
        self.assertIn("Checkpoint: <strong>FINAL</strong>", overview)
        self.assertIn("../history/2025_FBS_progression.html", overview)
        self.assertIn("./FINAL_FBS_comparison.html", overview)
        self.assertIn("Historical reconstruction", overview)

        for checkpoint in CHECKPOINTS:
            comparison = artifacts[f"{ROOT}/{checkpoint}_FBS_comparison.html"].decode()
            self.assertIn(
                f'<a href="./{checkpoint}_FBS_comparison.html" aria-current="page">{checkpoint}</a>',
                comparison,
            )
            for other in CHECKPOINTS:
                self.assertIn(f'href="./{other}_FBS_comparison.html"', comparison)
            standings = artifacts[f"{ROOT}/acc/{checkpoint}_FBS_standings.html"].decode()
            self.assertIn(
                f'<a href="./{checkpoint}_FBS_standings.html" aria-current="page">{checkpoint}</a>',
                standings,
            )
            for other in CHECKPOINTS:
                self.assertIn(f'href="./{other}_FBS_standings.html"', standings)

        self.assertEqual(conference_slug("American Athletic"), "american")
        self.assertEqual(conference_slug("Mid-American Conference"), "mac")
        self.assertIn(f"{ROOT}/american/W1_FBS_standings.html", artifacts)
        self.assertIn(f"{ROOT}/mac/W1_FBS_standings.html", artifacts)

        comparison = artifacts[f"{ROOT}/W1_FBS_comparison.html"].decode()
        acc_start = comparison.index("<h2>ACC CORS distribution</h2>")
        acc_end = comparison.index("<h2>American Athletic CORS distribution</h2>")
        acc_distribution = comparison[acc_start:acc_end]
        self.assertLess(acc_distribution.index('<th scope="row">Alpha</th>'), acc_distribution.index('<th scope="row">Alpine</th>'))
        self.assertLess(acc_distribution.index('<th scope="row">Alpine</th>'), acc_distribution.index('<th scope="row">Zulu</th>'))
        self.assertLess(acc_distribution.index('<th scope="row">Zulu</th>'), acc_distribution.index('<th scope="row">Missing</th>'))
        self.assertIn("81.0", acc_distribution)
        self.assertIn("Unavailable", acc_distribution)
        self.assertIn("Hosted by Alpha", comparison)
        self.assertIn("FBS Independents CORS distribution", comparison)
        self.assertIn("Unknown phase", comparison)
        self.assertIn("No games", comparison)
        self.assertIn("Game timing ambiguous at cutoff", comparison)
        self.assertIn("No championship game", comparison)

        payload = json.loads(artifacts[f"{ROOT}/2025_FBS_conferences.json"])
        self.assertEqual(payload["default_checkpoint"], "FINAL")
        self.assertEqual(
            [item["checkpoint"] for item in payload["checkpoints"]],
            list(CHECKPOINTS),
        )
        w1 = next(item for item in payload["checkpoints"] if item["checkpoint"] == "W1")
        self.assertEqual(
            {item["conference"] for item in w1["comparisons"]},
            {name for name, _slug in CONFERENCES},
        )
        self.assertEqual(w1["independent_rows"][0]["team"], "Independent")
        self.assertNotIn('"school"', artifacts[f"{ROOT}/2025_FBS_conferences.json"].decode())
        acc_sec = next(
            item for item in w1["interconference"]
            if item["conference"] == "ACC" and item["opponent"] == "SEC"
        )
        self.assertEqual(acc_sec["regular"]["wins"], 1)
        self.assertEqual(acc_sec["unknown"]["wins"], 1)
        self.assertEqual(acc_sec["combined"]["wins"], 2)
        acc_fcs = next(
            item for item in w1["interconference"]
            if item["conference"] == "ACC" and item["opponent"] == "FCS"
        )
        self.assertEqual(acc_fcs["regular"]["games"], 0)
        self.assertEqual(acc_fcs["postseason"]["games"], 0)
        self.assertEqual(acc_fcs["unknown"]["games"], 0)
        self.assertEqual(acc_fcs["combined"]["games"], 0)
        sec_projection = next(
            item for item in w1["projections"] if item["conference"] == "SEC"
        )
        self.assertEqual(sec_projection["status"], "unavailable")
        self.assertEqual(sec_projection["reason"], "ambiguous_game_timing")

    def test_independent_validator_rejects_json_and_static_markup_tampering(self) -> None:
        references = tuple(_reference(checkpoint) for checkpoint in CHECKPOINTS)
        artifacts = render_conference_artifacts(references)
        json_relative = Path(ROOT) / "2025_FBS_conferences.json"
        comparison_relative = Path(ROOT) / "W1_FBS_comparison.html"

        def assert_rejected(relative: Path, mutate, label: str) -> None:
            with tempfile.TemporaryDirectory(prefix="conference-validator-") as directory:
                site = Path(directory)
                _materialize(site, artifacts)
                target = site / relative
                target.write_text(mutate(target.read_text(encoding="utf-8")), encoding="utf-8")
                failures = validate_conference_artifacts(site, references)
                self.assertTrue(failures, f"validator accepted {label} tamper")

        with tempfile.TemporaryDirectory(prefix="conference-validator-good-") as directory:
            site = Path(directory)
            _materialize(site, artifacts)
            self.assertEqual(validate_conference_artifacts(site, references), [])

        json_text = artifacts[json_relative.as_posix()].decode("utf-8")
        assert_rejected(
            json_relative,
            lambda _text: json_text.replace(
                '"schema_version":1', '"schema_version":1,"schema_version":1', 1
            ),
            "duplicate JSON key",
        )
        assert_rejected(
            json_relative,
            lambda _text: json.dumps(
                {**json.loads(json_text), "unexpected": True},
                indent=2,
            ),
            "unexpected JSON field",
        )
        assert_rejected(
            json_relative,
            lambda _text: re.sub(
                r'("mean"\s*:\s*)-?(?:\d+(?:\.\d*)?|\.\d+)',
                r"\1NaN",
                json_text,
                count=1,
            ),
            "nonfinite JSON value",
        )

        html_text = artifacts[comparison_relative.as_posix()].decode("utf-8")
        html_mutations = (
            (lambda _text: html_text.replace("81.0", "82.0", 1), "HTML cell"),
            (lambda _text: html_text.replace("a" * 64, "c" * 64, 1), "HTML provenance"),
            (
                lambda _text: html_text.replace(
                    'href="./FINAL_FBS_comparison.html"',
                    'href="./missing.html"',
                    1,
                ),
                "missing navigation",
            ),
            (
                lambda _text: html_text.replace(
                    "<title>W1 conference comparison — 2025 FBS</title>",
                    "<title>Wrong title</title>",
                    1,
                ),
                "wrong title",
            ),
            (
                lambda _text: html_text.replace("</body>", "<table></table></body>", 1),
                "duplicate table",
            ),
            (
                lambda _text: html_text.replace("</body>", "<script>alert(1)</script></body>", 1),
                "executable script",
            ),
            (
                lambda _text: html_text.replace("<table", '<table onclick="alert(1)"', 1),
                "event attribute",
            ),
            (
                lambda _text: html_text.replace(
                    'href="./FINAL_FBS_comparison.html"',
                    'href="https://evil.example/redirect"',
                    1,
                ),
                "external URL",
            ),
            (
                lambda _text: html_text.replace("<table", "<table hidden", 1),
                "hidden table",
            ),
        )
        for mutate, label in html_mutations:
            assert_rejected(comparison_relative, mutate, label)

    def test_malformed_direct_values_fail_closed_at_output_boundary(self) -> None:
        references = tuple(_reference(checkpoint) for checkpoint in CHECKPOINTS)
        w1 = references[1]

        bad_comparison = replace(
            next(item for item in w1.comparisons if item.conference == "SEC"),
            mean=float("nan"),
        )
        bad_comparison_reference = replace(
            w1,
            comparisons=tuple(
                bad_comparison if item.conference == "SEC" else item
                for item in w1.comparisons
            ),
        )
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((bad_comparison_reference,))

        unsafe_row = object.__new__(StandingsRow)
        object.__setattr__(unsafe_row, "school", "<script>alert(1)</script>")
        object.__setattr__(unsafe_row, "conference", "ACC")
        object.__setattr__(unsafe_row, "conference_record", Record(1, 0, 0))
        object.__setattr__(unsafe_row, "overall_record", Record(1, 0, 0))
        object.__setattr__(unsafe_row, "cors", 81.0)
        object.__setattr__(unsafe_row, "national_rank", 1)
        object.__setattr__(unsafe_row, "position", 1)
        object.__setattr__(unsafe_row, "display_order", 1)
        unsafe_standings = ConferenceStandings(
            "ACC", (unsafe_row,) + w1.standings[0].rows[1:]
        )
        unsafe_reference = replace(
            w1,
            standings=(unsafe_standings,) + w1.standings[1:],
        )
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((unsafe_reference,))


if __name__ == "__main__":
    unittest.main()
