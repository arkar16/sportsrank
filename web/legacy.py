"""Permanent compatibility mapping for every historical SportsRank .html URL."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit, urlunsplit

_EXACT_REDIRECTS: dict[str, str] = {
    "/index.html": "/",
    "/cfb/": "/cfb",
    "/cfb/cfb.html": "/cfb",
    "/cfb/index.html": "/cfb",
    "/cfb/teams/": "/cfb/teams",
    "/cfb/teams/index.html": "/cfb/teams",
    "/cfb/conferences/": "/cfb/conferences",
    "/cfb/conferences/index.html": "/cfb/conferences",
    "/cfb/performance/": "/cfb/performance",
    "/cfb/performance/index.html": "/cfb/performance",
    "/cfb/history/nc_FBS_CFB_output.html": "/cfb/history/champions",
    "/cfb/years/history/nc_FBS_CFB_output.html": "/cfb/history/champions",
    "/cfb/years/history/CORS_NC_FBS_CFB.html": "/cfb/history/champions",
    "/cfb/history/wt_FBS_CFB_output.html": "/cfb/history/last-place",
    "/cfb/years/history/wt_FBS_CFB_output.html": "/cfb/history/last-place",
    "/cfb/years/history/CORS_WT_FBS_CFB.html": "/cfb/history/last-place",
    "/cfb/history/conference-honors.html": "/cfb/history/conference-honors",
    "/cfb/excitement/bev.html": "/cfb/excitement/bev",
    "/cfb/excitement/aev.html": "/cfb/excitement/aev",
    "/excitement/bev.html": "/excitement/bev",
    "/excitement/aev.html": "/excitement/aev",
    "/excitement/grades.html": "/excitement/grades",
    "/cbb/": "/cbb",
    "/cbb/index.html": "/cbb",
    "/cbb/current.html": "/cbb/current",
    "/cbb/teams/": "/cbb/teams",
    "/cbb/teams/index.html": "/cbb/teams",
    "/cbb/seasons/": "/cbb/seasons",
    "/cbb/seasons/index.html": "/cbb/seasons",
    "/cbb/conferences/": "/cbb/conferences",
    "/cbb/conferences/index.html": "/cbb/conferences",
    "/cbb/performance/": "/cbb/performance",
    "/cbb/performance/index.html": "/cbb/performance",
    "/cbb/tournaments/": "/cbb/tournaments",
    "/cbb/tournaments/index.html": "/cbb/tournaments",
    "/cbb/excitement/bev.html": "/cbb/excitement/bev",
    "/cbb/excitement/aev.html": "/cbb/excitement/aev",
    "/cbb/history/champions.html": "/cbb/history/champions",
    "/cbb/history/last-place.html": "/cbb/history/last-place",
    "/cbb/history/conference-honors.html": "/cbb/history/conference-honors",
    "/cbb/history/ncaa-champions.html": "/cbb/history/ncaa-champions",
}

_PATTERNS: tuple[tuple[re.Pattern[str], callable], ...] = (
    # CFB & CBB team pages
    (
        re.compile(r"^/(cfb|cbb)/teams/([a-z0-9-]+)/index\.html$"),
        lambda m: f"/{m[1]}/teams/{m[2]}",
    ),
    (
        re.compile(r"^/(cfb|cbb)/teams/([a-z0-9-]+)/([0-9]{4})\.html$"),
        lambda m: f"/{m[1]}/teams/{m[2]}/{m[3]}",
    ),
    # CFB & CBB conference pages (season comparison, conference history, conference season, OOC matchups)
    (
        re.compile(r"^/(cfb|cbb)/conferences/([0-9]{4})\.html$"),
        lambda m: f"/{m[1]}/conferences/{m[2]}",
    ),
    (
        re.compile(r"^/(cfb|cbb)/conferences/([a-z0-9-]+)/index\.html$"),
        lambda m: f"/{m[1]}/conferences/{m[2]}",
    ),
    (
        re.compile(r"^/(cfb|cbb)/conferences/([a-z0-9-]+)/([0-9]{4})\.html$"),
        lambda m: f"/{m[1]}/conferences/{m[2]}/{m[3]}",
    ),
    (
        re.compile(r"^/(cfb|cbb)/conferences/matchups/([0-9]{4})/([a-z0-9-]+)\.html$"),
        lambda m: f"/{m[1]}/conferences/matchups/{m[2]}/{m[3]}",
    ),
    # CFB & CBB pick share cards
    (
        re.compile(r"^/cfb/picks/([0-9]{4})/([a-z0-9-]+)/([a-z0-9-]+)\.html$"),
        lambda m: f"/cfb/picks/{m[1]}/{m[2]}/{m[3]}",
    ),
    (
        re.compile(r"^/cbb/picks/([0-9]{4}-[0-9]{2}-[0-9]{2})/([a-z0-9-]+)\.html$"),
        lambda m: f"/cbb/picks/{m[1]}/{m[2]}",
    ),
    # CFB season landing, checkpoints & season excitement
    (
        re.compile(r"^/cfb/years/([0-9]{4})/\1_CFB\.html$"),
        lambda m: f"/cfb/{m[1]}",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/checkpoints/(PRESEASON|FINAL|W[0-9]+)\.html$", re.I),
        lambda m: f"/cfb/{m[1]}/{m[2].lower()}",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/rankings/\1_(PRESEASON|FINAL|W[0-9]+)_FBS_cors\.html$", re.I),
        lambda m: f"/cfb/{m[1]}/{m[2].lower()}",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/excitement(?:/|/index\.html)$"),
        lambda m: f"/cfb/{m[1]}/excitement",
    ),
    # CFB weekly & full-season results / schedules / spreads / records
    (
        re.compile(r"^/cfb/years/([0-9]{4})/data/results/weekly_results/\1_W([0-9]+)_FBS_results\.html$"),
        lambda m: f"/cfb/{m[1]}/w{int(m[2])}/results",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/data/results/\1_FBS_results\.html$"),
        lambda m: f"/cfb/{m[1]}/results",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/data/slate/weekly_slate/\1_W([0-9]+)_FBS_slate\.html$"),
        lambda m: f"/cfb/{m[1]}/w{int(m[2])}/schedule",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/data/slate/\1_FBS_slate\.html$"),
        lambda m: f"/cfb/{m[1]}/schedule",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/data/records/\1_W([0-9]+)_FBS_records\.html$"),
        lambda m: f"/cfb/{m[1]}/w{int(m[2])}",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/data/\1_FBS_teams\.html$"),
        lambda m: f"/cfb/{m[1]}",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/spread/\1_W([0-9]+)_FBS_spread(?:_results)?\.html$"),
        lambda m: f"/cfb/{m[1]}/w{int(m[2])}/spreads",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/spread/\1_FBS_forecast_results\.html$"),
        lambda m: f"/cfb/performance/{m[1]}",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/reconstructed/\1_W([0-9]+)_FBS_spread\.html$"),
        lambda m: f"/cfb/{m[1]}/w{int(m[2])}/spreads",
    ),
    (
        re.compile(r"^/cfb/years/([0-9]{4})/history/(?:reconstruction|\1_FBS_progression)\.html$"),
        lambda m: f"/cfb/{m[1]}",
    ),
    # CFB & CBB performance reports
    (
        re.compile(r"^/(cfb|cbb)/performance/([0-9]{4})(?:/|/index\.html)$"),
        lambda m: f"/{m[1]}/performance/{m[2]}",
    ),
    # CBB seasons, weeks, progression, excitement, tournaments, editions
    (
        re.compile(r"^/cbb/seasons/([0-9]{4})(?:/|/index\.html)$"),
        lambda m: f"/cbb/seasons/{m[1]}",
    ),
    (
        re.compile(r"^/cbb/seasons/([0-9]{4})/weeks/([a-z0-9-]+)\.html$", re.I),
        lambda m: f"/cbb/seasons/{m[1]}/weeks/{m[2].lower()}",
    ),
    (
        re.compile(r"^/cbb/seasons/([0-9]{4})/progression\.html$"),
        lambda m: f"/cbb/seasons/{m[1]}/progression",
    ),
    (
        re.compile(r"^/cbb/seasons/([0-9]{4})/excitement(?:/|/index\.html)$"),
        lambda m: f"/cbb/seasons/{m[1]}/excitement",
    ),
    (
        re.compile(r"^/cbb/tournaments/([0-9]{4})\.html$"),
        lambda m: f"/cbb/tournaments/{m[1]}",
    ),
    (
        re.compile(r"^/cbb/editions/([A-Za-z0-9._-]+)\.html$"),
        lambda m: f"/cbb/editions/{m[1]}",
    ),
)

_ATTR_URL = re.compile(r'\b(href|content)=("|\')([^"\']+)\2')
_SPORTSRANK_HOSTS = {"www.sportsrank.top", "sportsrank.top"}


def resolve_legacy_redirect(path: str) -> str | None:
    """Return the canonical destination for a legacy static path, or None."""
    exact = _EXACT_REDIRECTS.get(path)
    if exact is not None:
        return exact
    for pattern, target in _PATTERNS:
        match = pattern.match(path)
        if match:
            return target(match)
    return None


def rewrite_html_links(html_text: str, source_legacy_path: str) -> str:
    """Rewrite internal .html links (relative, root-relative, or sportsrank.top) to clean URLs."""
    base_dir = posixpath.dirname(source_legacy_path)

    def _replace(match: re.Match[str]) -> str:
        attr, quote_char, raw_url = match[1], match[2], match[3]
        if not raw_url or raw_url.startswith(("#", "mailto:", "javascript:", "data:")):
            return match[0]
        parts = urlsplit(raw_url)
        if parts.scheme in ("http", "https"):
            if parts.netloc.lower() not in _SPORTSRANK_HOSTS:
                return match[0]
            resolved = resolve_legacy_redirect(parts.path)
            if resolved is None:
                return match[0]
            new_url = urlunsplit((parts.scheme, parts.netloc, resolved, parts.query, parts.fragment))
            return f"{attr}={quote_char}{new_url}{quote_char}"
        if parts.scheme or parts.netloc:
            return match[0]
        if not parts.path:
            return match[0]
        if parts.path.startswith("/"):
            abs_path = posixpath.normpath(parts.path)
            if parts.path.endswith("/") and abs_path != "/":
                abs_path += "/"
        else:
            abs_path = posixpath.normpath(posixpath.join(base_dir, parts.path))
        resolved = resolve_legacy_redirect(abs_path)
        if resolved is None:
            return match[0]
        new_url = urlunsplit(("", "", resolved, parts.query, parts.fragment))
        return f"{attr}={quote_char}{new_url}{quote_char}"

    return _ATTR_URL.sub(_replace, html_text)
