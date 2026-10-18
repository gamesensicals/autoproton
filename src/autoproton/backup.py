"""Copies of Steam's files taken before every change.

Each change gets a folder under ``~/.local/share/autoproton/backups/`` holding
gzipped copies of the files about to be replaced plus a ``manifest.json``.
The very first copy ever taken of each file is also kept forever under
``backups/pristine/``, so the state from before autoproton was first used
can always be recovered.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .atomic import atomic_write, sha256


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupFile:
    label: str  # "localconfig" | "config"
    source: str  # the real path it came from
    sha256: str
    size: int


@dataclass(frozen=True)
class BackupSet:
    id: str
    created: float
    reason: str
    appid: int | None
    game: str | None
    files: tuple[BackupFile, ...]
    path: Path

    def file(self, label: str) -> BackupFile | None:
        return next((f for f in self.files if f.label == label), None)


class BackupStore:
    def __init__(self, data_dir: Path, keep: int = 30, clock: Callable[[], float] = time.time):
        self.root = Path(data_dir) / "backups"
        self.keep = keep
        self.clock = clock

    def create(
        self, files: list[tuple[str, Path, bytes]], reason: str, appid: int | None = None, game: str | None = None
    ) -> BackupSet:
        now = self.clock()
        set_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)) + "-" + secrets.token_hex(3)
        folder = self.root / set_id
        folder.mkdir(parents=True)
        entries = []
        for label, path, data in files:
            real = os.path.realpath(path)
            atomic_write(folder / f"{label}.vdf.gz", gzip.compress(data, mtime=0))
            entries.append({"label": label, "source": real, "sha256": sha256(data), "size": len(data)})
            self._keep_pristine(label, real, data, now)
        manifest = {"id": set_id, "created": now, "reason": reason, "appid": appid, "game": game, "files": entries}
        atomic_write(folder / "manifest.json", json.dumps(manifest, indent=2).encode())
        return self._load(folder)

    def _keep_pristine(self, label: str, real: str, data: bytes, now: float) -> None:
        folder = self.root / "pristine" / hashlib.sha1(real.encode()).hexdigest()[:16]
        if (folder / "manifest.json").exists():
            return
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write(folder / f"{label}.vdf.gz", gzip.compress(data, mtime=0))
        manifest = {
            "id": f"pristine/{folder.name}", "created": now, "reason": "first backup", "appid": None, "game": None,
            "files": [{"label": label, "source": real, "sha256": sha256(data), "size": len(data)}],
        }
        atomic_write(folder / "manifest.json", json.dumps(manifest, indent=2).encode())

    def _load(self, folder: Path) -> BackupSet:
        try:
            m = json.loads((folder / "manifest.json").read_text())
            files = tuple(BackupFile(f["label"], f["source"], f["sha256"], int(f["size"])) for f in m["files"])
            return BackupSet(m["id"], float(m["created"]), m.get("reason", ""), m.get("appid"), m.get("game"), files, folder)
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise BackupError(f"backup {folder.name} is unreadable: {e}") from e

    def list(self, include_pristine: bool = True) -> list[BackupSet]:
        """All backup sets, newest first. Pristine copies come last."""
        sets, pristine = [], []
        try:
            folders = [p for p in self.root.iterdir() if p.is_dir() and p.name != "pristine"]
        except OSError:
            folders = []
        for folder in folders:
            try:
                sets.append(self._load(folder))
            except BackupError:
                continue
        if include_pristine:
            try:
                for folder in (self.root / "pristine").iterdir():
                    try:
                        pristine.append(self._load(folder))
                    except BackupError:
                        continue
            except OSError:
                pass
        sets.sort(key=lambda s: (s.created, s.id), reverse=True)
        pristine.sort(key=lambda s: s.created)
        return sets + pristine

    def get(self, set_id: str) -> BackupSet:
        folder = self.root / set_id
        if not set_id or ".." in Path(set_id).parts or not (folder / "manifest.json").is_file():
            raise BackupError(f"no backup called {set_id!r}")
        return self._load(folder)

    def read(self, backup: BackupSet, label: str) -> bytes:
        info = backup.file(label)
        if info is None:
            raise BackupError(f"backup {backup.id} has no copy of {label}")
        data = gzip.decompress((backup.path / f"{label}.vdf.gz").read_bytes())
        if sha256(data) != info.sha256:
            raise BackupError(f"backup {backup.id} copy of {label} is damaged (checksum mismatch)")
        return data

    def prune(self) -> list[str]:
        removed = []
        for old in self.list(include_pristine=False)[self.keep:]:
            shutil.rmtree(old.path, ignore_errors=True)
            removed.append(old.id)
        return removed
