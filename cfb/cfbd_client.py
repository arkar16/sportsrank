import os
from functools import lru_cache
from pathlib import Path

import cfbd


API_KEY_ENV_VAR = "CFBD_API"
DATA_DIR_ENV_VAR = "SPORTSRANK_DATA_DIR"


def configured_api_key() -> str:
    """Return the normalized provider credential from the BB environment."""

    return os.environ.get(API_KEY_ENV_VAR, "").strip()


def redact_api_key(message: str) -> str:
    """Replace the normalized provider credential in an error message."""

    api_key = configured_api_key()
    if api_key:
        return message.replace(api_key, "[redacted]")
    return message


def create_configuration():
    api_key = configured_api_key()
    if not api_key:
        raise RuntimeError(
            f"Set {API_KEY_ENV_VAR} in your environment before fetching CFBD data."
        )
    return cfbd.Configuration(access_token=api_key)


def create_api_client():
    return cfbd.ApiClient(create_configuration())


def division_classification(division):
    return cfbd.DivisionClassification(division.lower())


def classification_value(classification):
    return getattr(classification, "value", classification)


def data_directory():
    configured = os.environ.get(DATA_DIR_ENV_VAR, "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / ".sportsrank"


@lru_cache(maxsize=2)
def get_snapshot_service(category="scheduled"):
    """Construct the production seam on the shared cache/audit root.

    ``category`` changes only the Request Meter budget/purpose.  Scheduled and
    historical services intentionally point at the same ``SPORTSRANK_DATA_DIR``
    so one recovery ledger accounts for every request.
    """
    if __package__:
        from .request_meter import RequestMeter
        from .season_snapshot import SeasonSnapshotService
        from .season_source import ProductionSeasonSource
        from .snapshot_cache import SnapshotCache
    else:
        from request_meter import RequestMeter
        from season_snapshot import SeasonSnapshotService
        from season_source import ProductionSeasonSource
        from snapshot_cache import SnapshotCache

    root = data_directory()
    meter = RequestMeter(root / "cfbd_requests.sqlite3")
    source = ProductionSeasonSource(meter, category=category)
    return SeasonSnapshotService(source, SnapshotCache(root / "snapshots"))
