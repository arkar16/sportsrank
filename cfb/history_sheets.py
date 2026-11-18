"""Present the two legacy CORS history fragments as static record sheets."""

from html import escape

from lxml import html

from .reference_markup import school_label


_SHEETS = {
    "CORS_NC_FBS_CFB.html": ("National champions", "nc_FBS_CFB_output.html", False),
    "CORS_WT_FBS_CFB.html": ("Last-place finishers", "wt_FBS_CFB_output.html", True),
}
# all_time.py prepends year to the columns from the legacy cors.py final
# rankings. Only worst_to_history leaves the pandas index in the output.
_TEXT_COLUMNS = ("School", "Conference", "Record")
_STAT_COLUMNS = (
    "Win fraction", "CORS rating", "Avg. scoring margin", "Schedule strength",
    "Expected wins", "Wins vs. expected",
)


def render_history_sheet(filename: str, original: bytes, routes: dict[str, str], years: set[int]) -> str:
    """Render a legacy fragment in source order, including repeated seasons."""
    if filename not in _SHEETS:
        raise ValueError(f"unknown history sheet: {filename}")
    label, book, has_index = _SHEETS[filename]
    title = f"CORS {label.lower()} by season"
    tables = html.fragment_fromstring(original.decode("utf-8"), create_parent=True)
    if not len(tables):
        raise ValueError(f"{filename}: missing legacy tables")

    year_column = 1 if has_index else 0
    expected_tags = ("th",) * year_column + ("td",) * (1 + len(_TEXT_COLUMNS) + len(_STAT_COLUMNS))
    rows = []
    for table_number, table in enumerate(tables, 1):
        records = table.xpath(".//tr")
        if table.tag != "table" or len(records) != 1 or tuple(cell.tag for cell in records[0]) != expected_tags:
            raise ValueError(f"{filename}: unexpected legacy table shape at table {table_number}")
        cells = [cell.text_content() for cell in records[0]]
        season, school, conference, record, *stats = cells[year_column:]
        year = int(season)
        season_label = escape(season)
        if year in years:
            season_label = f'<a href="/cfb/years/{year}/{year}_CFB.html">{season_label}</a>'
        row_index = f'<td class="num history-detail">{escape(cells[0])}</td>' if has_index else ""
        numbers = "".join(_stat_cell("td", column, value)
                          for column, value in zip(_STAT_COLUMNS, stats, strict=True))
        rows.append(
            f'<tr>{row_index}<th scope="row" class="num">{season_label}</th>'
            f'<td>{school_label(school, routes)}</td><td class="history-detail">{escape(conference)}</td>'
            f'<td>{escape(record)}</td>{numbers}</tr>'
        )

    index_heading = '<th scope="col" class="num history-detail">Row index</th>' if has_index else ""
    text_headings = ('<th scope="col" class="history-school">School</th>'
                     '<th scope="col" class="history-detail">Conference</th><th scope="col">Record</th>')
    stat_headings = "".join(_stat_cell("th", column, column) for column in _STAT_COLUMNS)
    body = "\n".join(rows)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | SportsRank</title>
<link rel="stylesheet" href="/cfb/assets/record-book.css">
</head>
<body class="record-book">
<nav aria-label="Record book"><a href="/cfb/years/history/{book}">{label} record book</a></nav>
<main class="history-sheet">
<h1 id="history-title">{title}</h1>
<p class="archive-date">Historical edition · <a href="{filename.removesuffix('.html')}.source.txt" download="{filename}">Download original HTML</a></p>
<label class="archive-columns"><input type="checkbox"> Show all columns</label>
<div class="table-scroll" role="region" aria-label="Archived season results" tabindex="0">
<table aria-labelledby="history-title">
<thead><tr>{index_heading}<th scope="col" class="num">Season</th>{text_headings}{stat_headings}</tr></thead>
<tbody>{body}</tbody>
</table>
</div>
</main>
</body>
</html>
"""


def _stat_cell(tag: str, column: str, value: str) -> str:
    css = "num" if column == "CORS rating" else "num history-detail"
    scope = ' scope="col"' if tag == "th" else ""
    return f'<{tag}{scope} class="{css}">{escape(value)}</{tag}>'
