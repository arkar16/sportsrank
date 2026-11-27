"""Install the API-backed basketball build and retire stale generated game pages."""

from datetime import datetime, timezone
import filecmp
from hashlib import sha256
import json
import re
import os
from pathlib import Path
import shutil
import tempfile

DIRECTORIES = {
    "seasons",
    "teams",
    "conferences",
    "history",
    "performance",
    "tournaments",
    "editions",
    "picks",
}


MANIFEST = Path("history/cbbd-artifacts-v1.json")
EDITION = re.compile(r"editions/(\d{4}-\d{2}-\d{2}T\d{6}Z)(?:-cbbd)?\.html")
LEDGER = re.compile(r"seasons/\d{4}/excitement/(?:bev|baselines)-v1\.csv")
PICK = re.compile(r"picks/\d{4}-\d{2}-\d{2}/[a-z0-9-]+\.html")
# Reproduced byte-for-byte from CBBD and independently verified as issued.
ISSUED = {
    "editions/2026-11-19T185800Z.html": "65786287f4e03d8a59377b6d8fc0d6f53292617aeb8f8e66658ec9ca02c14c07"
}


def restore_artifacts(destination, stage, as_of):
    if as_of.utcoffset() is None:
        raise ValueError("preparation time requires a timezone")
    repository = Path(__file__).resolve().parents[1] / "website/cbb"
    entries = {key: (value, repository) for key, value in ISSUED.items()}
    manifest = destination / MANIFEST
    if manifest.is_file():
        record = json.loads(manifest.read_bytes())
        if record.get("provider") != "CollegeBasketballData":
            raise ValueError("unapproved basketball publication source")
        earlier = datetime.fromisoformat(record["prepared_at"]) <= as_of
        for path, digest in record["files"].items():
            if not (EDITION.fullmatch(path) or LEDGER.fullmatch(path) or PICK.fullmatch(path)):
                raise ValueError("unexpected path in API publication history")
            if path in ISSUED and digest != ISSUED[path]:
                raise ValueError("issued edition digest changed")
            if earlier or EDITION.fullmatch(path):
                entries[path] = digest, destination
    for path, (digest, source) in entries.items():
        edition = EDITION.fullmatch(path)
        if edition and datetime.strptime(edition[1], "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc) > as_of:
            continue
        data = (source / path).read_bytes()
        if sha256(data).hexdigest() != digest:
            raise ValueError(f"archived API evidence changed in {path}")
        target = stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def record_artifacts(stage, as_of):
    files = {}
    for pattern in (
        "editions/*.html",
        "seasons/*/excitement/bev-v1.csv",
        "seasons/*/excitement/baselines-v1.csv",
        "picks/*/*.html",
    ):
        for path in sorted(stage.glob(pattern)):
            files[path.relative_to(stage).as_posix()] = sha256(path.read_bytes()).hexdigest()
    target = stage / MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            dict(provider="CollegeBasketballData", prepared_at=as_of.isoformat(), files=files),
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


def install_pages(stage: Path, destination: Path):
    if not (stage / "index.html").is_file() or not (stage / "seasons/index.html").is_file():
        raise ValueError("incomplete basketball site")
    _sync(stage, destination, DIRECTORIES)


def install_conferences(stage, destination):
    if not (stage / "index.html").is_file():
        raise ValueError("incomplete basketball conference build")
    _sync(stage, destination, (".",))


def _sync(stage, destination, directories):
    built = set()
    for source in stage.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(stage)
        built.add(relative)
        target = destination / relative
        if target.exists() and filecmp.cmp(source, target, shallow=False):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, tmp)
        try:
            shutil.copymode(source, tmp.name)
            os.replace(tmp.name, target)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    for category in sorted(directories):
        root = destination / category
        for path in sorted(root.rglob("*"), reverse=True):
            if (
                path.is_file()
                and path.suffix in (".html", ".csv", ".json")
                and path.relative_to(destination) not in built
            ):
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
