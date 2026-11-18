"""Retained CFBD catalog and shared static school marks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import fcntl
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zlib


_ASSET_DIR = Path("cfb/assets/program-marks")
_ASSET_PREFIX = f"/{_ASSET_DIR.as_posix()}/"
_SCHEMA = "program-marks/v1"
# SportsRank's previously retained team pages establish this ESPN logo origin.
_IMAGE_URL = re.compile(
    r"https?://a\.espncdn\.com(/i/teamlogos/ncaa/500(?:-dark)?/[a-zA-Z0-9_-]+\.png)"
)
_CFBD_IMAGE_URL = re.compile(
    r"https://cdn\.collegefootballdata\.com/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.png"
)
_ASSET_NAME = re.compile(r"[0-9a-f]{64}\.png")
_MAX_IMAGE_BYTES = 2 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ProgramMarksError(ValueError):
    """A safe site or retained-catalog error."""


class _UnusableImage(ValueError):
    """A response that cannot be used as a static program mark."""


@dataclass(frozen=True)
class _Program:
    cfbd_id: int
    school: str
    aliases: tuple[str, ...]
    image_url: str | None


@dataclass(frozen=True)
class MarkInstallResult:
    schools: int
    images: int
    pending: int


def _display_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if (0 < len(name) <= 128 and name.isprintable() and "://" not in name
            and not name.startswith("//") and not any(c in name for c in "<>\\")):
        return name
    return ""


def _image_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if _CFBD_IMAGE_URL.fullmatch(value):
        return value
    match = _IMAGE_URL.fullmatch(value)
    if match:
        # Older catalog responses used HTTP for this same CDN path.
        return "https://a.espncdn.com" + match[1]
    return None


def _parse_catalog(raw: bytes) -> list[_Program]:
    try:
        rows = json.loads(raw)
    except (UnicodeError, ValueError):
        raise ProgramMarksError("The retained program catalog is unreadable.") from None
    if not isinstance(rows, list) or not rows:
        raise ProgramMarksError("Expected a nonempty CFBD team catalog.")

    programs = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise ProgramMarksError("The program catalog contains an invalid CFBD ID.")
        cfbd_id = row["id"]
        school = _display_name(row.get("school"))
        if not school or cfbd_id in programs:
            raise ProgramMarksError("The program catalog contains an invalid name or duplicate CFBD ID.")
        aliases = row.get("alternateNames")
        aliases = {_display_name(name) for name in aliases} if isinstance(aliases, list) else set()
        logos = row.get("logos")
        urls = map(_image_url, logos) if isinstance(logos, list) else ()
        programs[cfbd_id] = _Program(
            cfbd_id, school, tuple(sorted(aliases - {"", school})), next(filter(None, urls), None),
        )
    return [programs[cfbd_id] for cfbd_id in sorted(programs)]


def _atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _load_catalog(cache: Path, acquire: Callable[[], bytes] | None) -> list[_Program]:
    response = cache / "response.json"
    if not cache.exists() and acquire is not None:
        # Claim before transport. An uncertain attempt needs owner review even
        # if the process stopped before it could persist the response.
        try:
            cache.mkdir(mode=0o700, parents=True, exist_ok=False)
        except FileExistsError:
            pass
        else:
            _atomic_write(response, acquire())
    if not response.is_file():
        if cache.exists():
            raise ProgramMarksError("The catalog cache is claimed and has no retained response. Inspect the request audit.")
        raise ProgramMarksError("No retained catalog. Initial acquisition requires --acquire-catalog.")
    return _parse_catalog(response.read_bytes())


def _valid_png(data: bytes) -> bool:
    if not 33 <= len(data) <= _MAX_IMAGE_BYTES:  # Signature and complete IHDR chunk.
        return False
    if not data.startswith(_PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR"):
        return False
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if not (1 <= width <= 1024 and 1 <= height <= 1024):
        return False
    offset = len(_PNG_SIGNATURE)
    has_pixels = False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + size + 12
        if end > len(data):
            return False
        chunk = data[offset + 4:end - 4]
        if zlib.crc32(chunk) != int.from_bytes(data[end - 4:end], "big"):
            return False
        if chunk == b"IEND":
            return has_pixels and end == len(data)
        has_pixels |= chunk[:4] == b"IDAT" and size > 0
        offset = end
    return False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download_png(url: str) -> bytes:
    if _image_url(url) != url:
        raise _UnusableImage("Unsupported program mark URL")
    # Separate from the authenticated SDK. No inherited proxy, cookies,
    # authorization headers, redirects, or retries.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(url, headers={"Accept": "image/png"})
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise OSError("Incomplete program mark response")
            if response.headers.get_content_type() != "image/png":
                raise _UnusableImage("Unexpected program mark content type")
            length = response.headers.get("Content-Length")
            expected_size = None
            if length is not None:
                if not length.isascii() or not length.isdecimal():
                    raise _UnusableImage("Invalid program mark length")
                length = length.lstrip("0") or "0"
                if len(length) > len(str(_MAX_IMAGE_BYTES)) or int(length) > _MAX_IMAGE_BYTES:
                    raise _UnusableImage("Oversized program mark")
                expected_size = int(length)
            data = response.read(_MAX_IMAGE_BYTES + 1)
            if expected_size is not None and len(data) != expected_size:
                raise OSError("Incomplete program mark body")
    except HTTPError as error:
        error.close()
        if error.code in {404, 410} or 300 <= error.code < 400:
            raise _UnusableImage("Program mark unavailable at the supplied URL") from None
        raise
    if not _valid_png(data):
        raise _UnusableImage("Invalid program mark PNG")
    return data


def _valid_cached_asset(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return _valid_png(stream.read(_MAX_IMAGE_BYTES + 1))


def _install_assets(
    programs: list[_Program], directory: Path, unavailable: Path, *, offline: bool,
) -> tuple[dict[str, str], int]:
    paths = {}
    pending = 0
    urls = sorted({program.image_url for program in programs if program.image_url is not None})
    for url in urls:
        name = hashlib.sha256(url.encode("ascii")).hexdigest() + ".png"
        path = directory / name
        if not _valid_cached_asset(path):
            rejected = unavailable / name
            if rejected.is_file():
                continue
            if offline:
                pending += 1
                continue
            try:
                data = _download_png(url)
            except _UnusableImage:
                rejected.touch()
                continue
            except (OSError, HTTPException):
                pending += 1
                continue
            _atomic_write(path, data)
        paths[url] = _ASSET_PREFIX + name
    return paths, pending


def install_program_marks(
    site: Path, cache: Path, *, acquire: Callable[[], bytes] | None = None, offline: bool = False,
) -> MarkInstallResult:
    """Install static marks from a retained catalog, optionally filling missing images."""
    site, cache = site.resolve(), cache.resolve()
    if cache.is_relative_to(site) or site.is_relative_to(cache):
        raise ProgramMarksError("The raw catalog cache and site must be separate.")
    if offline and acquire is not None:
        raise ProgramMarksError("Offline mode requires a retained catalog.")
    directory = site / _ASSET_DIR
    if directory.resolve() != directory:
        raise ProgramMarksError("The site mark directory must stay inside the site without symlinks.")
    programs = _load_catalog(cache, acquire)
    with (cache / "install.lock").open("ab") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        directory.mkdir(parents=True, exist_ok=True)
        unavailable = cache / "unavailable"
        if not offline:
            unavailable.mkdir(exist_ok=True)
        paths, pending = _install_assets(programs, directory, unavailable, offline=offline)
        rows = [
            {"cfbd_id": program.cfbd_id, "school": program.school, "aliases": program.aliases,
             "asset": paths.get(program.image_url)}
            for program in programs
        ]
        manifest = json.dumps({"schema": _SCHEMA, "schools": rows}, ensure_ascii=False, indent=2)
        _atomic_write(directory / "manifest.json", (manifest + "\n").encode("utf-8"))
    return MarkInstallResult(len(programs), len(paths), pending)


def program_logo_paths(site: Path) -> dict[str, str]:
    """Return existing marks by school and unambiguous alternate name."""
    directory = site.resolve() / _ASSET_DIR
    manifest = directory / "manifest.json"
    if directory.resolve() != directory or manifest.is_symlink():
        return {}
    try:
        catalog = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if not isinstance(catalog, dict) or catalog.get("schema") != _SCHEMA:
        return {}
    rows = catalog.get("schools")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return {}

    schools = Counter(_display_name(row.get("school")) for row in rows)
    canonical_names = set(schools) | {""}
    aliases: Counter[str] = Counter()
    result = {}
    for row in rows:
        school = _display_name(row.get("school"))
        names = row.get("aliases")
        names = {_display_name(name) for name in names} if isinstance(names, list) else set()
        names -= canonical_names
        aliases.update(names)
        asset = row.get("asset")
        if not school or not isinstance(asset, str) or not asset.startswith(_ASSET_PREFIX):
            continue
        name = asset.removeprefix(_ASSET_PREFIX)
        path = directory / name
        if not _ASSET_NAME.fullmatch(name) or path.is_symlink() or not path.is_file():
            continue
        result.update(dict.fromkeys(names, asset))
        if schools[school] == 1:
            result[school] = asset
    return {name: asset for name, asset in result.items() if aliases[name] <= 1}
