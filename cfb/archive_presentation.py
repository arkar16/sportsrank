"""Reader-facing headings and navigation for previously published record sheets."""

from datetime import datetime, timezone
from functools import lru_cache
from html import escape, unescape
from pathlib import PurePosixPath
import posixpath
import re

from .archive_markup import original as _original, replace_copy, restore_original, ui
from .report_presentation import decorate_report


_FIELD_ATTR = re.compile(rb' data-sr-field aria-label="[^"<>]*"')
_END = re.compile(r"</body\s*>\s*</html\s*>", re.I)
_SEASON = re.compile(r"cfb/(?:years|performance)/([0-9]{4})/")
_RECORD = re.compile(
    r"[0-9]{4}(?:_(W[0-9]+|PRESEASON|FINAL))?_FBS_"
    r"(spread_results|forecast_results|cors|records|results|slate|spread|teams|progression)\.html"
)
_NAMES = {
    "cors": "CORS rankings", "records": "team records", "results": "scores",
    "slate": "schedule", "spread": "CORS forecasts", "teams": "teams",
    "spread_results": "forecast results", "forecast_results": "forecast results",
    "progression": "ranking progression",
}
_FIELD = re.compile(r'(<th(?:\s+[^<>]*)?)(>)([a-z][a-z_0-9]*)(</th>)')
_FIELD_NAMES = {
    "year": "Season", "school": "Team", "win_pct": "Win rate", "cors": "CORS rating",
    "mov": "Avg. scoring margin", "sos": "Schedule strength", "wins_vs_expected": "Wins vs. expected",
    "home_cors": "Home CORS rating", "away_cors": "Away CORS rating", "spread": "CORS spread",
    "spread_value": "Spread value", "game_id": "Game ID",
    "neutral_site_x": "Neutral site (forecast)", "neutral_site_y": "Neutral site (result)",
    "su_correct": "Winner correct", "ats_correct": "Spread correct", "ats_result": "Spread result",
}
_NAV_BLOCK = re.compile(
    r'<(?P<tag>nav|p)>(?P<links>\s*(?:<a href="[^"<>]+">[^<>]+</a>\s*(?:[·|]\s*)?)+)</(?P=tag)>'
)
_ANCHOR = re.compile(r'<a href="([^"<>]+)">([^<>]+)</a>')
_NAV_LABELS = {"Season", "CORS home", "All seasons", "Model performance"}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_STAMP = re.compile(
    r"(?P<paragraph><p>)?Last updated: "
    r"(?P<date>\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?)(?(paragraph)</p>)"
)
_DATA_TABLE = re.compile(r'<table\b[^>]*class="dataframe"[^>]*>\s*<thead>\s*<tr[^>]*>(.*?)</tr>', re.S)
_PRIMARY_FIELDS = {
    "honors": {"year", "school", "record", "cors"},
    "cors": {"rank", "school", "record", "cors"},
    "results": {"week", "home_team", "home_score", "away_team", "away_score"},
    "records": {"school", "wins", "losses", "ties", "record"},
    "slate": {"week", "home_team", "away_team", "neutral_site"},
    "spread": {"week", "home_team", "away_team", "spread"},
}


def restore_chrome(raw: bytes) -> bytes:
    """Public headers retain their machine names; old navigation stays recoverable."""
    raw = raw.replace(b'<html lang="en" data-sr-lang>', b"<html>")
    raw = _FIELD_ATTR.sub(b"", raw)
    return restore_original(raw)


def _checkpoint(key: str) -> str:
    return f"Week {key[1:]}" if key.startswith("W") else key.title()


def _field(match: re.Match) -> str:
    start, _, name, end = match.groups()
    if "aria-label=" in start:
        return match[0]
    label = _FIELD_NAMES.get(name, name.replace("_", " ").capitalize())
    return f'{start} data-sr-field aria-label="{label}">{name}{end}'


@lru_cache(maxsize=2048)
def updated_time(value: str, label: str = "Updated") -> str:
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return ""
    zone = ""
    if stamp.tzinfo:
        stamp = stamp.astimezone(timezone.utc)
        zone = " UTC"
    clock = "" if len(value) == 10 else f" · {stamp:%H:%M}{zone}"
    return (f'<time class="archive-date" datetime="{escape(value.replace(" ", "T"))}" '
            f'title="{escape(value)}">{escape(label)} {_MONTHS[stamp.month - 1]} {stamp.day}, {stamp.year}{clock}</time>')


