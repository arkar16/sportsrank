"""Select committed release-readiness checks for the tree checked out by CI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


def release_readiness_required(event_name: str, event: dict) -> bool:
    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
    elif event_name == "push":
        base = event["before"]
    else:
        raise ValueError(f"unsupported CI event: {event_name}")
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("CI comparison requires an exact base commit")
    if base == "0" * 40:
        return True
    # Compare with the actual tested tree, including GitHub's PR merge commit.
    # --no-renames includes both old and new paths when a release file moves.
    paths = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-only", "-z", base, "HEAD", "--"]
    ).split(b"\0")
    return any(
        path.startswith((b"website/", b"config/")) or path == b"firebase.json"
        for path in paths
    )


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    required = release_readiness_required(os.environ["GITHUB_EVENT_NAME"], event)
    print(f"release_readiness={str(required).lower()}")


if __name__ == "__main__":
    main()
