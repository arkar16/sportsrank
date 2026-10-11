"""Prepare the complete corrected site from one private input configuration.

Calculation uses retained inputs only and never calls CFBD. When a successor is
selected, the existing exporter authenticates immutable GitHub evidence before
local validation. This command never merges, seals or deploys.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

from cfb.public_site import main as export_main

SCHEMA = "sportsrank-site-preparation/v1"
PATH_FIELDS = {
    "private_candidate", "baseline_archive", "source_input_root", "source_archive",
    "trusted_input_manifest", "firebase_json", "code_root", "market_inputs",
    "historical_games_csv", "successor_baseline",
}
VALUE_FIELDS = {"baseline_sha256", "source_archive_sha256", "historical_games_sha256"}
REQUIRED = (PATH_FIELDS - {"successor_baseline"}) | VALUE_FIELDS


def export_arguments(config_path: Path, output: Path) -> list[str]:
    config = json.loads(config_path.read_bytes())
    if not isinstance(config, dict) or config.get("schema") != SCHEMA:
        raise ValueError("invalid site preparation configuration schema")
    if not REQUIRED <= config.keys() or set(config) - {"schema"} - PATH_FIELDS - VALUE_FIELDS:
        raise ValueError("site preparation configuration has missing or unknown fields")
    if output.exists():
        raise ValueError("site preparation output must be new")
    result = ["export"]
    for key in sorted(config.keys() - {"schema"}):
        value = config[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("site preparation values must be nonempty strings")
        if key in PATH_FIELDS:
            value = str((config_path.parent / value).resolve())
        result += ["--" + key.replace("_", "-"), value]
    result += ["--output-site", str(output / "site"), "--receipt", str(output / "receipt.json")]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="private path/hash configuration; no credentials")
    parser.add_argument("--output", type=Path, required=True, help="new preparation directory")
    args = parser.parse_args(argv)
    try:
        arguments = export_arguments(args.config.resolve(), args.output.resolve())
        # Claim the output before authentication/validation; no accidental rerun
        # can overwrite a partial attempt or its retained evidence.
        args.output.mkdir(parents=True, exist_ok=False)
        original_token = os.environ.get("GITHUB_TOKEN")
        try:
            if "--successor-baseline" in arguments and not original_token:
                # Reuse the same authenticated GitHub account as repository
                # tooling. Keep the value in process memory/environment only.
                token = os.environ.get("GH_TOKEN")
                if not token:
                    try:
                        token = subprocess.check_output(["gh", "auth", "token"], text=True,
                                                        stderr=subprocess.DEVNULL).strip()
                    except (OSError, subprocess.CalledProcessError) as error:
                        raise ValueError("existing GitHub authentication is unavailable") from error
                if not token:
                    raise ValueError("existing GitHub authentication is unavailable")
                os.environ["GITHUB_TOKEN"] = token
            status = export_main(arguments)
        finally:
            if original_token is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = original_token
        if status == 0:
            print(f"Prepared complete corrected site: {args.output / 'site'}")
            print(f"Reviewed receipt candidate: {args.output / 'receipt.json'}")
        return status
    except (OSError, ValueError) as error:
        parser.exit(1, f"site preparation failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
