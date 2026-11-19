"""Apply the shared record-book presentation while retaining archive source bytes."""
from functools import lru_cache
from html import escape, unescape
import re

from .archive_markup import strip_ui, ui as _ui
from .archive_presentation import decorate_archive, restore_chrome


_MARK = re.compile(rb'<img class="school-mark(?: large)?" [^<>]*>')
_SCHOOL_LINK = re.compile(rb'<a data-sr-team href="[^"<>]+">([^<>]*)</a>')
_CELL = re.compile(r'(<(?P<tag>td|th)\b[^>]*>)(?P<value>[^<>]+)(</(?P=tag)>)', re.I)
_SCHOOL_LABEL = re.compile(
    r'(?P<start><a(?: data-sr-team)? href="(?P<route>/cfb/teams/[^"/]+/index\.html)"[^>]*>|<span data-school>)'
    r'(?P<name>[^<>]+)(?P<end></(?:a|span)>)'
)
_HEADING = re.compile(r'(<span data-program="([^"]+)">)')
_BODY = re.compile(r"<body\b[^>]*>", re.I)
_HEAD_END = re.compile(r"</head\s*>", re.I)
_ASSET = "/cfb/assets/record-book.css"


def source_bytes(raw: bytes) -> bytes:
    """Recover an archive input exactly, including its original encoding and whitespace."""
    if b"<!--sr-ui-->" not in raw:
        return raw
    raw = strip_ui(raw)
    raw = _MARK.sub(b"", raw)
    return restore_chrome(_SCHOOL_LINK.sub(rb"\1", raw))


def _image(path: str, *, large: bool = False) -> str:
    name = "school-mark large" if large else "school-mark"
    size = 54 if large else 20
    return (f'<img class="{name}" src="{escape(path, quote=True)}" alt="" '
            f'width="{size}" height="{size}" loading="lazy">')


def school_label(name: str, routes: dict[str, str]) -> str:
    label = escape(name)
    if name in routes:
        return f'<a href="{escape(routes[name])}">{label}</a>'
    return f'<span data-school>{label}</span>'


def _school_text(raw: str) -> str:
    return unescape(raw).strip().replace("San Jos\ufffd State", "San José State")


MASTHEAD = (
    '<header class="masthead"><a class="nameplate" href="/">SportsRank</a>'
    '<nav aria-label="Site"><a href="/cfb/cfb.html">College football</a>'
    '<a href="/cfb/teams/index.html">Teams</a>'
    '<a href="/cfb/performance/index.html">Model performance</a></nav></header>'
)


class PagePresentation:
    def __init__(self, routes: dict[str, str], marks: dict[str, str]):
        self.routes = routes
        self.marks = marks
        self.by_route = {route: name for name, route in routes.items()}

    @lru_cache(maxsize=4096)
    def _school(self, raw_label: str) -> str:
        name = _school_text(raw_label)
        route = self.routes.get(name)
        if route:
            return f'<a data-sr-team href="{escape(route)}">{raw_label}</a>'
        if name in self.marks:
            return _image(self.marks[name]) + raw_label
        return raw_label

    def _cell(self, match: re.Match) -> str:
        return match[1] + self._school(match["value"]) + match[4]

    def _mark_label(self, match: re.Match, seen: set[str]) -> str:
        name = _school_text(match["name"])
        if name in seen or name not in self.marks:
            return match[0]
        if match["route"] and self.by_route.get(match["route"]) != name:
            return match[0]
        seen.add(name)
        return match["start"] + _image(self.marks[name]) + match["name"] + match["end"]

    def _heading(self, match: re.Match) -> str:
        path = self.marks.get(unescape(match[2]))
        return match[0] + (_image(path, large=True) if path else "")

    def render(self, raw: bytes, *, archive: bool, relative: str = "", targets: set[str] | None = None) -> bytes:
        original = source_bytes(raw)
        try:
            markup = original.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            markup = original.decode("latin-1")
            encoding = "latin-1"
        if not _BODY.search(markup):
            return original
        if archive and relative and targets is not None:
            markup = decorate_archive(markup, relative, targets)
        head = ""
        if 'name="viewport"' not in markup[:1500]:
            head += '<meta name="viewport" content="width=device-width, initial-scale=1">'
        if "charset=" not in markup[:1500].lower():
            head += f'<meta charset="{encoding}">'
        if _ASSET not in markup[:2500]:
            head += f'<link rel="stylesheet" href="{_ASSET}">'
        if head:
            markup = _HEAD_END.sub(lambda m: _ui(head) + m[0], markup, count=1)
        markup = _BODY.sub(lambda m: m[0] + _ui(MASTHEAD), markup, count=1)
        if archive:
            markup = _CELL.sub(self._cell, markup)
            # Some legacy tables are direct body children and have dozens of
            # columns. Keep horizontal scrolling local to the table.
            if "<table" in markup.lower() and 'class="scroll"' not in markup:
                markup = re.sub(r"<table\b", lambda m: _ui('<div class="table-scroll" tabindex="0">') + m[0], markup, flags=re.I)
                markup = re.sub(r"</table>", lambda m: m[0] + _ui('</div>'), markup, flags=re.I)
        # Only rows get small marks. Breadcrumbs and links to individual
        # seasons keep their original labels.
        if self.marks:
            game_log = set() if _HEADING.search(markup) else None
            def row_images(match):
                seen = game_log if game_log is not None else set()
                return _SCHOOL_LABEL.sub(lambda label: self._mark_label(label, seen), match[0])
            markup = re.sub(r"<tr\b[^>]*>.*?</tr>", row_images, markup, flags=re.I | re.S)
            markup = _HEADING.sub(self._heading, markup)
        return markup.encode(encoding, errors="xmlcharrefreplace")
