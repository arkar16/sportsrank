"""Static game excitement tables for the football watch guide and score sheets."""
from __future__ import annotations

from html import escape
from pathlib import Path
import re

from alltime_excitement import football_anchor
from excitement_grades import SCALE_LINK, graded_number

from .archive_markup import ui
from .game_excitement import GameAppeal, Matchup, score_for
from .historical_rebuild import _school
from .reference_archive import _rows, _score
from .reference_markup import school_label

_TABLE = re.compile(r"<table\b[^>]*>.*?</table>", re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.I | re.S)
_FORECAST = re.compile(r"spread/\d{4}_W\d+_FBS_spread\.html")
_RESULT = re.compile(r"(?:data/results/(?:weekly_results/)?\d{4}(?:_W\d+)?_FBS_results|spread/\d{4}_W\d+_FBS_spread_results)\.html")
_METHOD = "https://github.com/arkar16/sportsrank/blob/main/docs/plans/2026-game-excitement-scoring-v1.md"
ESTIMATE_NOTE = "* AEV estimated from the available scoring totals and comparable games."


def aev_label(score: GameAppeal | None, *, sport="cfb", plain=False):
    if score is None or score.aev is None:
        return "&mdash;"
    basis = score.evidence_label
    number = f'{score.aev:.1f}{"*" if score.estimated else ""}'
    if plain:
        return f"{number} · {basis}"
    return f'{graded_number(sport, "AEV", number)}<small> · {basis}</small>'


def bev_label(score, *, sport="cfb", plain=False):
    if not score:
        return "&mdash;"
    if plain:
        return score.bev + (f" · {score.bev_basis}" if score.bev_basis else "")
    value = graded_number(sport, "BEV", score.bev)
    return value + (f"<small> · {score.bev_basis}</small>" if score.bev_basis else "")


def add_score_columns(markup: bytes, directory: Path, path: Path, scores, year: int) -> bytes:
    """Add reader cells inside removable UI markers, preserving archive source bytes."""
    relative = path.relative_to(directory).as_posix()
    forecast = bool(_FORECAST.fullmatch(relative))
    if not forecast and not _RESULT.fullmatch(relative):
        return markup
    try:
        text = markup.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = markup.decode("latin-1")
        encoding = "latin-1"
    table = _TABLE.search(text)
    if not table:
        return markup
    rows = list(_rows(path))
    if not rows:
        return markup
    rendered = list(_ROW.finditer(table[0]))
    if len(rendered) != len(rows) + 1:
        raise ValueError(f"game table differs from archive rows in {path}")
    report = "Home" in rows[0]
    heads = '<th scope="col"><abbr title="Before Excitement Value">BEV</abbr></th>'
    if not forecast:
        heads += '<th scope="col"><abbr title="After Excitement Value">AEV</abbr></th>'
    cells = [heads]
    has_estimate = False
    for row in rows:
        week, home, away, neutral = (("Week", "Home", "Away", "Neutral site") if report else
                                     ("week", "home_team", "away_team", "neutral_site"))
        if not {week, home, away, neutral} <= row.keys():
            return markup  # A preserved legacy report with a different schema.
        key = Matchup(int(row[week]), _school(row[home]), _school(row[away]), row[neutral].lower() == "true")
        hs, aws = ("Home score", "Away score") if report else ("home_score", "away_score")
        observed = tuple(_score(None if row.get(field) in ("—", "Pending") else row.get(field))
                         for field in (hs, aws))
        score = score_for(scores, key, observed)
        extra = f'<td class="num">{bev_label(score)}</td>'
        if not forecast:
            has_final = None not in observed
            if score and score.aev is not None and has_final and observed != score.final:
                raise ValueError(f"AEV differs from the displayed game's final in {path}")
            extra += f'<td class="num">{aev_label(score if has_final else None)}</td>'
            if has_final and score and score.estimated:
                has_estimate = True
        cells.append(extra)

    rows_iter = iter(cells)
    decorated = _ROW.sub(lambda m: m[0][:-5] + ui(next(rows_iter)) + m[0][-5:], table[0])
    link = f'<a href="/cfb/years/{year}/excitement/index.html">{year} game excitement</a>'
    after = "" if forecast else " AEV measures game drama."
    if has_estimate:
        after += " " + ESTIMATE_NOTE
    note = ui(f'<p>BEV rates pregame appeal on a 0&ndash;100 scale.{after} {link}</p>')
    # Insert outside any existing presentation marker, so stripping the added
    # columns and link restores the same input on every subsequent build.
    text = text[:table.start()] + note + decorated + text[table.end():]
    return text.encode(encoding, errors="xmlcharrefreplace")


