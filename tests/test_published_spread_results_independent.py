"""Independent oracle for the retained 2026 weekly spread publication.

The retained weekly spread pages are the expected forecast source.  The
retained final-results page supplies completed scores.  The checks below grade
those values with a small local implementation and compare the resulting
numbers with a candidate export.  This deliberately does not call the
renderer, forecast ledger, or grading helpers used to produce the candidate.

Set ``SPORTSRANK_RETAINED_FORECAST_ROOT`` to the directory containing the
retained ``cfb/years/2026/spread`` pages.  Set
``SPORTSRANK_CORRECTED_FORECAST_ROOT`` when checking a corrected export; set it
to the old export to reproduce the pre-fix failure.  An optional
``SPORTSRANK_REBUILT_FORECAST_ROOT`` compares a second corrected export for
rebuild stability.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
import os
from pathlib import Path
import unittest


SPREAD_RELATIVE = Path("cfb/years/2026/spread")
RESULTS_PAGE = "2026_FBS_forecast_results.html"
WEEKS = (0, 1, 2, 3, 4)


class _Tables(HTMLParser):
    """Read literal HTML tables without importing the production renderer."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
            self.tables.append(self._table)
        elif tag == "tr":
            self._row = []
        elif self._row is not None and tag in {"td", "th"}:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None:
            assert self._row is not None
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._table is not None:
                self._table.append(self._row)
            self._row = None
        elif tag == "table":
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _tables(path: Path) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.tables


def _headered_rows(path: Path, header: tuple[str, ...]) -> list[dict[str, str]]:
    for table in _tables(path):
        if not table or tuple(table[0]) != header:
            continue
        return [
            dict(zip(header, row))
            for row in table[1:]
            if len(row) == len(header)
        ]
    raise AssertionError(f"{path} has no table with header {header!r}")


SOURCE_HEADER = (
    "week", "home_team", "away_team", "neutral_site", "home_cors",
    "away_cors", "spread_value", "spread",
)
RESULT_HEADER = (
    "Week", "Home", "Away", "Graded Forecast (home handicap)",
    "Predicted Winner", "Home score", "Away score", "Actual home margin",
    "Straight-up", "CORS line coverage", "Margin error", "Score corrected",
    "Disposition",
)
SUMMARY_HEADER = (
    "Evaluated", "Straight-up record", "Straight-up accuracy",
    "Straight-up denominator", "Ties", "Pick'em", "Unknown picks",
    "CORS coverage", "Coverage denominator", "Pushes", "MAE", "RMSE",
    "Margin denominator",
)


@dataclass(frozen=True)
class Expected:
    week: int
    home: str
    away: str
    source_margin: Decimal
    home_points: int
    away_points: int
    actual_margin: Decimal
    selection: str
    straight_up: str
    coverage: str
    error: Decimal

    @property
    def key(self) -> tuple[int, str, str]:
        return self.week, self.home, self.away


def _source_rows(root: Path) -> dict[tuple[int, str, str], dict[str, str]]:
    rows: dict[tuple[int, str, str], dict[str, str]] = {}
    for week in WEEKS:
        path = root / SPREAD_RELATIVE / f"2026_W{week}_FBS_spread.html"
        for row in _headered_rows(path, SOURCE_HEADER):
            if not row["week"].isdigit():
                continue
            key = (int(row["week"]), row["home_team"], row["away_team"])
            if key in rows:
                raise AssertionError(f"duplicate retained forecast {key!r}")
            rows[key] = row
    return rows


def _completed_scores(root: Path) -> dict[tuple[int, str, str], tuple[int, int]]:
    path = root / SPREAD_RELATIVE / RESULTS_PAGE
    result_rows = _headered_rows(path, RESULT_HEADER)
    scores: dict[tuple[int, str, str], tuple[int, int]] = {}
    for row in result_rows:
        if not row["Week"].isdigit():
            continue
        if not row["Home score"].isdigit() or not row["Away score"].isdigit():
            continue
        key = (int(row["Week"]), row["Home"], row["Away"])
        scores[key] = (int(row["Home score"]), int(row["Away score"]))
    return scores


def _expected(root: Path) -> dict[tuple[int, str, str], Expected]:
    sources = _source_rows(root)
    scores = _completed_scores(root)
    expected: dict[tuple[int, str, str], Expected] = {}
    for key, source in sources.items():
        if key not in scores:
            # A retained page may include the next scheduled slate.  It is
            # source evidence for a future/pending row, not an eligible grade.
            continue
        home_points, away_points = scores[key]
        source_margin = Decimal(source["spread_value"])
        actual_margin = Decimal(home_points - away_points)
        # The retained page's spread_value is a predicted home margin.  A
        # negative margin selects the away team.  Exact rounded-zero legacy
        # rows retain margin evidence but have no recoverable selected side.
        if source_margin > 0:
            selection = "home"
        elif source_margin < 0:
            selection = "away"
        else:
            selection = "unknown"
        if selection == "unknown":
            straight_up = "Ungraded"
            coverage = "Ungraded"
        else:
            straight_up = (
                "Tie"
                if actual_margin == 0
                else "Correct"
                if (actual_margin > 0) == (selection == "home")
                else "Incorrect"
            )
            oriented_actual = actual_margin if selection == "home" else -actual_margin
            line = abs(source_margin)
            coverage = (
                "Cover" if oriented_actual > line else
                "Push" if oriented_actual == line else
                "No cover"
            )
        expected[key] = Expected(
            week=key[0], home=key[1], away=key[2], source_margin=source_margin,
            home_points=home_points, away_points=away_points,
            actual_margin=actual_margin, selection=selection,
            straight_up=straight_up, coverage=coverage,
            error=abs(actual_margin - source_margin),
        )
    return expected


