"""Tiny on-disk cache for ProtonDB and GitHub responses."""

from __future__ import annotations

import gzip
import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from .atomic import atomic_write


class Cache:
    def __init__(self, folder: Path, clock: Callable[[], float] = time.time):
        self.folder = Path(folder)
        self.clock = clock

    def _path(self, key: str) -> Path:
        return self.folder / (re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json.gz")

    def _load(self, key: str):
        try:
            data = json.loads(gzip.decompress(self._path(key).read_bytes()))
            return float(data["saved"]), data["value"]
        except (OSError, ValueError, KeyError, EOFError, gzip.BadGzipFile):
            return None

    def get(self, key: str, max_age: float):
        """The cached value if it is younger than ``max_age`` seconds, else None."""
        hit = self._load(key)
        if hit is None or self.clock() - hit[0] > max_age:
            return None
        return hit[1]

    def get_stale(self, key: str):
        """(value, age in seconds) regardless of age, or None."""
        hit = self._load(key)
        return None if hit is None else (hit[1], self.clock() - hit[0])

    def put(self, key: str, value) -> None:
        payload = json.dumps({"saved": self.clock(), "value": value}, separators=(",", ":")).encode()
        try:
            atomic_write(self._path(key), gzip.compress(payload, mtime=0))
        except OSError:
            pass  # a cache that can't be written is just a slower cache

    def clear(self) -> int:
        removed = 0
        for p in self.folder.glob("*.json.gz"):
            p.unlink(missing_ok=True)
            removed += 1
        return removed
