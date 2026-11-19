"""Present archived forecast grades, market comparisons and their sources."""

from html import unescape
import re

from .archive_markup import replace_copy, ui


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
_HEADINGS = {"Provider comparison": "Market comparison", "Forecasts awaiting results": "Ungraded forecasts"}
_SHORT_JUMPS = {"Provider comparison": "Market", "Weekly performance": "By week", "Game results": "Games"}
_PICKS = r'\s*<ul>\s*<li>Correct Predictions: ([\d,]+)</li>\s*<li>Accuracy: ([\d.]+%)</li>\s*</ul>'
_LEGACY_METRICS = re.compile(
    r'<p>Total Games: ([\d,]+)</p>\s*<p>Against The Spread:</p>' + _PICKS
    + r'\s*<p>Straight Up:</p>' + _PICKS + r'(?:\s*<hr\s*/?>)?'
)
_SOURCES = re.compile(
    r'(<a href="\.\./\.\./\.\./(?P<path>cfb/years/(?P<year>\d{4})/[^"<>]+/'
    r'(?P=year)_(?P<when>W\d+|PRESEASON|FINAL)_FBS_(?P<kind>cors|results|spread|slate)\.html)">)'
    r'(?P=path)</a>'
)
_SCORING_NOTE = (
    "CORS line coverage measures whether the selected favorite covered the CORS spread. Pushes are reported "
    "separately; zero lines have no winner selection. MAE/RMSE use every evaluated margin, including ties "
    "and zero lines. Lower margin error is better."
)
_MARKET_NOTE = (
    "teamrankings 2013–2020; Bovada from 2021. API returned spreads, without a closing-line claim or "
    "provider substitution. Every nonzero CORS advantage selects a side; equal lines have no pick. "
    "ATS excludes pushes. CORS and provider errors above use the same games."
)
_EMPTY_MARKET = (
    "Market ATS — (0–0; 0 pushes; 0 no picks)<br>0 paired games · CORS MAE — / provider MAE — points"
    "<br>CORS RMSE — / provider RMSE — points · 0 missing lines"
)
_COUNT = r"([\d,]+)"
_SEASON_EXCLUSIONS = re.compile(r"<p>" + _COUNT + " unavailable · " + _COUNT + " pending scores · "
                                + _COUNT + r" non-FBS games excluded\.</p>")
_WEEK_EXCLUSIONS = re.compile(r"<p>" + _COUNT + " non-FBS games excluded · " + _COUNT + " pending scores · "
                              + _COUNT + " unavailable predictions</p>")
_IDS = re.compile(r"""\s+id\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>/]+))""", re.I)


def _count(number: str, singular: str, plural: str) -> str:
    return f"{number} {singular if number == '1' else plural}"


def _exclusions(source: str, missing: str, pending: str, excluded: str) -> str:
    entries = []
    if missing != "0":
        entries.append(_count(missing, "missing forecast", "missing forecasts"))
    if pending != "0":
        entries.append(_count(pending, "score pending", "scores pending"))
    if excluded != "0":
        entries.append(_count(excluded, "non-FBS game excluded", "non-FBS games excluded"))
    return replace_copy(source, "<p>" + " · ".join(entries) + "</p>" if entries else "")


def _source_link(match: re.Match) -> str:
    when = match["when"]
    week = f"Week {when[1:]}" if when.startswith("W") else when.title()
    kind = {"cors": "rankings", "results": "scores", "spread": "spreads", "slate": "schedule"}[match["kind"]]
    if match["kind"] == "spread" and "/reconstructed/" in match["path"]:
        kind = "reconstructed spreads"
    return match[1] + replace_copy(match["path"], f"{week} {kind}") + "</a>"


def _legacy_record(match: re.Match) -> str:
    games, spreads, spread_rate, winners, winner_rate = match.groups()
    return replace_copy(match[0],
        f'<p class="metrics"><strong>{_count(games, "game", "games")}</strong>'
        f'<br>Against the CORS spread · {spreads} correct ({spread_rate})'
        f'<br>Straight up · {winners} correct ({winner_rate})</p>')


def _metrics_paragraph(match: re.Match, before_market: bool, fbs: bool) -> str:
    body = match[1]
    if body == _EMPTY_MARKET and before_market:
        return replace_copy(match[0], "<p>Market lines start in 2013.</p>")
    if "--" in body or not (body.startswith("Market ATS ") or " · Winner accuracy " in body):
        return match[0]
    units = ("FBS matchup", "FBS matchups") if fbs else ("game", "games")
    body = re.sub(r"^<strong>([\d,]+) games</strong>",
                  lambda m: f'<strong>{_count(m[1], *units)}</strong>', body)
    body = re.sub(r"; 0 (?:ties|pushes|no picks)(?=[;)])", "", body)
    body = body.removesuffix(" · 0 missing lines")
    singular = {"ties": "tie", "pushes": "push", "no picks": "no pick", "missing lines": "missing line",
                "paired games": "paired game"}
    body = re.sub(r"(?<![\d,])1 (ties|pushes|no picks|missing lines|paired games)\b",
                  lambda m: "1 " + singular[m[1]], body)
    return replace_copy(match[0], f'<p class="metrics">{body}</p>') if body != match[1] else match[0]


def _season_count(match: re.Match) -> str:
    seasons = _count(match[1], "season", "seasons")
    missing = " · " + _count(match[2], "missing forecast", "missing forecasts") if match[2] != "0" else ""
    return replace_copy(match[0], f"<p>{seasons}{missing}</p>")


