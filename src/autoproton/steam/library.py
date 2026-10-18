"""Installed games, read from libraryfolders.vdf and the appmanifest files."""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..models import Game, SteamInstall
from .vdftext import VdfDocument, VdfError

# Steamworks Common Redistributables is not a game, and has no toolmanifest.vdf.
NOT_GAMES = {228980}
# Valve's own tool names. Narrow on purpose: a game can be called "Proton Pulse".
_TOOL_NAME = re.compile(
    r"^(Proton (\d|Experimental|Hotfix|Next|EasyAntiCheat|BattlEye)|Steam Linux Runtime|Steamworks Common)",
    re.IGNORECASE,
)


def library_paths(install: SteamInstall) -> list[Path]:
    """Every library folder, starting with the Steam root itself."""
    paths: list[Path] = [install.root]
    for vdf in (install.root / "steamapps" / "libraryfolders.vdf", install.root / "config" / "libraryfolders.vdf"):
        try:
            doc = VdfDocument.from_file(vdf)
        except (OSError, VdfError):
            continue
        block = doc.find(["libraryfolders"])
        for node in (block.children if block and block.is_block else []):
            if not node.key.isdigit():
                continue
            raw = node.get("path") if node.is_block else node.value
            if raw:
                paths.append(Path(raw))
        break
    unique: list[Path] = []
    seen = set()
    for p in paths:
        try:
            real = os.path.realpath(p)
        except OSError:
            continue
        if real not in seen and os.path.isdir(os.path.join(real, "steamapps")):
            seen.add(real)
            unique.append(Path(real))
    return unique


def read_manifest(path: Path) -> dict | None:
    try:
        doc = VdfDocument.from_file(path)
    except (OSError, VdfError):
        return None
    node = doc.find(["AppState"])
    return node.to_dict() if node and node.is_block else None


def iter_manifests(install: SteamInstall):
    """Yield (library, manifest dict) for every appmanifest in every library."""
    for lib in library_paths(install):
        steamapps = lib / "steamapps"
        try:
            names = sorted(os.listdir(steamapps))
        except OSError:
            continue
        for name in names:
            if name.startswith("appmanifest_") and name.endswith(".acf"):
                data = read_manifest(steamapps / name)
                if data:
                    yield lib, {k.lower(): v for k, v in data.items()}


def is_tool(lib: Path, manifest: dict) -> bool:
    try:
        appid = int(manifest.get("appid", "0"))
    except ValueError:
        return True
    if appid in NOT_GAMES:
        return True
    installdir = manifest.get("installdir") or ""
    if installdir and (lib / "steamapps" / "common" / installdir / "toolmanifest.vdf").exists():
        return True
    return bool(_TOOL_NAME.match(manifest.get("name") or ""))


def installed_games(install: SteamInstall) -> list[Game]:
    games: dict[int, Game] = {}
    for lib, m in iter_manifests(install):
        if is_tool(lib, m):
            continue
        try:
            appid = int(m.get("appid", ""))
        except ValueError:
            continue
        if appid not in games:
            games[appid] = Game(appid, m.get("name") or f"App {appid}", lib, m.get("installdir") or "")
    return sorted(games.values(), key=lambda g: g.name.casefold())
