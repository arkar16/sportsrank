"""Fail-closed smoke check for the exact static-site artifact after deploy."""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen


REQUIRED_PATHS = (
    "index.html",
    "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html",
    "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html",
    "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html",
    "cfb/years/2026/rankings/2026_PRESEASON_FBS_cors.html",
    "cfb/years/2026/data/slate/weekly_slate/2026_W0_FBS_slate.html",
)

DEFAULT_ATTEMPTS = 3
DEFAULT_PAGE_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRY_DELAY_SECONDS = 2.0
DEFAULT_MAX_DURATION_SECONDS = 180.0
MAX_ATTEMPTS = 5
MAX_RETRY_DELAY_SECONDS = 30.0
MAX_DURATION_SECONDS = 180.0


class SmokeError(RuntimeError):
    """A validation or deployed-site failure that must fail the publish job."""


def _validated_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SmokeError("base URL must be an absolute HTTP(S) URL")
    return base_url.rstrip("/") + "/"


def _artifact_bytes(artifact_root: Path, relative_path: str) -> bytes:
    path = artifact_root / relative_path
    try:
        path.relative_to(artifact_root)
    except ValueError as exc:
        raise SmokeError(f"required artifact path escapes artifact root: {relative_path}") from exc
    if not path.is_file():
        raise SmokeError(f"required artifact is missing: {relative_path}")
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise SmokeError(f"required artifact cannot be read: {relative_path}") from exc
    if not body:
        raise SmokeError(f"required artifact is empty: {relative_path}")
    return body


def _fetch_page(base_url: str, relative_path: str, timeout: float) -> bytes:
    # Exercise the public root for the homepage while retaining the artifact's
    # concrete index.html path as the comparison source.
    url_path = "" if relative_path == "index.html" else quote(relative_path, safe="/")
    url = urljoin(_validated_base_url(base_url), url_path)
    request = Request(
        url,
        headers={
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            if status != 200:
                raise SmokeError(f"{relative_path}: deployed response returned HTTP {status}")
            return response.read()
    except HTTPError as exc:
        raise SmokeError(f"{relative_path}: deployed response returned HTTP {exc.code}") from exc
    except (OSError, TimeoutError, URLError) as exc:
        raise SmokeError(f"{relative_path}: deployed request failed: {exc}") from exc


def run_smoke(
    base_url: str,
    artifact_root: Path,
    *,
    timeout: float = DEFAULT_PAGE_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
    max_duration: float = DEFAULT_MAX_DURATION_SECONDS,
    fetch_page: Callable[[str, str, float], bytes] | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> None:
    """Verify each required deployed URL byte-for-byte against the artifact.

    A failed page check restarts the complete six-page check.  The bounded
    retry and clock/sleep seams keep production fail-closed while allowing
    offline tests to exercise transient failures without waiting or networking.
    """

    if timeout <= 0:
        raise SmokeError("timeout must be greater than zero")
    if attempts < 1 or attempts > MAX_ATTEMPTS:
        raise SmokeError(f"attempts must be between 1 and {MAX_ATTEMPTS}")
    if retry_delay < 0 or retry_delay > MAX_RETRY_DELAY_SECONDS:
        raise SmokeError(f"retry delay must be between 0 and {MAX_RETRY_DELAY_SECONDS} seconds")
    if max_duration <= 0 or max_duration > MAX_DURATION_SECONDS:
        raise SmokeError(f"max duration must be between 0 and {MAX_DURATION_SECONDS} seconds")
    _validated_base_url(base_url)
    if not artifact_root.is_dir():
        raise SmokeError(f"artifact root is missing: {artifact_root}")

    expected_pages = tuple(
        (relative_path, _artifact_bytes(artifact_root, relative_path))
        for relative_path in REQUIRED_PATHS
    )
    fetcher = _fetch_page if fetch_page is None else fetch_page
    sleeper = time.sleep if sleep is None else sleep
    timer = time.monotonic if clock is None else clock
    started = timer()
    last_error: SmokeError | None = None

    for attempt in range(1, attempts + 1):
        try:
            if timer() - started >= max_duration:
                raise SmokeError("retry window exceeded before page verification")
            for relative_path, expected in expected_pages:
                elapsed = timer() - started
                if elapsed >= max_duration:
                    raise SmokeError("retry window exceeded during page verification")
                page_timeout = min(timeout, max_duration - elapsed)
                try:
                    actual = fetcher(base_url, relative_path, page_timeout)
                except SmokeError:
                    raise
                except Exception as exc:  # noqa: BLE001 - fail closed at the network seam
                    raise SmokeError(f"{relative_path}: deployed request failed: {exc}") from exc
                if actual != expected:
                    expected_digest = hashlib.sha256(expected).hexdigest()
                    actual_digest = hashlib.sha256(actual).hexdigest()
                    raise SmokeError(
                        f"{relative_path}: deployed response bytes does not match the validated artifact "
                        f"(expected sha256 {expected_digest}, got {actual_digest})"
                    )
            if timer() - started >= max_duration:
                raise SmokeError("retry window exceeded after page verification")
        except SmokeError as exc:
            last_error = exc
            if attempt == attempts:
                break
            remaining = max_duration - (timer() - started)
            if remaining <= 0:
                break
            sleeper(min(retry_delay, remaining))
            continue

        for relative_path, expected in expected_pages:
            digest = hashlib.sha256(expected).hexdigest()
            print(f"postdeploy smoke passed: {relative_path} sha256={digest}")
        return

    detail = str(last_error) if last_error is not None else "verification did not run"
    raise SmokeError(f"verification exhausted after {attempts} attempts: {detail}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="deployed site origin")
    parser.add_argument(
        "--artifact-root",
        required=True,
        type=Path,
        help="directory containing the validated website artifact",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PAGE_TIMEOUT_SECONDS,
        help=f"per-page HTTP timeout in seconds (default: {DEFAULT_PAGE_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=DEFAULT_ATTEMPTS,
        help=f"complete six-page checks to attempt (default: {DEFAULT_ATTEMPTS})",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_RETRY_DELAY_SECONDS,
        help=f"seconds between complete checks (default: {DEFAULT_RETRY_DELAY_SECONDS:g})",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAX_DURATION_SECONDS,
        help=f"maximum retry window in seconds (default: {DEFAULT_MAX_DURATION_SECONDS:g})",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    fetch_page: Callable[[str, str, float], bytes] | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        run_smoke(
            args.base_url,
            args.artifact_root,
            timeout=args.timeout,
            attempts=args.attempts,
            retry_delay=args.retry_delay,
            max_duration=args.max_duration,
            fetch_page=fetch_page,
            sleep=sleep,
            clock=clock,
        )
    except SmokeError as exc:
        print(f"postdeploy smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
