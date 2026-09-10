"""Atomic JSON persistence for normalized season source data."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any


class SnapshotCache:
    _thread_locks: dict[str, threading.RLock] = {}
    _thread_locks_guard = threading.Lock()

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, year: int, classification: str) -> Path:
        return self.root / f"cfb-{classification.lower()}-{int(year)}.json"

    @contextmanager
    def lock(self, year: int, classification: str):
        """Exclusively lock one snapshot across threads and local processes."""
        self.root.mkdir(parents=True, exist_ok=True)
        snapshot_path = self.path_for(year, classification)
        lock_key = str(snapshot_path.resolve())
        with self._thread_locks_guard:
            thread_lock = self._thread_locks.setdefault(
                lock_key, threading.RLock()
            )
        with thread_lock:
            lock_path = snapshot_path.with_suffix(snapshot_path.suffix + ".lock")
            with lock_path.open("a+b") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def load(self, year: int, classification: str) -> dict[str, Any] | None:
        path = self.path_for(year, classification)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
        if value.get("year") != int(year) or value.get("classification") != classification.upper():
            raise ValueError(f"Snapshot identity does not match {path}")
        return value

    def save(self, year: int, classification: str, value: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(year, classification)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                json.dump(value, target, sort_keys=True, separators=(",", ":"))
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return path