def _as_decimal(value: str) -> Decimal:
    if value in {"", "—"}:
        raise AssertionError(f"expected a numeric value, got {value!r}")
    return Decimal(value)


def _summary(expected: list[Expected]) -> dict[str, str]:
    straight = Counter(item.straight_up for item in expected)
    coverage = Counter(item.coverage for item in expected)
    denominator = coverage["Cover"] + coverage["No cover"]
    mae = sum((item.error for item in expected), Decimal(0)) / len(expected)
    rmse = (
        sum((item.error * item.error for item in expected), Decimal(0))
        / len(expected)
    ).sqrt()
    return {
        "Evaluated": str(len(expected)),
        "Straight-up record": f"{straight['Correct']}-{straight['Incorrect']}",
        "Straight-up denominator": str(straight['Correct'] + straight['Incorrect']),
        "Ties": str(straight['Tie']),
        "Pick'em": "0",
        "Unknown picks": str(straight['Ungraded']),
        "CORS coverage": f"{100 * coverage['Cover'] / denominator:.2f}%",
        "Coverage denominator": str(denominator),
        "Pushes": str(coverage['Push']),
        "MAE": f"{mae:.3f}",
        "RMSE": f"{rmse:.3f}",
        "Margin denominator": str(len(expected)),
    }


def _target_rows(
    root: Path,
    filename: str = RESULTS_PAGE,
) -> dict[tuple[int, str, str], dict[str, str]]:
    path = root / SPREAD_RELATIVE / filename
    rows = _headered_rows(path, RESULT_HEADER)
    result: dict[tuple[int, str, str], dict[str, str]] = {}
    for row in rows:
        if not row["Week"].isdigit():
            continue
        key = (int(row["Week"]), row["Home"], row["Away"])
        if key in result:
            raise AssertionError(f"duplicate candidate result {key!r}")
        result[key] = row
    return result


def _target_summary(
    root: Path,
    filename: str = RESULTS_PAGE,
) -> dict[str, str]:
    path = root / SPREAD_RELATIVE / filename
    for table in _tables(path):
        if table and tuple(table[0]) == SUMMARY_HEADER:
            if len(table) != 2:
                raise AssertionError(f"{path} has unexpected summary row count")
            return dict(zip(SUMMARY_HEADER, table[1]))
    raise AssertionError(f"{path} has no season summary table")


