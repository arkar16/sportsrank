"""Renew credentials from the protected workflow's exact Google-auth file."""
from __future__ import annotations

from functools import partial
import os
import threading
from typing import Callable, Mapping

import google.auth
from google.auth.transport.requests import Request

from .firebase import FirebasePublicationError

_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
_HOSTING_ORIGIN = "https://firebasehosting.googleapis.com/"


def access_token_provider(environment: Mapping[str, str] | None = None) -> Callable[[], str]:
    """Share one refreshable credential across both Firebase adapters.

    The explicit file comes only from the gated auth action; do not search for
    other accounts or fall back if that file is invalid. Loading is lazy so
    sealing an initial baseline can still run without Firebase credentials.
    Explicit-token local callers retain their existing interface.
    """
    values = os.environ if environment is None else environment
    if "FIREBASE_CREDENTIALS_FILE" not in values:
        return lambda: values.get("FIREBASE_ACCESS_TOKEN", "")
    path = values["FIREBASE_CREDENTIALS_FILE"]
    lock = threading.Lock()
    credentials = None
    request = None

    def token() -> str:
        nonlocal credentials, request
        # Upload workers also share these credentials and refresh transport.
        with lock:
            if credentials is None:
                try:
                    if not isinstance(path, str) or not path.strip():
                        raise ValueError("missing gated credentials")
                    credentials, _ = google.auth.load_credentials_from_file(path, scopes=_SCOPES)
                    request = partial(Request(), timeout=30)
                except Exception:
                    raise FirebasePublicationError("Gated Firebase credentials could not be loaded") from None
            try:
                credentials.before_request(request, "GET", _HOSTING_ORIGIN, {})
            except Exception:
                # SDK errors may contain credential or response details.
                raise FirebasePublicationError("Firebase credential refresh failed") from None
            value = credentials.token
            if not isinstance(value, str) or not value or value.strip() != value or "\r" in value or "\n" in value:
                raise FirebasePublicationError("Firebase credential refresh returned an invalid token")
            return value

    return token
