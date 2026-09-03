import os

import cfbd


API_KEY_ENV_VAR = "CFBD_API_KEY"


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
