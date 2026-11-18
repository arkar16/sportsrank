"""Build the static program record book from the published college football archive."""
import argparse
from collections import defaultdict
from html import escape
import os
from pathlib import Path
import tempfile

from .history_sheets import render_history_sheet
from .program_marks import program_logo_paths
from .program_pages import build_program_pages
from .reference_archive import Season, program_routes, read_archive
from .reference_markup import PagePresentation
from .season_pages import build_season_pages


def _write_changed(path: Path, content: bytes) -> bool:
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _home(seasons: list[Season], routes: dict[str, str], *, is_root: bool) -> str:
    latest = seasons[-1]
    key = next(reversed(latest.rankings))
    edition = "Final" if key == "FINAL" else "Preseason" if key == "PRESEASON" else f"Through Week {key[1:]}"
    year_link = f"/cfb/years/{latest.year}/{latest.year}_CFB.html"
    top = []
    for entry in sorted(latest.rankings[key], key=lambda row: row.rank)[:10]:
        top.append(
            f'<tr><td class="num">{entry.rank}</td><th scope="row"><a href="{routes[entry.school]}">'
            f'{escape(entry.school)}</a></th><td>{escape(entry.record)}</td>'
            f'<td class="num">{entry.rating:.2f}</td></tr>'
        )
    decades = defaultdict(list)
    for season in reversed(seasons):
        year = season.year
        decades[year // 10 * 10].append(f'<a href="/cfb/years/{year}/{year}_CFB.html">{year}</a>')
    archive = "".join(f'<div><h3>{decade}s</h3><p>{" ".join(links)}</p></div>' for decade, links in decades.items())
    title = "SportsRank" if is_root else "College Football | SportsRank"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="/cfb/assets/record-book.css"></head>
<body><main><h1>College football</h1>
<p class="edition">{latest.year} FBS &middot; {edition}</p>
<div class="home-columns"><section><h2>CORS rankings</h2><div class="table-scroll">
<table><thead><tr><th scope="col" class="num">Rank</th><th scope="col">Team</th>
<th scope="col">Record</th><th scope="col" class="num">CORS rating</th></tr></thead>
<tbody>{''.join(top)}</tbody></table></div><p><a href="{year_link}">Full {latest.year} rankings and results</a></p>
</section><section><h2>Record book</h2><p><a href="/cfb/teams/index.html">All {len(routes)} ranked programs</a></p>
<p><a href="/cfb/performance/index.html">CORS model performance</a></p>
<p><a href="/cfb/years/history/nc_FBS_CFB_output.html">National champions</a></p>
<p><a href="/cfb/years/history/wt_FBS_CFB_output.html">Last-place finishers</a></p></section></div>
<section><h2>Season archive</h2><div class="year-archive">{archive}</div></section>
</main></body></html>
"""


def _not_found(year: int) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>404 · Page not found | SportsRank</title><link rel="stylesheet" href="/cfb/assets/record-book.css"></head>
<body><main class="missing-page"><h1>Page not found</h1>
<p>Error 404. There’s no SportsRank page at this address.</p>
<ul class="recovery-links">
<li><a href="/cfb/years/{year}/{year}_CFB.html">{year} college football standings</a></li>
<li><a href="/cfb/teams/index.html">Find a team</a></li>
<li><a href="/cfb/cfb.html">Browse the season archive</a></li>
</ul></main></body></html>
"""


def _history_sheet(path: Path, routes: dict[str, str], years: set[int], presentation: PagePresentation) -> int:
    # After the one-time conversion, the public original also supplies the next
    # offline build. A freshly exported table fragment takes precedence.
    source = path.with_suffix(".source.txt")
    current = path.read_bytes()
    if current.lstrip().startswith(b"<table"):
        original = current
    elif b'<h1 id="history-title">' in current:
        original = source.read_bytes()
    else:
        raise ValueError(f"unrecognized history sheet at {path}")
    markup = render_history_sheet(path.name, original, routes, years)
    changed = _write_changed(source, original)
    return changed + _write_changed(path, presentation.render(markup.encode(), archive=False))


def build(site: Path) -> tuple[int, int, int]:
    site = site.resolve()
    seasons = read_archive(site)
    if not seasons:
        raise ValueError(f"no college football rankings at {site}")
    routes = program_routes(seasons)
    presentation = PagePresentation(routes, program_logo_paths(site))
    changed = 0
    for asset in (Path(__file__).parent / "assets").iterdir():
        changed += _write_changed(site / "cfb/assets" / asset.name, asset.read_bytes())
    for relative, markup in build_program_pages(seasons, routes):
        changed += _write_changed(site / relative, presentation.render(markup.encode(), archive=False))
    season_pages = set()
    for season in seasons:
        for relative, markup in build_season_pages(site, season, routes):
            path = site / relative
            changed += _write_changed(path, presentation.render(markup.encode(), archive=False))
            season_pages.add(path)
    for relative in ("index.html", "cfb/cfb.html"):
        markup = _home(seasons, routes, is_root=relative == "index.html")
        changed += _write_changed(site / relative, presentation.render(markup.encode(), archive=False))
    changed += _write_changed(site / "404.html", presentation.render(_not_found(seasons[-1].year).encode(), archive=False))
    years = {season.year for season in seasons}
    targets = {"/" + path.relative_to(site).as_posix() for path in site.rglob("*.html")}
    # Decorate source pages last. School text, scoring rows, existing links and
    # the exact input bytes used by forecast evaluation can all be recovered.
    for directory in ("years", "performance", "history"):
        for path in (site / "cfb" / directory).rglob("*.html"):
            if path in season_pages:
                continue
            relative = path.relative_to(site).as_posix()
            if relative in {"cfb/years/history/CORS_NC_FBS_CFB.html", "cfb/years/history/CORS_WT_FBS_CFB.html"}:
                changed += _history_sheet(path, routes, years, presentation)
                continue
            raw = path.read_bytes()
            markup = presentation.render(raw, archive=True, relative=relative, targets=targets)
            if raw != markup:
                changed += _write_changed(path, markup)
    return len(seasons), len(routes), changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=Path("website"), help="generated static site root")
    args = parser.parse_args(argv)
    seasons, programs, changed = build(args.site)
    print(f"{seasons} seasons, {programs} programs, {changed} changed files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
