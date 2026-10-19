"""Apply, revert and restore changes to Steam's files, safely.

Every change follows the same steps:

1. take a lock, so two copies of autoproton can't write at once;
2. refuse if Steam is running (it would overwrite our change when it exits);
3. re-read both files and check the game's settings are still what the user
   was shown;
4. build the new file contents in memory and check they parse back to exactly
   the intended settings;
5. back up the current files;
6. write a small journal, then replace each file atomically;
7. read the files back from disk and check again;
8. log the change in history.

If anything fails after step 6 starts, the files that were already replaced
are put back from memory. If autoproton itself dies part-way, the journal is
found at the next start and the change is either finished or undone.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .atomic import atomic_write, file_sha256, sha256
from .backup import BackupSet, BackupStore
from .context import AppContext
from .history import History, HistoryEntry
from .models import Account, GameSettings, SteamInstall
from .steam import config_store, process
from .steam.vdftext import VdfError

EMPTY_CONFIG = b'"InstallConfigStore"\n{\n}\n'


class TransactionError(RuntimeError):
    """Base class: the message is meant for the user."""


class SteamRunning(TransactionError):
    def __init__(self):
        super().__init__("Steam is running. Close Steam first; it overwrites these settings when it exits.")


class Busy(TransactionError):
    def __init__(self):
        super().__init__("Another autoproton window is saving changes right now. Try again in a moment.")


class StaleState(TransactionError):
    def __init__(self, current: GameSettings):
        self.current = current
        super().__init__("This game's settings changed in Steam since they were shown. Review them again.")


class NothingToDo(TransactionError):
    def __init__(self):
        super().__init__("Steam already has exactly these settings.")


class VerifyFailed(TransactionError):
    pass


class RecoveryNeeded(TransactionError):
    def __init__(self):
        super().__init__("An earlier change was interrupted. Restart autoproton to finish recovering it.")


@dataclass(frozen=True)
class ChangePlan:
    install: SteamInstall
    account: Account
    appid: int
    game: str
    before: GameSettings  # what the user was shown as the current settings
    after: GameSettings
    action: str = "apply"  # "apply" | "revert" | "restore"
    ref: str | None = None  # the history entry being reverted or restored
    note: str = ""


@dataclass(frozen=True)
class DiffRow:
    file: str
    key: str
    before: str
    after: str


def diff(plan: ChangePlan) -> list[DiffRow]:
    rows = []
    if plan.before.launch_options != plan.after.launch_options:
        rows.append(DiffRow(
            str(plan.account.localconfig), f"apps/{plan.appid}/LaunchOptions",
            plan.before.describe_options(), plan.after.describe_options(),
        ))
    if plan.before.compat_tool != plan.after.compat_tool:
        rows.append(DiffRow(
            str(plan.install.config_vdf), f"CompatToolMapping/{plan.appid}",
            plan.before.describe_tool(), plan.after.describe_tool(),
        ))
    return rows


def _read(path: Path, default: bytes | None = None) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        if default is not None:
            return default
        raise TransactionError(f"Steam file not found: {path}") from None


def current_settings(install: SteamInstall, account: Account, appid: int) -> GameSettings:
    try:
        return config_store.read_settings(_read(account.localconfig), _read(install.config_vdf, EMPTY_CONFIG), appid)
    except VdfError as e:
        raise TransactionError(f"Could not read Steam's settings: {e}") from e


class Store:
    """autoproton's own data: backups, history, the lock and the journal."""

    def __init__(self, ctx: AppContext, keep_backups: int = 30):
        self.ctx = ctx
        self.dir = ctx.data_dir
        self.backups = BackupStore(self.dir, keep=keep_backups, clock=ctx.clock)
        self.history = History(self.dir / "history.jsonl", clock=ctx.clock)
        self.journal_path = self.dir / "pending.json"

    @contextlib.contextmanager
    def lock(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.dir / "lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy() from None
            yield
        finally:
            os.close(fd)

    def pending(self) -> dict | None:
        try:
            return json.loads(self.journal_path.read_text())
        except FileNotFoundError:
            return None
        except ValueError:
            return {"broken": True}


def _write_files(changes: list[tuple[Path, bytes | None, bytes]], verify=None) -> None:
    """Replace each file; on any failure put back the ones already replaced."""
    done: list[tuple[Path, bytes | None]] = []
    try:
        for path, old, new in changes:
            atomic_write(path, new, expected_sha256=sha256(old) if old is not None else None, check=True)
            done.append((path, old))
        for path, _old, new in changes:
            if _read(path) != new:
                raise VerifyFailed(f"{path} did not contain what was written; the change was undone.")
        if verify is not None:
            verify()
    except BaseException:
        for path, old in reversed(done):
            with contextlib.suppress(OSError):
                if old is None:
                    Path(os.path.realpath(path)).unlink()
                else:
                    atomic_write(path, old)
        raise


def execute(ctx: AppContext, store: Store, plan: ChangePlan) -> HistoryEntry:
    with store.lock():
        if store.pending() is not None:
            raise RecoveryNeeded()
        if process.is_running(ctx):
            raise SteamRunning()
        local_path, cfg_path = plan.account.localconfig, plan.install.config_vdf
        local = _read(local_path)
        cfg_exists = cfg_path.exists()
        cfg = _read(cfg_path, EMPTY_CONFIG)
        try:
            current = config_store.read_settings(local, cfg, plan.appid)
        except VdfError as e:
            raise TransactionError(f"Could not read Steam's settings: {e}") from e
        if current != plan.before:
            raise StaleState(current)
        try:
            # New text is checked; undo and restore write back what Steam had, as it was.
            # Undo also removes, once empty, the blocks that were missing before the
            # undone change, and only those.
            undone = store.history.get(plan.ref) if (plan.action == "revert" and plan.ref) else None
            prune = frozenset(undone.missing) if undone else frozenset()
            new_local, new_cfg = config_store.build_changes(
                local, cfg, plan.appid, plan.after, current, check=plan.action == "apply", prune=prune)
        except (VdfError, config_store.InvalidSetting) as e:
            raise TransactionError(f"Could not change Steam's settings: {e}; nothing was written.") from e
        if new_local is None and new_cfg is None:
            raise NothingToDo()

        changes: list[tuple[str, Path, bytes | None, bytes]] = []
        if new_local is not None:
            changes.append(("localconfig", local_path, local, new_local))
        if new_cfg is not None:
            changes.append(("config", cfg_path, cfg if cfg_exists else None, new_cfg))
        backup = store.backups.create(
            [(label, path, old) for label, path, old, _new in changes if old is not None],
            reason=plan.action, appid=plan.appid, game=plan.game,
        )
        entry = HistoryEntry(
            id=store.history.new_id(plan.appid), ts=ctx.clock(), action=plan.action,
            steam_root=str(plan.install.root), account_id=plan.account.account_id, appid=plan.appid,
            game=plan.game, before=current, after=plan.after, backup_set=backup.id, ref=plan.ref, note=plan.note,
            missing=tuple(config_store.missing_blocks(local, cfg, plan.appid)),
        )
        journal = {
            "entry": entry.to_json(),
            "files": [
                {"path": str(os.path.realpath(p)), "old": sha256(o) if o is not None else None, "new": sha256(n)}
                for _label, p, o, n in changes
            ],
        }
        atomic_write(store.journal_path, json.dumps(journal, indent=2).encode())
        def verify():
            if current_settings(plan.install, plan.account, plan.appid) != plan.after:
                raise VerifyFailed("Steam's files did not read back as intended; the change was undone.")

        try:
            _write_files([(p, o, n) for _label, p, o, n in changes], verify)
        except BaseException:
            store.journal_path.unlink(missing_ok=True)
            raise
        store.history.append(entry)
        store.journal_path.unlink(missing_ok=True)
        store.backups.prune()
        return entry


def recover(ctx: AppContext, store: Store) -> str | None:
    """Finish or undo a change that was interrupted. Returns a message, or None if nothing was pending."""
    journal = store.pending()
    if journal is None:
        return None
    if journal.get("broken") or "files" not in journal:
        store.journal_path.unlink(missing_ok=True)
        return "Found an unreadable note about an interrupted change and removed it. Check History if a game looks wrong."
    with store.lock():
        files = journal["files"]
        states = []
        for f in files:
            cur = file_sha256(Path(f["path"]))
            states.append("new" if cur == f["new"] else "old" if cur == f["old"] else "other")
        entry = HistoryEntry.from_json(journal["entry"])
        if all(s == "new" for s in states):
            store.history.append(entry)
            store.journal_path.unlink()
            return f"Finished saving an interrupted change to {entry.game}."
        if all(s == "old" for s in states):
            store.journal_path.unlink()
            return f"An interrupted change to {entry.game} had not been saved yet; nothing was changed."
        if process.is_running(ctx):
            return "An interrupted change left Steam's files half-updated. Close Steam and restart autoproton to repair it."
        backup = store.backups.get(entry.backup_set) if entry.backup_set else None
        repaired, unrepaired = [], []
        for f, state in zip(files, states, strict=True):
            path = Path(f["path"])
            if state == "old":
                continue
            if state == "other":
                # changed again since (Steam rewrites these files when it exits): leave it be
                unrepaired.append(path.name)
                continue
            label = next((b.label for b in backup.files if b.source == f["path"]), None) if backup else None
            if label:
                atomic_write(path, store.backups.read(backup, label))
                repaired.append(path.name)
            elif f["old"] is None:
                path.unlink(missing_ok=True)  # autoproton created this file; take it away again
                repaired.append(path.name)
            else:
                unrepaired.append(path.name)
        store.journal_path.unlink()
        # Judge by what is in effect now, not by which files changed: Steam rewrites
        # both files when it exits, so "changed since" alone says little.
        try:
            local_path = Path(entry.steam_root) / "userdata" / entry.account_id / "config" / "localconfig.vdf"
            now = config_store.read_settings(_read(local_path), _read(Path(entry.steam_root) / "config" / "config.vdf",
                                                                      EMPTY_CONFIG), entry.appid)
        except (TransactionError, VdfError, OSError):
            now = None
        if now == entry.before:
            return (f"An interrupted change to {entry.game} was undone"
                    + (f" ({', '.join(repaired)} restored from backup)." if repaired else "."))
        store.history.append(entry)
        if now == entry.after:
            return f"Finished saving an interrupted change to {entry.game}."
        return (f"An interrupted change to {entry.game} may still be partly in effect"
                + (f" ({', '.join(unrepaired)} changed since)" if unrepaired else "")
                + ". It's in History, where you can undo it.")


def restore_file(ctx: AppContext, store: Store, backup: BackupSet, label: str, install: SteamInstall,
                 account: Account | None) -> HistoryEntry:
    """Put back a whole file from a backup. The current file is backed up first."""
    with store.lock():
        if process.is_running(ctx):
            raise SteamRunning()
        info = backup.file(label)
        if info is None:
            raise TransactionError(f"Backup {backup.id} has no copy of {label}.")
        target = Path(info.source)
        data = store.backups.read(backup, label)
        current = _read(target, b"")
        safety = store.backups.create([(label, target, current)] if current else [], reason="restore-file")
        _write_files([(target, current if target.exists() else None, data)])
        entry = HistoryEntry(
            id=store.history.new_id(None), ts=ctx.clock(), action="restore-file", steam_root=str(install.root),
            account_id=account.account_id if (account and label == "localconfig") else "", appid=None,
            game=f"{target.name} from backup {backup.id}", before=GameSettings(), after=GameSettings(),
            backup_set=safety.id, ref=backup.id,
        )
        store.history.append(entry)
        return entry
