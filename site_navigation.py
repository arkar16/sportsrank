"""Shared static sport navigation for SportsRank pages."""


def masthead(section_links: str = "") -> str:
    return (
        '<header class="masthead"><a class="nameplate" href="/">SportsRank</a>'
        '<nav aria-label="Site"><a href="/cfb/cfb.html">College football</a>'
        '<a href="/cbb/index.html">College basketball</a>'
        f'{section_links}</nav></header>'
    )