def _phone_columns(markup: str, kind: str, weekly: bool) -> str:
    """Place primary figures together on narrow screens; retain the full native table."""
    primary = _PRIMARY_FIELDS.get(kind)
    if not primary or "forecast-games" in markup or markup.count("<table") != 1:
        return markup
    table = _DATA_TABLE.search(markup)
    if not table:
        return markup
    fields = re.findall(r"<th(?:\s+[^>]*)?>([^<>]*)</th>", table[1])
    selected = primary - {"week"} if weekly else primary
    visible = selected.intersection(fields)
    if len(visible) < 2 or len(visible) == len(fields):
        return markup
    hidden = ",".join(f":nth-child({i})" for i, field in enumerate(fields, 1) if field not in selected)
    rule = ('<style>@media screen and (max-width:650px){'
            f'.archive-content:has(.archive-columns input:not(:checked)) .dataframe tr>:is({hidden})'
            '{display:none}}</style>')
    choice = '<label class="archive-columns"><input type="checkbox"> Show all columns</label>'
    opening = re.search(r'<div\b[^>]*class="(?:table-scroll|scroll|forecast-table-scroll)"[^>]*>\s*$', markup[:table.start()])
    position = opening.start() if opening else table.start()
    markup = markup[:position] + ui(choice) + markup[position:]
    return markup.replace("</head>", ui(rule) + "</head>", 1)


def _progression_copy(markup: str, year: str, targets: set[str]) -> str:
    data = f'<a href="{year}_FBS_progression.json">'
    for source, replacement in {
        "<p>CORS points and national rank are shown at each checkpoint.</p>": "",
        "Scroll across for later checkpoints. Team names stay visible.": "Scroll for later weeks.",
        "<summary>Data provenance</summary>": "<summary>Sources</summary>",
        data + "Progression data</a>": data + "Ratings and ranks (JSON)</a>",
    }.items():
        markup = markup.replace(source, replace_copy(source, replacement))
    if f"/cfb/years/{year}/{year}_CFB.html" in targets:
        season = f'<a href="../{year}_CFB.html">Season</a>'
        markup = markup.replace(season, replace_copy(season))
    markup = re.sub(r"(?<=>)(W\d+|PRESEASON|FINAL) ranking(?=</a>)",
                    lambda m: replace_copy(m[0], _checkpoint(m[1])), markup)
    markup = re.sub(r"<p>Checkpoint: (W\d+|FINAL|PRESEASON)</p>",
                    lambda m: replace_copy(m[0], "" if m[1] == "FINAL" else f"<p>Through {_checkpoint(m[1])}</p>"), markup)
    return markup


def _archive_title(markup: str, title: str) -> str:
    old = re.search(r"<title>([^<>]*)</title>", markup, re.I)
    if not old or "--" in old[1]:
        return markup
    version = re.search(r"\bCORS (v[0-9]+(?:\.[0-9]+)+)", old[1])
    if version:
        title += " · CORS " + version[1]
    if unescape(old[1]) == title:
        return markup
    # A title element treats HTML comments as literal text. Retain the whole
    # element outside the replacement so browser tabs show only the new title.
    return markup[:old.start()] + replace_copy(old[0], f"<title>{escape(title)}</title>") + markup[old.end():]


