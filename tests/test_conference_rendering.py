"""Independent checks for the pure static conference artifact adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from statistics import median
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


UTC = timezone.utc


def _row(
    school: str,
    conference: str,
    cors: float,
    rank: int,
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


def _comparison(
    conference: str,
    values: tuple[float, ...],
) -> RatingComparison:
    values = tuple(values)
    ordered = sorted(values, reverse=True)
    top_count = math.ceil(len(ordered) / 4)
    remainder = ordered[top_count:]
    top_mean = sum(ordered[:top_count]) / top_count
    remainder_mean = sum(remainder) / len(remainder) if remainder else None
    return RatingComparison(
        conference=conference,
        available=remainder_mean is not None,
        reason=None if remainder_mean is not None else "empty_remainder",
        member_count=len(values),
        mean=sum(values) / len(values),
        median=float(median(values)),
        top_count=top_count,
        top_mean=top_mean,
        remainder_count=len(remainder),
        remainder_mean=remainder_mean,
        top_gap=top_mean - remainder_mean if remainder_mean is not None else None,
    )


def _inter(
    conference: str,
    opponent: str,
    kind: str,
    regular: Record,
    postseason: Record | None = None,
    unknown: Record | None = None,
) -> InterconferenceRecord:
    if postseason is None:
        postseason = Record()
    if unknown is None:
        unknown = Record()
    return InterconferenceRecord(
        conference=conference,
        opponent=opponent,
        opponent_kind=kind,
        regular=regular,
        postseason=postseason,
        unknown=unknown,
        combined=Record(
            regular.wins + postseason.wins + unknown.wins,
            regular.losses + postseason.losses + unknown.losses,
            regular.ties + postseason.ties + unknown.ties,
        ),
    )


def _reference(checkpoint: str, *, final: bool = False) -> ConferenceReference:
    acc_record = Record(1, 1, 0) if final else Record(1, 0, 0)
    acc_other = Record(1, 1, 0) if final else Record(0, 1, 0)
    sec_record = Record(1, 1, 0) if final else Record(1, 0, 0)
    sec_other = Record(1, 1, 0) if final else Record(0, 1, 0)
    acc_rows = (
        _row("Alpha", "ACC", 80.0 if not final else 82.0, 1, acc_record, Record(2, 1, 0), 1, 1),
        _row("Beta", "ACC", 70.0 if not final else 68.0, 2, acc_other, Record(1, 2, 0), 1 if final else 2, 2),
    )
    sec_rows = (
        (
            _row("Delta", "SEC", 50.0 if not final else 47.0, 4, sec_other, Record(1, 2, 0), 1, 1),
            _row("Gamma", "SEC", 60.0 if not final else 63.0, 3, sec_record, Record(2, 1, 0), 1, 2),
        )
        if final
        else (
            _row("Gamma", "SEC", 60.0, 3, sec_record, Record(2, 1, 0), 1, 1),
            _row("Delta", "SEC", 50.0, 4, sec_other, Record(1, 2, 0), 2, 2),
        )
    )
    independent = _row("Indy", "FBS Independents", 40.0, 5, Record(), Record(1, 1, 0), None, 1)
    acc_comparison = _comparison("ACC", (row.cors for row in acc_rows))
    sec_comparison = _comparison("SEC", (row.cors for row in sec_rows))
    projection_acc = ChampionshipProjection(
        conference="ACC",
        status="projected",
        participants=("Alpha", "Beta"),
        contenders=(),
        selection_basis="standings",
        site_state="neutral",
        host_team=None,
        neutral_site=True,
        confirmation_id=None,
    )
    projection_sec = ChampionshipProjection(
        conference="SEC",
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
    records = (
        _inter("ACC", "SEC", "conference", Record(1, 0, 0)),
        _inter("SEC", "ACC", "conference", Record(0, 1, 0)),
        _inter(
            "ACC",
            "FBS Independents",
            "independent",
            Record(1, 0, 0),
            unknown=Record(1, 0, 0),
        ),
        _inter("SEC", "FBS Independents", "independent", Record()),
        _inter("ACC", "FCS", "fcs", Record()),
        _inter("SEC", "FCS", "fcs", Record()),
    )
    return ConferenceReference(
        season=2025,
        snapshot_checksum="a" * 64,
        supplement_checksum="b" * 64,
        content_identity="conference-fixture-v1",
        dataset_id="conference-fixture",
        model_version="model-v1",
        checkpoint=checkpoint,
        phase="final" if final else "week",
        target_week=1 if final else 0,
        cutoff=datetime(2026, 1, 1, tzinfo=UTC) if final else datetime(2025, 9, 10, tzinfo=UTC),
        standings=(
            ConferenceStandings("ACC", acc_rows),
            ConferenceStandings("SEC", sec_rows),
        ),
        independent_rows=(independent,),
        comparisons=(acc_comparison, sec_comparison),
        interconference=records,
        projections=(projection_acc, projection_sec),
    )


def _rename_conference(reference: ConferenceReference, old: str, new: str) -> ConferenceReference:
    standings = []
    for item in reference.standings:
        if item.conference == old:
            standings.append(
                ConferenceStandings(
                    new,
                    tuple(replace(row, conference=new) for row in item.rows),
                )
            )
        else:
            standings.append(item)
    comparisons = tuple(
        replace(item, conference=new) if item.conference == old else item
        for item in reference.comparisons
    )
    projections = tuple(
        replace(item, conference=new) if item.conference == old else item
        for item in reference.projections
    )
    records = []
    for item in reference.interconference:
        records.append(
            replace(
                item,
                conference=new if item.conference == old else item.conference,
                opponent=new if item.opponent == old else item.opponent,
            )
        )
    return replace(
        reference,
        standings=tuple(standings),
        comparisons=comparisons,
        projections=projections,
        interconference=tuple(records),
    )


class ConferenceRenderingTests(unittest.TestCase):
    def test_routes_visible_values_navigation_and_safe_json(self) -> None:
        references = (_reference("W0"), _reference("FINAL", final=True))
        artifacts = render_conference_artifacts(references)
        root = "cfb/years/2025/conferences"
        expected = {
            f"{root}/2025_FBS_conferences.html",
            f"{root}/2025_FBS_conferences.json",
            f"{root}/W0_FBS_comparison.html",
            f"{root}/FINAL_FBS_comparison.html",
            f"{root}/acc/W0_FBS_standings.html",
            f"{root}/acc/FINAL_FBS_standings.html",
            f"{root}/sec/W0_FBS_standings.html",
            f"{root}/sec/FINAL_FBS_standings.html",
        }
        self.assertEqual(set(artifacts), expected)
        overview = artifacts[f"{root}/2025_FBS_conferences.html"].decode()
        self.assertIn("Checkpoint: <strong>FINAL</strong>", overview)
        self.assertIn("82.0", overview)
        self.assertIn("Historical reconstruction", overview)
        self.assertIn("No championship game", overview)
        self.assertIn("../history/2025_FBS_progression.html", overview)
        self.assertIn("./FINAL_FBS_comparison.html", overview)
        self.assertIn("./W0_FBS_comparison.html", overview)
        self.assertIn('aria-current="page">Conference overview', overview)

        comparison = artifacts[f"{root}/W0_FBS_comparison.html"].decode()
        self.assertIn("80.0", comparison)
        self.assertIn("1-0-0", comparison)
        self.assertIn("Unknown phase", comparison)
        self.assertIn("No games", comparison)
        self.assertIn("FCS", comparison)
        self.assertIn("FBS Independents", comparison)
        self.assertIn("width=device-width, initial-scale=1", comparison)
        self.assertIn("overflow:auto", comparison)
        self.assertIn("position:sticky;top:0", comparison)
        self.assertNotIn("<script", comparison.lower())
        self.assertNotIn("http://", comparison.lower())
        self.assertIn("../2025_CFB.html", comparison)
        self.assertIn("./acc/W0_FBS_standings.html", comparison)
        self.assertIn('<a href="./W0_FBS_comparison.html" aria-current="page">W0</a>', comparison)
        self.assertIn("./FINAL_FBS_comparison.html", comparison)

        final_comparison = artifacts[f"{root}/FINAL_FBS_comparison.html"].decode()
        self.assertLess(
            final_comparison.index('<th scope="row">Gamma</th>'),
            final_comparison.index('<th scope="row">Delta</th>'),
        )

        standings = artifacts[f"{root}/acc/W0_FBS_standings.html"].decode()
        self.assertIn("Conference record", standings)
        self.assertIn("Overall record", standings)
        self.assertIn("CORS points", standings)
        self.assertIn("National CORS rank", standings)
        self.assertIn("Position", standings)
        self.assertIn("../W0_FBS_comparison.html", standings)
        self.assertIn("../../2025_CFB.html", standings)
        self.assertIn("../../history/2025_FBS_progression.html", standings)
        self.assertIn('<a href="./W0_FBS_standings.html" aria-current="page">W0</a>', standings)
        self.assertIn("./FINAL_FBS_standings.html", standings)

        payload = json.loads(artifacts[f"{root}/2025_FBS_conferences.json"])
        self.assertEqual(payload["default_checkpoint"], "FINAL")
        self.assertEqual([item["checkpoint"] for item in payload["checkpoints"]], ["W0", "FINAL"])
        self.assertEqual(payload["checkpoints"][0]["standings"][0]["rows"][0]["team"], "Alpha")
        independent_record = next(
            item
            for item in payload["checkpoints"][0]["interconference"]
            if item["opponent"] == "FBS Independents"
            and item["conference"] == "ACC"
        )
        self.assertEqual(independent_record["unknown"]["wins"], 1)
        self.assertNotIn('"school"', artifacts[f"{root}/2025_FBS_conferences.json"].decode())
        self.assertIn("provenance_class", payload)

    def test_checkpoint_duplicates_population_and_alias_collisions_fail_closed(self) -> None:
        w0 = _reference("W0")
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((w0, w0))

        reduced = replace(
            w0,
            standings=(ConferenceStandings("ACC", w0.standings[0].rows[:1]), w0.standings[1]),
        )
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((w0, reduced))

        collision = _rename_conference(w0, "SEC", "Atlantic Coast Conference")
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((collision,))

        unknown = _rename_conference(w0, "SEC", "Unknown League")
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((unknown,))

    def test_direct_constructed_nonfinite_and_unsafe_values_fail_at_output_boundary(self) -> None:
        reference = _reference("FINAL", final=True)
        bad_comparison = replace(reference.comparisons[0], mean=float("nan"))
        bad_reference = replace(
            reference,
            comparisons=(bad_comparison,) + reference.comparisons[1:],
        )
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((bad_reference,))

        acc_independent = next(
            item
            for item in reference.interconference
            if item.conference == "ACC" and item.opponent == "FBS Independents"
        )
        bad_interconference = replace(acc_independent, combined=Record(1, 0, 0))
        bad_reference = replace(
            reference,
            interconference=tuple(
                bad_interconference if item is acc_independent else item
                for item in reference.interconference
            ),
        )
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((bad_reference,))

    def test_projection_reason_is_visible_and_unknown_at_cutoff_is_safe(self) -> None:
        reference = _reference("W0")
        projection = replace(
            reference.projections[0],
            status="unavailable",
            participants=(),
            contenders=(),
            selection_basis=None,
            site_state="unavailable",
            neutral_site=None,
            reason="ambiguous_game_timing",
        )
        reference = replace(reference, projections=(projection, reference.projections[1]))
        artifacts = build_conference_artifacts((reference,))
        comparison = artifacts[
            "cfb/years/2025/conferences/W0_FBS_comparison.html"
        ].decode()
        self.assertIn("Game timing ambiguous at cutoff", comparison)

        unsafe_row = object.__new__(StandingsRow)
        object.__setattr__(unsafe_row, "school", "<script>alert(1)</script>")
        object.__setattr__(unsafe_row, "conference", "ACC")
        object.__setattr__(unsafe_row, "conference_record", Record(1, 0, 0))
        object.__setattr__(unsafe_row, "overall_record", Record(1, 0, 0))
        object.__setattr__(unsafe_row, "cors", 80.0)
        object.__setattr__(unsafe_row, "national_rank", 1)
        object.__setattr__(unsafe_row, "position", 1)
        object.__setattr__(unsafe_row, "display_order", 1)
        bad_standings = ConferenceStandings(
            "ACC", (unsafe_row, reference.standings[0].rows[1])
        )
        bad_reference = replace(
            reference,
            standings=(bad_standings, reference.standings[1]),
        )
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts((bad_reference,))

    def test_explicit_slug_mapping_is_not_a_normalizer(self) -> None:
        self.assertEqual(conference_slug("Big Ten Conference"), "big-ten")
        self.assertEqual(conference_slug("American Athletic"), "american")
        self.assertEqual(conference_slug("Mid-American"), "mac")
        with self.assertRaises(ConferenceRenderingError):
            conference_slug("Big Ten Football")

    def test_independents_are_a_separate_distribution_not_a_comparison(self) -> None:
        reference = _reference("W0")
        independent_comparison = RatingComparison(
            conference="FBS Independents",
            available=False,
            reason="empty_remainder",
            member_count=1,
            mean=40.0,
            median=40.0,
            top_count=1,
            top_mean=40.0,
            remainder_count=0,
            remainder_mean=None,
            top_gap=None,
        )
        with self.assertRaises(ConferenceRenderingError):
            build_conference_artifacts(
                (replace(reference, comparisons=reference.comparisons + (independent_comparison,)),)
            )

    def test_empty_remainder_preserves_known_distribution_fields(self) -> None:
        reference = _reference("W0")
        acc_row = reference.standings[0].rows[0]
        acc_comparison = RatingComparison(
            conference="ACC",
            available=False,
            reason="empty_remainder",
            member_count=1,
            mean=80.0,
            median=80.0,
            top_count=1,
            top_mean=80.0,
            remainder_count=0,
            remainder_mean=None,
            top_gap=None,
        )
        unavailable = replace(
            reference.projections[0],
            status="unavailable",
            participants=(),
            contenders=(),
            selection_basis=None,
            site_state="unavailable",
            neutral_site=None,
            reason="no_qualifying_results",
        )
        reduced = replace(
            reference,
            standings=(ConferenceStandings("ACC", (acc_row,)), reference.standings[1]),
            comparisons=(acc_comparison, reference.comparisons[1]),
            projections=(unavailable, reference.projections[1]),
        )
        overview = build_conference_artifacts((reduced,))[
            "cfb/years/2025/conferences/2025_FBS_conferences.html"
        ].decode()
        self.assertIn(
            "<td>1</td><td>80.0</td><td>80.0</td><td>1</td><td>80.0</td>"
            "<td>0</td><td>Unavailable</td><td>Unavailable</td>",
            overview,
        )

    def test_hosted_projection_and_checkpoint_navigation_keep_identity_visible(self) -> None:
        reference = _reference("W0")
        hosted = replace(
            reference.projections[0],
            site_state="hosted",
            host_team="Alpha",
            neutral_site=False,
        )
        reference = replace(reference, projections=(hosted, reference.projections[1]))
        artifacts = build_conference_artifacts((reference, _reference("FINAL", final=True)))
        comparison = artifacts[
            "cfb/years/2025/conferences/W0_FBS_comparison.html"
        ].decode()
        self.assertIn("Hosted by Alpha", comparison)
        self.assertIn("./FINAL_FBS_comparison.html", comparison)


if __name__ == "__main__":
    unittest.main()
