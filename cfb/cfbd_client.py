import os
from functools import lru_cache
from pathlib import Path

import cfbd


API_KEY_ENV_VAR = "CFBD_API_KEY"
DATA_DIR_ENV_VAR = "SPORTSRANK_DATA_DIR"


def create_configuration():
    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
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
    """Construct the persistent production seam without reading credentials."""
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
