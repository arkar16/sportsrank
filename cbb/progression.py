"""Static week-by-week CORS ranking progression pages and JSON exports for basketball."""
from html import escape
import json

from cfb.conference_pages import conference_route
from cfb.reference_markup import school_label

_INLINE_STYLE = (
    "<style>.archive-progression .table-scroll{max-height:70vh;border:1px solid var(--rule);isolation:isolate}"
    ".archive-progression table{border-collapse:separate;border-spacing:0}"
    ".archive-progression thead th{position:sticky;top:0;z-index:2;min-width:4.5rem}"
    ".archive-progression tbody th{position:sticky;left:0;z-index:1;background:var(--paper)}"
    ".archive-progression thead th:first-child{left:0;z-index:3}"
    ".archive-progression th:first-child{min-width:9rem}"
    ".archive-progression td.points,.archive-progression td.rank{text-align:right}</style>"
)


def _checkpoint_label(key):
    if key == "FINAL":
        return "Final"
    if key == "W0":
        return "Preseason"
    return f"Week {int(key[1:])}"


def _checkpoint_href(year, key):
    if key == "FINAL":
        return f"/cbb/seasons/{year}/weeks/final.html"
    return f"/cbb/seasons/{year}/weeks/{int(key[1:])}.html"


def write_progression(root, year, label, checkpoints, rankings, season_routes, presentation, *, model, finals):
    """Write seasons/<year>/progression.html and progression.json."""
    ordered_keys = [f'W{c["week"]}' for c in checkpoints]
    if finals and "FINAL" in rankings:
        ordered_keys.append("FINAL")
    latest_rows = rankings[ordered_keys[-1]]
    roster = sorted(latest_rows, key=lambda r: (r.school.casefold(), r.school))
    by_key = {key: {r.school: r for r in rankings[key]} for key in ordered_keys}

    json_rows = []
    html_rows = []
    for entry in roster:
        school = entry.school
        conf = entry.conference or "—"
        conf_cell = (
            f'<a href="{escape(conference_route(conf, year, sport="basketball"))}">{escape(conf)}</a>'
            if conf and conf != "—" else "—"
        )
        cells = []
        point_map = {}
        for key in ordered_keys:
            row = by_key[key].get(school)
            if row is None:
                cells.append('<td class="num">—</td><td class="num">—</td>')
                point_map[key] = {"available": False}
            else:
                cells.append(f'<td class="points num">{row.rating:.2f}</td><td class="rank num">{row.rank}</td>')
                point_map[key] = {"available": True, "points": float(row.rating), "rank": row.rank}
        html_rows.append(
            f'<tr><th scope="row">{school_label(school, season_routes)}</th><td>{conf_cell}</td>'
            + "".join(cells) + "</tr>"
        )
        json_rows.append({"team": school, "conference": conf, "checkpoints": point_map})

    payload = {
        "schema_version": 1,
        "sport": "cbb",
        "classification": "D1",
        "season": year,
        "season_label": label,
        "model_version": model,
        "checkpoints": [{"key": k, "label": _checkpoint_label(k)} for k in ordered_keys],
        "rows": json_rows,
    }
    json_path = root / f"seasons/{year}/progression.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")

    nav_links = [
        '<a href="progression.json">Ratings and ranks (JSON)</a>',
        *(f'<a href="{_checkpoint_href(year, k)}">{_checkpoint_label(k)}</a>' for k in ordered_keys),
    ]
    headers = (
        '<th scope="col">Team</th><th scope="col">Conference</th>'
        + "".join(
            f'<th scope="col" class="num">{_checkpoint_label(k)} Points</th>'
            f'<th scope="col" class="num">{_checkpoint_label(k)} Rank</th>'
            for k in ordered_keys
        )
    )
    title = f"{label} ranking progression"
    markup = (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)} | SportsRank basketball</title>'
        f'<link rel="stylesheet" href="/cfb/assets/record-book.css">{_INLINE_STYLE}</head>'
        f'<body class="record-book">'
        f'<nav class="archive-nav" aria-label="Archive">'
        f'<a href="/cbb/seasons/{year}/index.html">{escape(label)} season</a> · '
        f'<a href="/cbb/performance/{year}/index.html">Model performance</a></nav>'
        f'<main class="archive-content archive-progression"><h1>{escape(title)}</h1>'
        f'<nav aria-label="Checkpoints">{"".join(nav_links)}</nav>'
        f'<p id="table-help">Scroll for later weeks. {escape(model)}.</p>'
        f'<div class="table-scroll" role="region" aria-label="Ranking progression" '
        f'aria-describedby="table-help" tabindex="0"><table><thead><tr>{headers}</tr></thead>'
        f'<tbody>{"".join(html_rows)}</tbody></table></div></main></body></html>\n'
    )
    html_path = root / f"seasons/{year}/progression.html"
    html_path.write_bytes(presentation.render(markup.encode(), archive=False))