def season_guide(year, scores, routes, directory=None, *, completed_season=False):
    def table(games, pending):
        title = "Completed games by AEV"
        if pending:
            title = "Games awaiting a verified final" if completed_season else "Upcoming games by BEV"
        if not games:
            return ""
        rows = []
        for key, score in games:
            folder = "spread" if pending else "data/results/weekly_results"
            kind = "spread" if pending else "results"
            week = f"../{folder}/{year}_W{key.week}_FBS_{kind}.html"
            link = (f'<a href="{week}">{key.week}</a>' if directory is None or
                    (directory / week.removeprefix("../")).is_file() else str(key.week))
            cells = (f'<td>{link}</td><td>{school_label(key.away, routes)}</td>'
                     f'<td>{school_label(key.home, routes)}</td><td class="num">{bev_label(score)}</td>')
            if not pending:
                hs, aws = score.final
                source = (f'<span data-cfbd-game="{escape(score.source_game)}">{aws}&ndash;{hs}</span>'
                          if score.source_game else f"{aws}&ndash;{hs}")
                cells += f'<td class="num">{source}</td><td class="num">{aev_label(score)}</td>'
            rows.append(f'<tr id="{football_anchor(key)}">{cells}</tr>')
        headers = ["Week", "Away", "Home", "BEV"] + ([] if pending else ["Score (away–home)", "AEV"])
        return (f'<h2>{title}</h2><div class="table-scroll" tabindex="0"><table><thead><tr>'
                + "".join(f'<th scope="col">{h}</th>' for h in headers) + "</tr></thead><tbody>"
                + "\n".join(rows) + "</tbody></table></div>")

    pending = sorted(((k, s) for k, s in scores.items() if s.final is None),
                     key=lambda item: (-float(item[1].bev), item[0]))
    completed = sorted(((k, s) for k, s in scores.items() if s.final is not None),
                       key=lambda item: (-(item[1].aev if item[1].aev is not None else -1), item[0]))
    estimated_note = ESTIMATE_NOTE + " " if any(s.estimated for s in scores.values()) else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{year} game excitement · CORS</title>
<link rel="stylesheet" href="/cfb/assets/record-book.css"></head><body><main>
<p><a href="../{year}_CFB.html">{year} college football</a></p><h1>{year} game excitement</h1>
<p>Before Excitement Value (BEV) rates a matchup from its pregame CORS ranks and predicted margin.
After Excitement Value (AEV) rates how long the score stayed close, lead changes, comebacks and overtime.
Both run from 0 to 100. Game data comes from CollegeFootballData. {SCALE_LINK}.</p>
<p>{estimated_note}A final-score estimate uses comparable games with the same or a nearby margin. A pregame baseline uses only information available before kickoff.</p>
{table(pending, True)}{table(completed, False)}
<p>BEV uses the preceding CORS v1.0.0 checkpoint, including for reconstructed historical forecasts.
A missing team rating uses a pregame baseline. These scores use no market bonus. <a href="bev-v1.json">Pregame inputs</a> · <a href="{_METHOD}">Scoring method</a></p>
</main></body></html>"""