def decorate_archive(markup: str, relative: str, targets: set[str]) -> str:
    """Decorate a document from its route and the real destinations in this export."""
    name = PurePosixPath(relative).name
    season = _SEASON.match(relative)
    year = season[1] if season else ""
    record = _RECORD.fullmatch(name)
    key, kind = record.groups() if record else ("", "")
    crumbs = {}

    def link(href: str, label: str) -> None:
        if href in targets and href not in ("/" + relative, "/cfb/cfb.html", "/cfb/performance/index.html"):
            crumbs.setdefault(href, label)

    if year:
        link(f"/cfb/years/{year}/{year}_CFB.html", f"{year} season")
        if key:
            link(f"/cfb/years/{year}/checkpoints/{key}.html", _checkpoint(key))

    heading = ""
    family = kind
    if record:
        period = _checkpoint(key) if key else year
        subject = "reconstructed forecasts" if "/reconstructed/" in relative else _NAMES[kind]
        heading = f"{period} {subject}"
        if kind == "progression":
            markup = _progression_copy(markup, year, targets)
        elif "/reconstructed/" in relative and kind == "spread" and key and key.startswith("W"):
            week = int(key[1:])
            prior = f"W{week - 1}" if week else "PRESEASON"
            label = _checkpoint(prior)
            url = f"/cfb/years/{year}/rankings/{year}_{prior}_FBS_cors.html"
            ratings = f'<a href="{url}">{label} ratings</a>' if url in targets else f"{label} ratings"
            note = "<p>Calculated from prior-week CORS ratings.</p>"
            markup = markup.replace(note, replace_copy(note, f"<p>From {ratings}.</p>"))
    elif name == "reconstruction.html":
        family, heading = "reconstruction", f"{year} reconstructed forecasts"
    elif name in {"nc_FBS_CFB_output.html", "wt_FBS_CFB_output.html"}:
        family = kind = "honors"
        prefix = name[:2].upper()
        heading = "CORS national champions" if prefix == "NC" else "CORS last-place finishers"
        link(f"/cfb/years/history/CORS_{prefix}_FBS_CFB.html", "Archived record sheet")

        def link_year(match: re.Match) -> str:
            year_url = f"/cfb/years/{match[2]}/{match[2]}_CFB.html"
            if year_url not in targets:
                return match[0]
            return match[1] + ui(f'<a href="{year_url}">') + match[2] + ui("</a>") + "</td>"

        markup = re.sub(r"(<tr>\s*<td>)([0-9]{4})</td>", link_year, markup)

    # Some early generators closed the document before writing its actual table.
    end = _END.search(markup)
    if end and markup[end.end():].strip():
        markup = markup[:end.start()] + _original(end[0]) + markup[end.end():]
        markup += ui("</body></html>")
    markup = decorate_report(markup, relative)

    def absorb_navigation(match: re.Match) -> str:
        links = _ANCHOR.findall(match["links"])
        if "--" in match[0] or any(label not in _NAV_LABELS for _, label in links):
            return match[0]
        resolved = []
        for raw_href, label in links:
            href = posixpath.normpath(posixpath.join("/", posixpath.dirname(relative), unescape(raw_href)))
            # The same literal home href was once emitted at different depths.
            if label == "CORS home":
                href = "/cfb/cfb.html"
            elif label == "Season" and href not in targets and year:
                href = f"/cfb/years/{year}/{year}_CFB.html"
            if href not in targets:
                return match[0]
            resolved.append((href, label))
        for href, label in resolved:
            destination = _SEASON.match(href.lstrip("/"))
            if label == "Season" and destination:
                label = f"{destination[1]} season"
            if label == "Model performance" and destination:
                label = f"{destination[1]} model performance"
            link(href, label)
        return _original(match[0])

    markup = _NAV_BLOCK.sub(absorb_navigation, markup)
    if heading:
        markup = _archive_title(markup, f"{year} {heading}" if year and key else heading)
        old = re.search(r"<h1>([^<>]*)</h1>", markup)
        if old and "--" not in old[1] and unescape(old[1]) != heading:
            markup = markup[:old.start(1)] + _original(old[1]) + ui(escape(heading)) + markup[old.end(1):]
    markup = _phone_columns(markup, kind, bool(key))
    markup = _FIELD.sub(_field, markup)

    def format_stamp(match: re.Match) -> str:
        readable = updated_time(match["date"])
        if not readable:
            return match[0]
        if match["paragraph"]:
            # Weekly forecast regeneration recognizes this exact source paragraph.
            return ui("<div hidden>") + match[0] + ui("</div><p>" + readable + "</p>")
        return ui("<span hidden>") + match[0] + ui("</span>" + readable)

    markup = _STAMP.sub(format_stamp, markup)
    links = " · ".join(f'<a href="{escape(href)}">{escape(label)}</a>' for href, label in crumbs.items())
    navigation = f'<nav class="archive-nav" aria-label="Archive">{links}</nav>' if links else ""
    main = not re.search(r"<main\b", markup, re.I)
    if main:
        css = "archive-content" + (" archive-" + family if family else "")
        navigation += f'<main class="{css}">'
        # An appended end marker already contains both closing tags; keep UI markers disjoint.
        if markup.endswith(ui("</body></html>")):
            markup = markup.removesuffix(ui("</body></html>")) + ui("</main></body></html>")
        else:
            markup = re.sub(r"</body\s*>", lambda m: ui("</main>") + m[0], markup, count=1, flags=re.I)
    markup = re.sub(r"<body\b[^>]*>", lambda m: m[0] + ui(navigation), markup, count=1, flags=re.I)
    markup = markup.replace("<html>", '<html lang="en" data-sr-lang>', 1)
    if not re.search(r"<!doctype\s+html\b", markup[:512], re.I):
        markup = ui("<!doctype html>") + markup
    return markup
