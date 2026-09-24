"""Content checks shared by public website and current-tree transports.

These checks recognize source records and archive formats; they do not claim
that arbitrary encodings or deliberately concealed data can be classified.
Review remains responsible for approving intended public content. The only
source-shaped JSON exception is the exact, synthetic portable test fixtures.
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

_ARCHIVES = ('.tar', '.tar.gz', '.tgz', '.zip', '.gz', '.bz2', '.xz', '.7z',
             '.bundle', '.sqlite', '.sqlite3', '.db', '.pickle', '.pkl')
_PRIVATE_PARTS = {'.git', '.sportsrank', 'private', 'private-inputs', 'source-inputs'}
_EXAMPLE_ENV_SHA256 = '3b1bb1cad55ecf66871db3834f70f218b26c4c29da66dea7eb6734ed2c9514d6'
_SYNTHETIC_FIXTURES = {
    'tests/fixtures/cfbd/2024/fbs/games.json': '0d863d32a62e432919570a7018f02b1b4eab4ba471200d2e200f2b56099f1224',
    'tests/fixtures/cfbd/2024/fbs/teams.json': '19a81d384daf157765dae00f35e7a5241c3f93545d41c6434221107432eeab2f',
    'tests/fixtures/cfbd/2025/fbs/games.json': 'a727337ccdf41663923d643ba765e8c320af8f00a5ebd2a85aaa11fbc1cef3b5',
    'tests/fixtures/cfbd/2025/fbs/teams.json': '92e5bc8d28c8f54785e0b8f3044e0ff914cf9cd636ade4b585dace35184e5c7a',
}


def has_source_payload(value: Any) -> bool:
    """Recognize normalized snapshots and provider game/team records recursively."""
    if isinstance(value, Mapping):
        keys = set(value)
        # These exact historical summary rows are generated public rankings,
        # rather than provider Team records. Keep their established metadata.
        derived_team = (keys == {'school', 'conference', 'cors', 'record', 'win_pct', 'kind', 'year'}
                        and value.get('kind') in {'National champion', 'Worst team'})
        if ({'teams', 'games'} <= keys or {'home_team', 'away_team'} <= keys
                or {'homeTeam', 'awayTeam'} <= keys
                or ({'school', 'conference'} <= keys and not derived_team)):
            return True
        return any(has_source_payload(item) for item in value.values())
    if isinstance(value, list):
        return any(has_source_payload(item) for item in value)
    return False


def assert_public_bytes(relative: str, raw: bytes) -> None:
    """Reject recognized private data regardless of its filename or JSON nesting."""
    path = PurePosixPath(relative)
    # The repository's existing credential template contains placeholders.
    # Its exact bytes are allowed; a populated or modified dotenv is not.
    if relative == '.env.example' and hashlib.sha256(raw).hexdigest() == _EXAMPLE_ENV_SHA256:
        return
    if (path.is_absolute() or '..' in path.parts
            or any(part.lower() in _PRIVATE_PARTS for part in path.parts)
            or path.name == '.env' or path.name.startswith('.env.')):
        raise ValueError(f'public transport contains a private path: {relative}')
    header = raw[:512]
    if (relative.lower().endswith(_ARCHIVES)
            or header.startswith((b'PK\x03\x04', b'PK\x05\x06', b'\x1f\x8b', b'BZh',
                                  b'\xfd7zXZ\x00', b'7z\xbc\xaf\x27\x1c',
                                  b'# v2 git bundle', b'# v3 git bundle', b'SQLite format 3\x00'))
            or header[257:262] == b'ustar'):
        raise ValueError(f'public transport contains an unapproved archive: {relative}')
    if relative in _SYNTHETIC_FIXTURES:
        if hashlib.sha256(raw).hexdigest() != _SYNTHETIC_FIXTURES[relative]:
            raise ValueError(f'public synthetic fixture differs from reviewed bytes: {relative}')
        return
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return
    stripped = text.lstrip()
    if stripped.startswith(('{', '[')):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            if has_source_payload(value):
                raise ValueError(f'public transport contains a source snapshot payload: {relative}')
            return
    # Static browser assets can hide complete records inside scripts, string
    # assignments, HTML attributes, or comments. Decode JSON fragments there,
    # rather than treating the HTML/CSS/JS filename as evidence of safety.
    if (path.suffix.lower() in {'.html', '.htm', '.js', '.mjs', '.css', '.json'}
            or stripped.startswith(('{', '['))
            or ('<script' in text.lower() and path.suffix.lower() not in {'.py', '.md', '.yml', '.yaml', '.toml'})):
        text = html.unescape(text)
        decoder = json.JSONDecoder()
        for match in re.finditer(r'[\{\[]', text):
            try:
                value, _end = decoder.raw_decode(text, match.start())
            except json.JSONDecodeError:
                continue
            if has_source_payload(value):
                raise ValueError(f'public transport contains an embedded source snapshot payload: {relative}')
