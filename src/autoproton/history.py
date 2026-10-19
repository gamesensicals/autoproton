"""A log of every change autoproton made, so any of them can be undone.

Stored as one JSON object per line in ``~/.local/share/autoproton/history.jsonl``.
Lines are only ever appended. "Works" / "doesn't work" marks are separate
lines that refer back to a change, so the file is never rewritten.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .models import GameSettings

MARKS = ("untested", "works", "broken")


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    ts: float
    action: str  # "apply" | "revert" | "restore" | "restore-file"
    steam_root: str
    account_id: str
    appid: int | None
    game: str
    before: GameSettings
    after: GameSettings
    backup_set: str | None = None
    ref: str | None = None
    note: str = ""
    mark: str = "untested"
    missing: tuple[str, ...] = ()  # blocks missing from Steam's files before this change; undo may remove only these

    def to_json(self) -> dict:
        return {
            "type": "change", "id": self.id, "ts": self.ts, "action": self.action, "steam_root": self.steam_root,
            "account_id": self.account_id, "appid": self.appid, "game": self.game,
            "before": self.before.to_json(), "after": self.after.to_json(),
            "backup_set": self.backup_set, "ref": self.ref, "note": self.note, "missing": list(self.missing),
        }

    @classmethod
    def from_json(cls, d: dict) -> HistoryEntry:
        return cls(
            id=d["id"], ts=float(d["ts"]), action=d["action"], steam_root=d.get("steam_root", ""),
            account_id=str(d.get("account_id", "")), appid=d.get("appid"), game=d.get("game", ""),
            before=GameSettings.from_json(d.get("before") or {}), after=GameSettings.from_json(d.get("after") or {}),
            backup_set=d.get("backup_set"), ref=d.get("ref"), note=d.get("note", ""),
            # lines from before "missing" existed say "created"; every block a change created was missing
            missing=tuple(d["missing"] if "missing" in d else d.get("created") or ()),
        )


class History:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock

    def _append_line(self, obj: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            if os.fstat(fd).st_size:
                # make sure an earlier torn write can't glue onto this line
                with open(self.path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        line = b"\n" + line
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def new_id(self, appid: int | None) -> str:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock()))
        return f"{stamp}-{appid if appid is not None else 'file'}-{secrets.token_hex(2)}"

    def append(self, entry: HistoryEntry) -> HistoryEntry:
        self._append_line(entry.to_json())
        return entry

    def mark(self, entry_id: str, mark: str) -> None:
        if mark not in MARKS:
            raise ValueError(f"mark must be one of {', '.join(MARKS)}")
        if self.get(entry_id) is None:
            raise KeyError(entry_id)
        self._append_line({"type": "mark", "ref": entry_id, "mark": mark, "ts": self.clock()})

    def _read(self) -> list[HistoryEntry]:
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return []
        entries: dict[str, HistoryEntry] = {}
        marks: dict[str, str] = {}
        for line in raw:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                if obj.get("type") == "change":
                    e = HistoryEntry.from_json(obj)
                    entries[e.id] = e
                elif obj.get("type") == "mark" and obj.get("mark") in MARKS:
                    marks[obj["ref"]] = obj["mark"]
            except (ValueError, KeyError, TypeError, AttributeError):
                continue  # a torn or hand-edited line; skip it
        return [replace(e, mark=marks.get(e.id, "untested")) for e in entries.values()]

    def entries(self, steam_root: str | None = None, account_id: str | None = None, appid: int | None = None) -> list[HistoryEntry]:
        """Matching entries, newest first."""
        out = [
            e for e in self._read()
            if (steam_root is None or e.steam_root == steam_root)
            and (account_id is None or e.account_id in (account_id, ""))
            and (appid is None or e.appid == appid)
        ]
        return sorted(out, key=lambda e: (e.ts, e.id), reverse=True)

    def get(self, entry_id: str) -> HistoryEntry | None:
        return next((e for e in self._read() if e.id == entry_id), None)
