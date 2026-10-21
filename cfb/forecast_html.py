"""Static, horizontally scrollable presentation for new forecast tables."""

from __future__ import annotations

import html
from typing import Sequence

from bs4 import BeautifulSoup


_STYLE = """
.forecast-report{max-width:100%}
.forecast-table-scroll{overflow-x:auto;max-width:100%;margin-bottom:1rem}
.forecast-report table{border-collapse:separate;border-spacing:0;white-space:nowrap}
.forecast-report th,.forecast-report td{box-sizing:border-box;padding:.3rem .45rem}
.forecast-games{--week-width:3.5rem;--team-width:10rem;table-layout:fixed}
.forecast-games th,.forecast-games td{white-space:normal;overflow-wrap:anywhere}
.forecast-games th:nth-child(-n+3),.forecast-games td:nth-child(-n+3){position:sticky;background:#fff;z-index:1}
.forecast-games th:nth-child(1),.forecast-games td:nth-child(1){left:0}
.forecast-games th:nth-child(2),.forecast-games td:nth-child(2){left:var(--week-width)}
.forecast-games th:nth-child(3),.forecast-games td:nth-child(3){left:calc(var(--week-width) + var(--team-width))}
@media(max-width:600px){.forecast-games{--week-width:2.5rem;--team-width:5.5rem}}
""".strip()


def render_forecast_page(
    title: str,
    timestamp: str,
    body: str,
    links: Sequence[tuple[str, str]] = (),
) -> str:
    """Wrap each generated table separately; keep headings stationary.

    The caller supplies escaped, generated table cells. Identity column widths
    and sticky offsets share the same variables, so the frozen cells cannot
    drift over the data columns. Only the game table freezes identity columns.
    """

    fragment = BeautifulSoup(body, "html.parser")
    for index, table in enumerate(fragment.find_all("table")):
        wrapper = fragment.new_tag("div")
        wrapper["class"] = "forecast-table-scroll"
        wrapper["tabindex"] = "0"
        wrapper["role"] = "region"
        wrapper["aria-label"] = ("Games", "Summary", "Omissions")[min(index, 2)]
        table.wrap(wrapper)
        if index != 0:
            continue
        fields = [cell.get_text(strip=True) for cell in table.select("thead th")]
        if fields[:3] not in (["Week", "Home", "Away"], ["week", "home_team", "away_team"]):
            continue
        table["class"] = [*table.get("class", []), "forecast-games"]
        # Explicit table and colgroup widths make sticky left positions equal
        # the actual preceding cell widths, including padding and borders.
        data_width = max(0, len(fields) - 3) * 9
        table["style"] = (
            "width:calc(var(--week-width) + var(--team-width) + "
            f"var(--team-width) + {data_width}rem)"
        )
        columns = fragment.new_tag("colgroup")
        for field_index in range(len(fields)):
            column = fragment.new_tag("col")
            width = "var(--week-width)" if field_index == 0 else "var(--team-width)" if field_index < 3 else "9rem"
            column["style"] = f"width:{width}"
            columns.append(column)
        table.insert(0, columns)
    navigation = "".join(
        f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a> | '
        for label, href in links
    )
    return (
        "<!doctype html>\n<html>\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<style>{_STYLE}</style>\n"
        f"<title>{html.escape(title)}</title>\n"
        "</head>\n<body>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        f"<p>{navigation}</p>\n"
        f"<p>Last updated: {html.escape(timestamp)}</p>\n"
        f'<div class="forecast-report">{fragment}</div>\n'
        "</body>\n</html>\n"
    )