def _report_copy(markup: str, relative: str) -> str:
    # Match the published wording, so an unfamiliar qualification in a future
    # report survives until an editor has read it.
    season = re.fullmatch(r"cfb/performance/(\d{4})/index\.html", relative)
    before_market = bool(season and int(season[1]) < 2013 and _EMPTY_MARKET in markup and _MARKET_NOTE in markup)
    fbs = relative.startswith("cfb/performance/") or "_FBS_" in relative
    eligibility = "Only games between FBS teams count. " if fbs else ""
    paragraphs = {
        "CORS model performance across seasons.": "",
        "CORS predictions compared with final FBS-versus-FBS scores.": "",
        "Saved CORS spreads; results are not yet in this report.": "",
        _SCORING_NOTE: (
            f"<details><summary>Scoring rules</summary><p>{eligibility}"
            "CORS line coverage is the percentage of favorites that beat the CORS spread, excluding pushes. "
            "An even spread has no pick. Mean absolute error (MAE) and root mean square error (RMSE) "
            "include ties and even spreads.</p></details>"
        ),
        _MARKET_NOTE: "" if before_market else (
            "<details><summary>Lines and grading</summary><p>SportsRank uses TeamRankings for 2013–2020 "
            "and Bovada from 2021. Missing lines stay out of the comparison. Closing-line status is unknown. "
            "CORS picks whenever the spreads differ. Market ATS is the share of picks that beat the market "
            "spread, excluding pushes. "
            "Both margin errors use the same games.</p></details>"
        ),
    }
    for text, visible in paragraphs.items():
        source = f"<p>{text}</p>"
        markup = markup.replace(source, replace_copy(source, visible))
    markup = re.sub(r'<p class="metrics">([^\n]*?)</p>', lambda m: _metrics_paragraph(m, before_market, fbs), markup)
    for old, label in _HEADINGS.items():
        markup = markup.replace(f"<h2>{old}</h2>", f"<h2>{replace_copy(old, label)}</h2>")
    markup = markup.replace("<summary>Saved inputs</summary>", "<summary>" + replace_copy("Saved inputs", "Sources") + "</summary>")
    for old, label in {
        "Selected lines and retrieval provenance": "Market lines (JSON)", "Download season summaries": "Seasons (CSV)",
        "Game CSV": "Games (CSV)", "Weekly CSV": "Weeks (CSV)", "Report JSON": "Report (JSON)",
    }.items():
        markup = markup.replace(f">{old}</a>", ">" + replace_copy(old, label) + "</a>")
    markup = _SOURCES.sub(_source_link, markup)
    markup = _SEASON_EXCLUSIONS.sub(lambda m: _exclusions(m[0], m[1], m[2], m[3]), markup)
    markup = _WEEK_EXCLUSIONS.sub(lambda m: _exclusions(m[0], m[3], m[2], m[1]), markup)
    markup = re.sub(r"<p>([\d,]+) seasons · ([\d,]+) unavailable\.</p>", _season_count, markup)
    return markup


def decorate_report(markup: str, relative: str) -> str:
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
    legacy = _LEGACY_METRICS.search(content)
    for heading in headings:
        label = heading[1]
        if label in _JUMP_LABELS:
            target = jump(heading.start(), _JUMP_LABELS[label], _SHORT_JUMPS.get(label, _HEADINGS.get(label, label)))
            if label == "Summary" and legacy and heading.end() <= legacy.start():
                wrap(heading.start(), legacy.end(), f'<section {target} aria-label="Forecast grades">', "</section>")
            else:
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
        target = jump(inputs.start(), "saved-inputs", "Sources")
        wrap(inputs.start(), inputs.end(), f"<div {target}>", "</div>")
    weeks = re.search(r'<p><a href="[0-9]{4}_W[0-9]+_(?:FBS|FCS)_spread_results\.html">.*?</p>',
                      content, re.S)
    if weeks:
        target = jump(weeks.start(), "weeks", "Weekly results")
        wrap(weeks.start(), weeks.end(),
             f'<nav class="sr-report-weeks" aria-label="Weekly forecast results" {target}>', "</nav>")

    for table in tables:
        columns = re.findall(r"<th\b[^>]*>([^<]*)</th>", table["columns"])
        if columns[:3] in (["Week", "Home", "Away"], ["week", "home_team", "away_team"]):
            wrap(table.start(), table.end(), '<div class="sr-report-matchups">', "</div>")
        elif columns[:2] == ["home_team", "away_team"]:
            wrap(table.start(), table.end(), '<div class="sr-report-legacy-matchups">', "</div>")

    nav = ""
    if len(jumps) > 1 and not legacy:
        links = "".join(link for _, link in sorted(jumps))
        nav = ui(f'<nav class="sr-report-nav" aria-label="On this page">{links}</nav>')
    wrapper = '<div class="sr-report sr-report-index">' if is_index else '<div class="sr-report">'
    chunks = [
        markup[:head_end.start()],
        ui('<link rel="stylesheet" href="/cfb/assets/report-pages.css">'),
        markup[head_end.start():title.end()],
        ui(wrapper),
        nav,
    ]
    cursor = 0
    # Archived documents often put adjacent elements on the same byte offset.
    # Close any wrapper ending there before opening the next one.
    for position, addition in sorted(additions, key=lambda item: (item[0], not item[1].startswith("</"))):
        chunks.extend((content[cursor:position], ui(addition)))
        cursor = position
    chunks.extend((content[cursor:], ui("</div>"), markup[body_end.start():]))
    rendered = "".join(chunks)
    if legacy:
        rendered = rendered.replace("<h2>Summary</h2>", replace_copy("<h2>Summary</h2>"), 1)
        rendered = rendered.replace("<h2>Results</h2>", replace_copy("<h2>Results</h2>", "<h2>Games</h2>"), 1)
        return _LEGACY_METRICS.sub(_legacy_record, rendered, count=1)
    return _report_copy(rendered, relative)
