"""Offline historical correction into an isolated, unsealed review website.

The ordinary provider/calendar and production carryover contracts are unchanged.
This adapter implements ADR-0024's explicitly authorized archive compatibility.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import posixpath
from pathlib import Path
import shutil
import tempfile
from typing import Callable

from .carryover_registry import reconcile_previous_final
from .performance import _page, _read, build_performance_files, seasons
from .release import _row_table
from .ranking_engine import (
    FCS_CORS, PreviousFinal, preseason_ranking, records_for_week,
    week_ranking, spreads_for_week,
)
from .ranking_progression import build_progression_outputs
from .season_snapshot import SeasonSnapshot, _checksum
from .season_source import SourceGame, SourceTeam
from .week_calendar import EASTERN, WEEK_ONE_BOUNDARIES

POLICY = "historical-reconstruction/v1"
RESULT_COLUMNS = ("week", "home_team", "home_division", "home_score", "away_team",
                  "away_division", "away_score", "neutral_site")


def _json(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _school(value: str) -> str:
    # The same bounded legacy SJSU spelling repaired by the modern carryover registry.
    return value.replace("San Jos\ufffd State", "San José State")


def _score(value: str) -> int:
    number = Decimal(value)
    if not number.is_finite() or number < 0 or number != int(number):
        raise ValueError("historical score must be a nonnegative integer")
    return int(number)


def _bool(value: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise ValueError("invalid historical neutral-site value")
    return value.lower() == "true"


def local_day(value: str) -> date:
    """Date-only records retain their date; timestamp records are provider UTC."""
    if len(value) == 10:
        return date.fromisoformat(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(EASTERN).date()


def _key(row: dict, *, source: bool = False) -> tuple:
    suffix = "points" if source else "score"
    return (_school(row["home_team"]), _school(row["away_team"]),
            _score(row[f"home_{suffix}"]), _score(row[f"away_{suffix}"]),
            _bool(row["neutral_site"]))


class HistoricalInputs:
    """One immutable local CSV plus retained pages, shared across the whole run."""

    def __init__(self, website: Path, games_csv: Path):
        self.website = website
        raw = games_csv.read_bytes()
        self.source_sha = _sha(raw)
        self.games = defaultdict(list)
        identifiers = set()
        # Parse the exact bytes whose digest the review record retains.
        from io import StringIO
        for row in csv.DictReader(StringIO(raw.decode("utf-8-sig"))):
            year = int(row["season"])
            if year >= 2024:
                continue  # Modern corrected scores/calendar come from the retained site.
            identifier = (year, row["id"])
            if identifier in identifiers:
                raise ValueError(f"duplicate historical source game ID for {year}")
            identifiers.add(identifier)
            self.games[year].append(row)

    def root(self, year: int) -> Path:
        return self.website / "cfb/years" / str(year)

    def roster(self, year: int) -> tuple[SourceTeam, ...]:
        root = self.root(year)
        path = root / f"rankings/{year}_FINAL_FBS_cors.html"
        if not path.exists():
            path = root / f"rankings/{year}_PRESEASON_FBS_cors.html"
        rows, _ = _read(path)
        teams = tuple(SourceTeam(_school(row["school"]), row["conference"]) for row in rows)
        if len({team.school for team in teams}) != len(teams):
            raise ValueError(f"duplicate archived team for {year}")
        return teams

    def prior_final(self, year: int) -> PreviousFinal:
        rows, _ = _read(self.root(year) / f"rankings/{year}_FINAL_FBS_cors.html")
        cors = {_school(row["school"]): float(Decimal(row["cors"])) for row in rows}
        wve = {_school(row["school"]): float(Decimal(row.get("wins_vs_expected") or "0"))
               for row in rows}
        if len(cors) != len(rows):
            raise ValueError("duplicate prior FINAL team")
        return PreviousFinal(cors, wve, year, "FBS")

    def snapshot(self, year: int) -> tuple[SeasonSnapshot, dict]:
        retained, result_sha = _read(self.root(year) / f"data/results/{year}_FBS_results.html")
        # Old provider exports can repeat the same played game under two IDs.
        # Equal semantic rows count once; conflicting scores fail below.
        unique, seen_rows, deduplicated = [], set(), []
        for row in retained:
            key = (int(row["week"]), *_key(row), row["home_division"], row["away_division"])
            if key in seen_rows:
                deduplicated.append({"week": int(row["week"]), "home": row["home_team"], "away": row["away_team"]})
            else:
                seen_rows.add(key)
                unique.append(row)
        retained = unique
        teams = self.roster(year)
        members = {team.school for team in teams}
        raw = self.games[year]
        regular = defaultdict(list)
        for row in raw:
            if row["season_type"] == "regular" and row["home_points"] and row["away_points"]:
                regular[_key(row, source=True)].append(row)
        origins = Counter()
        for row in retained:
            candidates = regular.get(_key(row), [])
            if len(candidates) == 1 and candidates[0]["start_date"]:
                day = local_day(candidates[0]["start_date"])
                monday = day - timedelta(days=day.weekday())
                origins[monday - timedelta(weeks=int(row["week"]) - 1)] += 1
        if year in WEEK_ONE_BOUNDARIES:
            boundary = WEEK_ONE_BOUNDARIES[year].date()
        elif origins:
            ranked_origins = origins.most_common()
            if len(ranked_origins) > 1 and ranked_origins[0][1] == ranked_origins[1][1]:
                raise ValueError(f"ambiguous archived Week 1 boundary for {year}")
            boundary = ranked_origins[0][0]
        else:
            raise ValueError(f"no dated regular-season Week 1 boundary for {year}")
        games = []
        moved = []
        retained_counts = Counter(_key(row) for row in retained)
        for row in retained:
            week = int(row["week"])
            candidates = regular.get(_key(row), [])
            if year < 2024 and week == 1 and len(candidates) == 1:
                if local_day(candidates[0]["start_date"]) < boundary:
                    week = 0
                    moved.append({"home": row["home_team"], "away": row["away_team"], "from": 1, "to": 0})
            games.append(SourceGame(
                week, _school(row["home_team"]), row["home_division"], _score(row["home_score"]),
                _school(row["away_team"]), row["away_division"], _score(row["away_score"]),
                _bool(row["neutral_site"]),
                provider_id=candidates[0]["id"] if len(candidates) == 1 else None,
                date=candidates[0]["start_date"] if len(candidates) == 1 else None,
                completed=True, disposition="completed",
            ))
        added = []
        for row in raw:
            if row["season_type"] != "postseason" or not ({_school(row["home_team"]), _school(row["away_team"])} & members):
                continue
            if not row["home_points"] or not row["away_points"]:
                raise ValueError(f"unscored historical postseason game for {year}")
            key = _key(row, source=True)
            day = local_day(row["start_date"])
            week = 1 + (day - boundary).days // 7
            if week < 1:
                raise ValueError("historical postseason precedes regular season")
            if retained_counts[key]:
                # Existing postseason records are relocated rather than counted twice.
                matches = [i for i, game in enumerate(games) if (
                    game.home_team, game.away_team, game.home_points, game.away_points,
                    game.neutral_site) == key and
                    (game.week == week or not regular.get(key))]
                if len(matches) > 1:
                    raise ValueError("ambiguous retained postseason game")
                if matches:
                    retained_counts[key] -= 1
                    from dataclasses import replace
                    games[matches[0]] = replace(games[matches[0]], week=week)
                    continue
            home_class = "fbs" if key[0] in members else row["home_classification"] or "unknown"
            away_class = "fbs" if key[1] in members else row["away_classification"] or "unknown"
            games.append(SourceGame(week, key[0], home_class, key[2], key[1], away_class,
                                    key[3], key[4], provider_id=row["id"], date=row["start_date"],
                                    completed=True, disposition="completed"))
            added.append({"id": row["id"], "week": week, "home": key[0], "away": key[1]})
        identities = [(game.week, game.home_team, game.away_team, game.neutral_site,
                       local_day(game.date) if game.date else None) for game in games]
        if len(set(identities)) != len(identities):
            duplicates = [key for key, count in Counter(identities).items() if count > 1]
            raise ValueError(f"duplicate corrected matchup in {year}: {duplicates}")
        metadata = {"schema_version": 3, "dataset_id": POLICY,
                    "complete_through_week": max(game.week for game in games)}
        state = {**metadata, "sport": "cfb", "classification": "FBS", "year": year}
        checksum = _checksum(state, teams, games)
        evidence = {"season": year, "snapshot_sha256": checksum,
                    "retained_results_sha256": result_sha, "week_one_boundary": boundary.isoformat(),
                    "boundary_votes": origins.get(boundary, 0), "added_games": added,
                    "week_zero_moves": moved, "deduplicated_results": deduplicated,
                    "result_count": len(games)}
        snapshot = SeasonSnapshot("cfb", "FBS", year, teams, tuple(games),
                                  metadata, checksum)
        return snapshot, evidence


def historical_carryover(year: int, prior: PreviousFinal, previous_members: set[str],
                         current_members: set[str]) -> tuple[PreviousFinal, dict]:
    """Apply the approved archive exception without weakening the engine validator."""
    if prior.year != year - 1 or set(prior.cors) != previous_members:
        raise ValueError("historical carryover is missing or mismatches the preceding archived FINAL")
    if year >= 2024:
        reconciled = reconcile_previous_final(year, "FBS", prior.cors,
                                              prior.wins_vs_expected, current_members)
        return PreviousFinal(reconciled.cors, reconciled.wins_vs_expected, year - 1, "FBS"), reconciled.evidence()
    added = sorted(current_members - previous_members)
    departed = sorted(previous_members - current_members)
    cors = {team: prior.cors[team] if team in previous_members else FCS_CORS for team in current_members}
    wve = {team: prior.wins_vs_expected.get(team, 0.0) if team in previous_members else 0.0
           for team in current_members}
    return PreviousFinal(cors, wve, year - 1, "FBS"), {"newly_listed": added, "departed": departed}


def _write(root: Path, relative: str, raw: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    href = posixpath.relpath("cfb/cfb.html", posixpath.dirname(relative))
    path.write_bytes(raw.replace(b"__CORS_HOME__", href.encode()))


def _data_page(title: str, rows: list[dict], columns: tuple[str, ...]) -> bytes:
    return _page(title, f'<p>Last updated: {datetime.now(timezone.utc).isoformat()}</p>'
                 + '<p>Historical reconstruction · original issued forecasts remain separate.</p>'
                 + _row_table(rows, columns), '<a href="__CORS_HOME__">CORS home</a>')


def rebuild_history(website: Path, games_csv: Path, output: Path, *,
                    from_season: int | None = None,
                    progress: Callable[[str], None] | None = None) -> dict:
    """Rebuild through the latest available season, atomically into a new directory."""
    website, games_csv, output = website.resolve(), games_csv.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(website) or website.is_relative_to(output) or games_csv.is_relative_to(output):
        raise ValueError("historical review output must be new and separate from all source inputs")
    inputs = HistoricalInputs(website, games_csv)
    years = seasons(website)
    if not years:
        raise ValueError("no archived seasons")
    if from_season is None:
        affected = []
        for year in years:
            members = {team.school for team in inputs.roster(year)}
            if any(row["season_type"] == "postseason" and
                   {_school(row["home_team"]), _school(row["away_team"])} & members
                   for row in inputs.games[year]):
                if inputs.snapshot(year)[1]["added_games"]:
                    affected.append(year)
        if not affected:
            raise ValueError("no missing historical postseason games found")
        from_season = min(affected)
    selected = [year for year in years if year >= from_season]
    if not selected or selected[0] != from_season or selected != list(range(from_season, max(years) + 1)):
        raise ValueError("historical rebuild requires an unbroken season chain")
    prior = inputs.prior_final(from_season - 1)
    previous_members = {team.school for team in inputs.roster(from_season - 1)}
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = []
    with tempfile.TemporaryDirectory(prefix="historical-review-", dir=output.parent) as temporary:
        site = Path(temporary) / "site"
        shutil.copytree(website, site)
        for year in selected:
            if progress:
                progress(f"Reconstructing {year}")
            snapshot, record = inputs.snapshot(year)
            members = {team.school for team in snapshot.teams}
            carried, changes = historical_carryover(year, prior, previous_members, members)
            record["membership"] = changes
            record["prior_final_sha256"] = _sha(_json(asdict(prior)))
            pre = preseason_ranking(snapshot, carried)
            target = snapshot.complete_through_week
            # Legacy W0 was PRESEASON. Retain that URL as a compatibility alias
            # when no scored W0 game exists; do not halve carryover by inventing
            # an empty scored checkpoint before the first real week.
            rankings = {0: week_ranking(snapshot, 0, previous_final=carried)
                        if any(game.week == 0 for game in snapshot.games) else pre}
            for week in range(1, target + 1):
                rankings[week] = week_ranking(snapshot, week, rankings[week - 1])
            prefix = f"cfb/years/{year}"
            rank_columns = ("rank", "school", "conference", "record", "win_pct", "cors", "mov", "sos", "expected_wins", "wins_vs_expected")
            for checkpoint, rows in [("PRESEASON", pre), *((f"W{week}", rows) for week, rows in rankings.items())]:
                _write(site, f"{prefix}/rankings/{year}_{checkpoint}_FBS_cors.html",
                       _data_page(f"{year} {checkpoint} CORS rankings", list(rows), rank_columns))
            has_final = (inputs.root(year) / f"rankings/{year}_FINAL_FBS_cors.html").exists()
            if has_final:
                _write(site, f"{prefix}/rankings/{year}_FINAL_FBS_cors.html",
                       _data_page(f"{year} FINAL CORS rankings", rankings[target], rank_columns))
            results = [{"week": game.week, "home_team": game.home_team,
                        "home_division": game.home_classification, "home_score": game.home_points,
                        "away_team": game.away_team, "away_division": game.away_classification,
                        "away_score": game.away_points, "neutral_site": game.neutral_site}
                       for game in snapshot.games]
            _write(site, f"{prefix}/data/results/{year}_FBS_results.html",
                   _data_page(f"{year} results", results, RESULT_COLUMNS))
            if year < 2024:
                # Retain unplayed/canceled slate rows, while replacing completed
                # rows with the corrected chronology and restoring postseason.
                slate_path = inputs.root(year) / f"data/slate/{year}_FBS_slate.html"
                inherited_slate = _read(slate_path)[0] if slate_path.exists() else []
                original_results, _ = _read(inputs.root(year) / f"data/results/{year}_FBS_results.html")
                completed_keys = {(int(row["week"]), _school(row["home_team"]),
                                   _school(row["away_team"]), _bool(row["neutral_site"]))
                                  for row in original_results}
                pending = [row for row in inherited_slate if
                           (int(row["week"]), _school(row["home_team"]),
                            _school(row["away_team"]), _bool(row["neutral_site"])) not in completed_keys]
                slate_columns = tuple(key for key in RESULT_COLUMNS if not key.endswith("score"))
                full_slate = [{key: row[key] for key in slate_columns} for row in results] + pending
                _write(site, f"{prefix}/data/slate/{year}_FBS_slate.html",
                       _data_page(f"{year} season slate", full_slate, slate_columns))
            for week in range(target + 1):
                rows = [row for row in results if row["week"] == week]
                _write(site, f"{prefix}/data/results/weekly_results/{year}_W{week}_FBS_results.html",
                       _data_page(f"{year} Week {week} results", rows, RESULT_COLUMNS))
                slate = [{key: row[key] for key in RESULT_COLUMNS if not key.endswith("score")} for row in rows]
                _write(site, f"{prefix}/data/slate/weekly_slate/{year}_W{week}_FBS_slate.html",
                       _data_page(f"{year} Week {week} slate", slate, tuple(slate[0]) if slate else
                                  tuple(key for key in RESULT_COLUMNS if not key.endswith("score"))))
                predictions = spreads_for_week(snapshot, week, pre if week == 0 else rankings[week - 1])
                _write(site, f"{prefix}/reconstructed/{year}_W{week}_FBS_spread.html",
                       _data_page(f"{year} Week {week} reconstructed predictions", predictions,
                                  ("week", "home_team", "away_team", "neutral_site", "home_cors", "away_cors", "spread", "home_margin", "home_handicap", "predicted_winner")))
                records = records_for_week(snapshot, week)
                _write(site, f"{prefix}/data/records/{year}_W{week}_FBS_records.html",
                       _data_page(f"{year} Week {week} records", records, tuple(records[0])))
            # Rebuild the already-public progression contract where it exists.
            history_path = f"{prefix}/history/{year}_FBS_progression"
            if (site / (history_path + ".html")).exists():
                _, rendered, serialized = build_progression_outputs(
                    snapshot, rankings, pre, final_rows=rankings[target] if has_final else None,
                    phase="final" if has_final else "week", target_week=target,
                    dataset_id=POLICY, carryover_identity=record["prior_final_sha256"])
                _write(site, history_path + ".html", rendered.encode())
                _write(site, history_path + ".json", serialized.encode())
            links = ''.join(f'<li>Week {week}: <a href="../rankings/{year}_W{week}_FBS_cors.html">Rankings</a> · '
                            f'<a href="../data/results/weekly_results/{year}_W{week}_FBS_results.html">Results</a> · '
                            f'<a href="../reconstructed/{year}_W{week}_FBS_spread.html">Reconstructed predictions</a></li>'
                            for week in range(target + 1))
            _write(site, f"{prefix}/history/reconstruction.html", _page(f"{year} reconstructed checkpoints",
                   f'<p>{len(record["added_games"])} postseason games restored.</p><ul>{links}</ul>',
                   f'<a href="../../../performance/{year}/index.html">Model performance</a>'))
            year_page = site / prefix / f"{year}_CFB.html"
            if year_page.exists():
                raw = year_page.read_bytes()
                for week in range(target + 1):
                    missing = f"spread/{year}_W{week}_FBS_spread.html"
                    if not (year_page.parent / missing).exists():
                        replacement = f"reconstructed/{year}_W{week}_FBS_spread.html"
                        raw = raw.replace(missing.encode(), replacement.encode())
                link = b'\n<p><a href="history/reconstruction.html">Reconstructed historical checkpoints</a></p>\n'
                if b"</body>" in raw:
                    raw = raw.replace(b"</body>", link + b"</body>", 1)
                else:
                    raw += link
                year_page.write_bytes(raw)
            record["through_week"] = target
            record["final"] = has_final
            record["final_sha256"] = _sha(_json(rankings[target])) if has_final else None
            evidence.append(record)
            if not has_final and year != selected[-1]:
                raise ValueError("intermediate season has no FINAL")
            prior = PreviousFinal({row["school"]: row["cors"] for row in rankings[target]},
                                  {row["school"]: row["wins_vs_expected"] for row in rankings[target]}, year, "FBS")
            previous_members = members
        for filename, kind, choose in (("nc_FBS_CFB_output.html", "National champion", 0),
                                       ("wt_FBS_CFB_output.html", "Worst team", -1)):
            rows = []
            for year in years:
                final = site / f"cfb/years/{year}/rankings/{year}_FINAL_FBS_cors.html"
                if final.exists():
                    ranking, _ = _read(final)
                    rows.append({"year": year, **ranking[choose], "kind": kind})
            _write(site, "cfb/history/" + filename, _data_page(kind + " history", rows,
                   ("year", "school", "conference", "record", "win_pct", "cors", "kind")))
        if progress:
            progress("Generating complete reconstructed performance")
        for relative, raw in build_performance_files(site, reconstruct_all=True).items():
            _write(site, relative, raw)
        report = {"schema": POLICY, "purpose": "unsealed_local_review",
                  "source_csv_sha256": inputs.source_sha, "from_season": from_season,
                  "through_season": selected[-1], "seasons": evidence,
                  "added_postseason_games": sum(len(record["added_games"]) for record in evidence)}
        _write(site, "historical-review.json", _json(report))
        # Ordinary release records/receipts in the inherited tree do not validate
        # this correction. Never promote or hand it to a production publisher.
        site.rename(output)
    return report
