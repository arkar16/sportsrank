"""Add report navigation and layout without rewriting archived HTML."""

from collections.abc import Callable
from html import unescape
import re


_PERFORMANCE = re.compile(r"cfb/performance/(?:[0-9]{4}/)?index\.html")
_FORECAST_RESULTS = re.compile(
    r"cfb/years/(?P<year>[0-9]{4})/spread/(?P=year)"
    r"(?:_W[0-9]+_(?:FBS|FCS)_spread_results|_(?:FBS|FCS)_forecast_results)\.html"
)
_TABLE = re.compile(
    r'(?P<scroll><div\b(?=[^>]*\sclass="(?:scroll|forecast-table-scroll)")[^>]*>\s*)?'
    r'<table\b[^>]*>\s*(?:<colgroup>.*?</colgroup>\s*)?'
    r'<thead\b[^>]*>\s*<tr\b[^>]*>(?P<columns>.*?)</tr>'
    r'.*?</table>(?(scroll)\s*</div>)',
    re.DOTALL,
)
_JUMP_LABELS = {
    "Provider comparison": "provider-comparison",
    "Forecasts awaiting results": "pending-forecasts",
    "Weekly performance": "weekly-performance",
    "Game results": "game-results",
    "Summary": "summary",
    "Results": "results",
    "Weekly summary": "weekly-summary",
    "Weekly omissions": "weekly-omissions",
    "Season summary": "season-summary",
    "Season omissions": "season-omissions",
}
_LEGACY_METRICS = re.compile(
    r'(?P<ats><p>Against The Spread:</p>\s*<ul>.*?</ul>)\s*'
    r'(?P<straight><p>Straight Up:</p>\s*<ul>.*?</ul>)',
    re.DOTALL,
)
_IDS = re.compile(r"""\s+id\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>/]+))""", re.I)


def decorate_report(markup: str, relative: str, ui: Callable[[str], str]) -> str:
    """Enhance the decoded source of a model performance or forecast results page."""
    is_index = relative == "cfb/performance/index.html"
    if not (_PERFORMANCE.fullmatch(relative) or _FORECAST_RESULTS.fullmatch(relative)):
        return markup

    head_end = re.search(r"</head\s*>", markup, re.I)
    body_end = re.search(r"</body\s*>", markup, re.I)
    title = re.search(r"<h1\b[^>]*>.*?</h1\s*>", markup, re.I | re.S)
    if not (head_end and body_end and title):
        return markup
    content = markup[title.end():body_end.start()]
    tables = list(_TABLE.finditer(content))
    if not tables:
        return markup

    occupied = {unescape("".join(groups)) for groups in _IDS.findall(markup)
                if any(groups)}
    jumps = []
    additions = []

    def jump(position: int, name: str, label: str) -> str:
        target = f"sr-report-{name}"
        suffix = 2
        while target in occupied:
            target = f"sr-report-{name}-{suffix}"
            suffix += 1
        occupied.add(target)
        jumps.append((position, f'<a href="#{target}">{label}</a>'))
        return f'id="{target}" tabindex="-1"'

    def wrap(start: int, end: int, opening: str, closing: str) -> None:
        additions.extend(((start, opening), (end, closing)))

    # Put comparable summaries side by side on a wide page. Their reading order
    # and explanatory paragraphs stay together on a phone.
    scoring = content.find('<p class="metrics">')
    market = re.search(r'<h2>Provider comparison</h2>\s*<p class="metrics">.*?</p>', content, re.S)
    if market and 0 <= scoring < market.start():
        notes = re.match(r'(?:\s*<p>teamrankings [^<]*</p>)?(?:\s*<p><a href="\.\./market-lines\.json">[^<]*</a></p>)?',
                         content[market.end():])
        additions.extend((
            (scoring, '<div class="sr-report-comparison"><section><h2>Forecast accuracy</h2>'),
            (market.start(), '</section><section>'),
            (market.end() + notes.end(), '</section></div>'),
        ))

    headings = list(re.finditer(r"<h2\b[^>]*>([^<]+)</h2>", content))
    for heading in headings:
        label = heading[1]
        if label in _JUMP_LABELS:
            target = jump(heading.start(), _JUMP_LABELS[label], label)
            wrap(heading.start(), heading.end(), f"<div {target}>", "</div>")

    if is_index:
        download = re.search(r'<p><a href="seasons\.csv">', content)
        position = download.start() if download else tables[0].start()
        target = jump(position, "season-summaries", "Season summaries")
        additions.append((position, f"<h2 {target}>Season summaries</h2>"))
    elif not headings or tables[0].start() < headings[0].start():
        position = tables[0].start()
        target = jump(position, "game-results", "Game results")
        additions.append((position, f"<h2 {target}>Game results</h2>"))

    downloads = re.search(r'<p><a href="games\.csv">.*?</p>', content, re.S)
    if downloads:
        target = jump(downloads.start(), "downloads", "Downloads")
        wrap(downloads.start(), downloads.end(), f'<div class="sr-report-links" {target}>', "</div>")
    inputs = re.search(r"<details\b[^>]*><summary>Saved inputs</summary>.*?</details>", content, re.S)
    if inputs:
        target = jump(inputs.start(), "saved-inputs", "Saved inputs")
        wrap(inputs.start(), inputs.end(), f"<div {target}>", "</div>")
    weeks = re.search(r'<p><a href="[0-9]{4}_W[0-9]+_(?:FBS|FCS)_spread_results\.html">.*?</p>',
                      content, re.S)
    if weeks:
        target = jump(weeks.start(), "weeks", "Weekly results")
        wrap(weeks.start(), weeks.end(),
             f'<nav class="sr-report-weeks" aria-label="Weekly forecast results" {target}>', "</nav>")

    legacy_metrics = _LEGACY_METRICS.search(content)
    if legacy_metrics:
        additions.append((legacy_metrics.start(), '<div class="sr-report-legacy-metrics">'))
        for group in ("ats", "straight"):
            start, end = legacy_metrics.span(group)
            wrap(start, end, "<div>", "</div>")
        additions.append((legacy_metrics.end(), "</div>"))

    for table in tables:
        columns = re.findall(r"<th\b[^>]*>([^<]*)</th>", table["columns"])
        if columns[:3] in (["Week", "Home", "Away"], ["week", "home_team", "away_team"]):
            wrap(table.start(), table.end(), '<div class="sr-report-matchups">', "</div>")
        elif columns[:2] == ["home_team", "away_team"]:
            wrap(table.start(), table.end(), '<div class="sr-report-legacy-matchups">', "</div>")

    nav = "".join(link for _, link in sorted(jumps))
    wrapper = '<div class="sr-report sr-report-index">' if is_index else '<div class="sr-report">'
    chunks = [
        markup[:head_end.start()],
        ui('<link rel="stylesheet" href="/cfb/assets/report-pages.css">'),
        markup[head_end.start():title.end()],
        ui(wrapper),
        ui(f'<nav class="sr-report-nav" aria-label="On this page">{nav}</nav>'),
    ]
    cursor = 0
    # Archived documents often put adjacent elements on the same byte offset.
    # Close any wrapper ending there before opening the next one.
    for position, addition in sorted(additions, key=lambda item: (item[0], not item[1].startswith("</"))):
        chunks.extend((content[cursor:position], ui(addition)))
        cursor = position
    chunks.extend((content[cursor:], ui("</div>"), markup[body_end.start():]))
    return "".join(chunks)
