"""Is Steam running, and closing or starting it.

Steam keeps its settings in memory and writes them back when it exits, so any
change made while it runs would be overwritten. autoproton only writes while
Steam is fully closed.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

from ..context import AppContext
from ..models import SteamInstall
from .locate import FLATPAK_ID

STEAM_PROCESS_NAMES = {"steam", "steam.sh", "steamwebhelper"}

# Variables that PyInstaller or the AppImage set for our own bundled
# libraries. Passed on to Steam or a browser, they would make those programs
# load our copies of Qt, Python or OpenSSL.
_RESTORE = ("LD_LIBRARY_PATH", "LD_PRELOAD")
_DROP = (
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QTWEBENGINEPROCESS_PATH",
    "PYTHONHOME",
    "PYTHONPATH",
    "_MEIPASS2",
    "_PYI_APPLICATION_HOME_DIR",
    "_PYI_ARCHIVE_FILE",
    "_PYI_PARENT_PROCESS_LEVEL",
    "_PYI_SPLASH_IPC",
    "APPDIR",
    "APPIMAGE",
    "ARGV0",
    "OWD",
    "SSL_CERT_FILE_AUTOPROTON",
)


def clean_env(env: Mapping[str, str]) -> dict[str, str]:
    """The environment to hand to other programs, without our bundle's variables."""
    out = dict(env)
    for var in _RESTORE:
        orig = out.pop(var + "_ORIG", None)
        if orig is not None:
            out[var] = orig
        elif out.get("_PYI_APPLICATION_HOME_DIR") or out.get("APPIMAGE") or out.get("_MEIPASS2"):
            out.pop(var, None)
    for var in _DROP:
        out.pop(var, None)
    for var in [k for k in out if k.startswith(("_PYI_", "_MEIPASS"))]:
        out.pop(var)
    return out


def steam_pids(ctx: AppContext) -> list[int]:
    proc = ctx.sys_root / "proc"
    pids = []
    try:
        entries = os.listdir(proc)
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit() or int(entry) == ctx.own_pid:
            continue
        try:
            comm = (proc / entry / "comm").read_text(errors="replace").strip()
        except OSError:
            continue
        if comm in STEAM_PROCESS_NAMES and not _is_zombie(proc / entry):
            pids.append(int(entry))
    return sorted(pids)


def _is_zombie(proc_dir) -> bool:
    """A process that has exited but not been collected yet (e.g. a finished `steam -shutdown`)."""
    try:
        stat = (proc_dir / "stat").read_text(errors="replace")
    except OSError:
        return False
    # format: pid (comm) state ...; comm may contain spaces or parentheses
    return stat.rpartition(")")[2].split()[:1] == ["Z"]


def is_running(ctx: AppContext) -> bool:
    return bool(steam_pids(ctx))


def in_game_mode(env: Mapping[str, str]) -> bool:
    """True inside the Steam Deck's Game Mode, where Steam is the whole session."""
    desktop = (env.get("XDG_CURRENT_DESKTOP", "") + ":" + env.get("XDG_SESSION_DESKTOP", "")).lower()
    return "gamescope" in desktop or env.get("SteamGamepadUI") == "1"


def steam_command(ctx: AppContext, install: SteamInstall) -> list[str]:
    if install.flavor == "flatpak":
        return ["flatpak", "run", FLATPAK_ID]
    if install.flavor == "snap":
        return ["snap", "run", "steam"]
    found = ctx.which("steam")
    if found:
        return [found]
    return [str(install.root / "steam.sh")]


def shutdown(ctx: AppContext, install: SteamInstall) -> None:
    ctx.spawn(steam_command(ctx, install) + ["-shutdown"], clean_env(ctx.env))


def launch(ctx: AppContext, install: SteamInstall, url: str | None = None) -> None:
    ctx.spawn(steam_command(ctx, install) + ([url] if url else []), clean_env(ctx.env))


def open_url(ctx: AppContext, url: str) -> None:
    ctx.spawn(["xdg-open", url], clean_env(ctx.env))


def wait_for_exit(
    ctx: AppContext, timeout: float = 60.0, poll: float = 0.5, cancel: threading.Event | None = None
) -> bool:
    """Wait until no Steam process is left. Returns False on timeout or cancel."""
    deadline = ctx.clock() + timeout
    while True:
        if not is_running(ctx):
            return True
        if (cancel and cancel.is_set()) or ctx.clock() >= deadline:
            return False
        ctx.sleep(poll)