def _assert_export(
    testcase: unittest.TestCase,
    root: Path,
    expected: dict[tuple[int, str, str], Expected],
    source_keys: set[tuple[int, str, str]],
) -> None:
    rows = _target_rows(root)
    expected_by_week = {
        week: {
            key: item for key, item in expected.items() if item.week == week
        }
        for week in WEEKS
    }

    def assert_rows(
        target_rows: dict[tuple[int, str, str], dict[str, str]],
        expected_rows: dict[tuple[int, str, str], Expected],
    ) -> None:
        for key, item in expected_rows.items():
            row = target_rows.get(key)
            if row is None:
                raise AssertionError(f"candidate export omitted retained forecast {key!r}")
            with testcase.subTest(game=key):
                testcase.assertEqual(row["Disposition"], "Evaluated")
                testcase.assertEqual(
                    _as_decimal(row["Graded Forecast (home handicap)"]),
                    -item.source_margin,
                )
                expected_winner = (
                    item.home if item.selection == "home" else
                    item.away if item.selection == "away" else "—"
                )
                testcase.assertEqual(row["Predicted Winner"], expected_winner)
                testcase.assertEqual(int(row["Home score"]), item.home_points)
                testcase.assertEqual(int(row["Away score"]), item.away_points)
                testcase.assertEqual(int(row["Actual home margin"]), int(item.actual_margin))
                testcase.assertEqual(row["Straight-up"], item.straight_up)
                testcase.assertEqual(row["CORS line coverage"], item.coverage)
                testcase.assertEqual(_as_decimal(row["Margin error"]), item.error)

    assert_rows(rows, expected)

    # Preserve every retained source identity in the public result table.  A
    # source row without a completed score remains pending with no derived
    # grading fields; it must not disappear or be recomputed from later data.
    for key in source_keys:
        row = rows.get(key)
        if row is None:
            raise AssertionError(f"candidate export omitted retained source {key!r}")
        if key not in expected:
            with testcase.subTest(pending_game=key):
                testcase.assertEqual(row["Disposition"], "Pending")

    for week, expected_rows in expected_by_week.items():
        weekly_filename = f"2026_W{week}_FBS_spread_results.html"
        weekly_rows = _target_rows(root, weekly_filename)
        assert_rows(weekly_rows, expected_rows)
        weekly_summary = _target_summary(root, weekly_filename)
        expected_weekly_summary = _summary(list(expected_rows.values()))
        for field, value in expected_weekly_summary.items():
            testcase.assertEqual(weekly_summary[field], value, f"W{week} {field}")

    # Every retained source forecast with a completed score must be graded
    # exactly once.  The candidate may contain more rows for other games, but
    # future or pending games must not become evaluated merely because a
    # spread page exists for them.
    expected_keys = set(expected)
    evaluated = {
        key for key, row in rows.items() if row["Disposition"] == "Evaluated"
    }
    testcase.assertTrue(expected_keys <= evaluated)
    for key, row in rows.items():
        if row["Disposition"] == "Pending":
            testcase.assertEqual(row["Graded Forecast (home handicap)"], "—")
            testcase.assertEqual(row["Predicted Winner"], "—")
            testcase.assertEqual(row["Home score"], "—")
            testcase.assertEqual(row["Away score"], "—")
            testcase.assertEqual(row["Actual home margin"], "—")
            testcase.assertEqual(row["Straight-up"], "—")
            testcase.assertEqual(row["CORS line coverage"], "—")
            testcase.assertEqual(row["Margin error"], "—")

    expected_summary = _summary(list(expected.values()))
    actual_summary = _target_summary(root)
    for field, value in expected_summary.items():
        testcase.assertEqual(actual_summary[field], value, field)


def _configured_root(name: str, default: Path | None = None) -> Path | None:
    value = os.environ.get(name)
    if value:
        return Path(value)
    if default is not None and default.exists():
        return default
    return None


class PublishedSpreadResultsIndependentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.retained_root = _configured_root("SPORTSRANK_RETAINED_FORECAST_ROOT")
        if cls.retained_root is None:
            raise unittest.SkipTest(
                "set SPORTSRANK_RETAINED_FORECAST_ROOT to run the retained-page oracle"
            )
        cls.expected = _expected(cls.retained_root)

    def test_retained_source_is_complete_and_independent_oracle_is_stable(self) -> None:
        self.assertEqual(len(self.expected), 158)
        self.assertEqual(Counter(item.week for item in self.expected.values()), Counter({0: 8, 1: 43, 2: 49, 3: 57, 4: 1}))
        self.assertEqual(
            Counter(
                "positive" if item.source_margin > 0 else
                "negative" if item.source_margin < 0 else "zero"
                for item in self.expected.values()
            ),
            Counter({"positive": 110, "negative": 45, "zero": 3}),
        )
        self.assertEqual(
            {(item.week, item.home, item.away) for item in self.expected.values() if item.selection == "unknown"},
            {
                (2, "Michigan", "Oklahoma"),
                (2, "Minnesota", "Mississippi State"),
                (3, "Wake Forest", "Miami"),
            },
        )
        self.assertEqual(
            _summary(list(self.expected.values())),
            {
                "Evaluated": "158", "Straight-up record": "107-48",
                "Straight-up denominator": "155", "Ties": "0", "Pick'em": "0",
                "Unknown picks": "3", "CORS coverage": "50.65%",
                "Coverage denominator": "154", "Pushes": "1", "MAE": "18.082",
                "RMSE": "22.801", "Margin denominator": "158",
            },
        )

    def test_corrected_export_grades_retained_completed_forecasts(self) -> None:
        target = _configured_root("SPORTSRANK_CORRECTED_FORECAST_ROOT")
        if target is None:
            self.skipTest(
                "set SPORTSRANK_CORRECTED_FORECAST_ROOT to check a candidate export"
            )
        _assert_export(
            self, target, self.expected, set(_source_rows(self.retained_root))
        )

    def test_corrected_rebuild_preserves_rows_and_summaries(self) -> None:
        first = _configured_root("SPORTSRANK_CORRECTED_FORECAST_ROOT")
        rebuilt = _configured_root("SPORTSRANK_REBUILT_FORECAST_ROOT")
        if first is None or rebuilt is None:
            self.skipTest(
                "set both corrected and rebuilt export roots for rebuild stability"
            )
        source_keys = set(_source_rows(self.retained_root))
        _assert_export(self, first, self.expected, source_keys)
        _assert_export(self, rebuilt, self.expected, source_keys)
        self.assertEqual(_target_rows(first), _target_rows(rebuilt))
        self.assertEqual(_target_summary(first), _target_summary(rebuilt))


if __name__ == "__main__":
    unittest.main()
