"""Shared static sport navigation for SportsRank pages."""


def masthead(section_links: str = "", *, compact: bool = False) -> str:
    football, basketball = ("CFB", "MBB") if compact else ("College football", "College basketball")
    return (
        '<header class="masthead"><a class="nameplate" href="/">SportsRank</a>'
        f'<nav aria-label="Site"><a href="/cfb/cfb.html">{football}</a>'
        f'<a href="/cbb/index.html">{basketball}</a>'
        f'{section_links}</nav></header>'
    )
