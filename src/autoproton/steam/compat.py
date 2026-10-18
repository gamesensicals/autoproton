"""Compatibility tools (Proton versions) and their internal names.

Steam stores a tool's *internal* name in config.vdf, which differs from the
name shown in its UI: "Proton 9.0" is ``proton_9``, "Proton 6.3" is
``proton_63``, and a custom tool like GE-Proton uses whatever its
compatibilitytool.vdf declares.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..context import AppContext
from ..models import CompatTool, SteamInstall
from .library import iter_manifests
from .vdftext import VdfDocument, VdfError

EXPERIMENTAL = "proton_experimental"
HOTFIX = "proton_hotfix"

_OFFICIAL_DISPLAY = re.compile(r"^Proton (\d+)\.(\d+)$")
_GE = re.compile(r"GE-?Proton[-_ ]?(\d+)[-.](\d+)", re.I)
_LEGACY_GE = re.compile(r"Proton-(\d+)\.(\d+)-GE-(\d+)", re.I)

_KNOWN_DISPLAY = {
    EXPERIMENTAL: "Proton Experimental",
    HOTFIX: "Proton Hotfix",
    "proton_63": "Proton 6.3",
    "proton_513": "Proton 5.13",
    "proton_411": "Proton 4.11",
    "proton_42": "Proton 4.2",
    "proton_316": "Proton 3.16",
    "proton_37": "Proton 3.7",
}


def official_name(major: int, minor: int) -> str:
    """Internal name for an official Proton release: 10.0 -> proton_10, 6.3 -> proton_63."""
    return f"proton_{major}" if minor == 0 else f"proton_{major}{minor}"


def official_name_from_display(display: str) -> str | None:
    if display == "Proton Experimental":
        return EXPERIMENTAL
    if display == "Proton Hotfix":
        return HOTFIX
    m = _OFFICIAL_DISPLAY.match(display.strip())
    return official_name(int(m.group(1)), int(m.group(2))) if m else None


def display_name(internal: str) -> str:
    if internal in _KNOWN_DISPLAY:
        return _KNOWN_DISPLAY[internal]
    m = re.fullmatch(r"proton_(\d+)", internal)
    if m and (int(m.group(1)) >= 7 or m.group(1) == "5"):
        return f"Proton {int(m.group(1))}.0"
    return internal


def ge_version(name: str) -> tuple[int, int, int] | None:
    """(major, build, 0) for GE-Proton10-34; (6, 21, 2) for the old Proton-6.21-GE-2 style."""
    m = _GE.search(name)
    if m:
        return (int(m.group(1)), int(m.group(2)), 0)
    m = _LEGACY_GE.search(name)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def read_compatibilitytool_vdf(path: Path) -> list[tuple[str, str]]:
    """(internal name, display name) pairs declared by a custom tool."""
    try:
        doc = VdfDocument.from_file(path)
    except (OSError, VdfError):
        return []
    block = doc.find(["compatibilitytools", "compat_tools"])
    out = []
    for node in (block.children if block and block.is_block else []):
        if node.is_block:
            out.append((node.key, node.get("display_name") or node.key))
    return out


def custom_tool_dirs(ctx: AppContext, install: SteamInstall) -> list[Path]:
    dirs = [install.compat_dir]
    if install.flavor == "native":
        dirs += [
            ctx.sys_root / "usr/share/steam/compatibilitytools.d",
            ctx.sys_root / "usr/local/share/steam/compatibilitytools.d",
        ]
    for extra in (ctx.env.get("STEAM_EXTRA_COMPAT_TOOLS_PATHS") or "").split(":"):
        if extra:
            dirs.append(Path(extra))
    return dirs


def installed_tools(ctx: AppContext, install: SteamInstall) -> list[CompatTool]:
    tools: dict[str, CompatTool] = {}
    for lib, m in iter_manifests(install):
        internal = official_name_from_display(m.get("name") or "")
        installdir = m.get("installdir") or ""
        path = lib / "steamapps" / "common" / installdir
        if internal and installdir and (path / "proton").exists():
            tools.setdefault(internal, CompatTool(internal, m["name"], "official", path))
    for base in custom_tool_dirs(ctx, install):
        try:
            subdirs = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
        for sub in subdirs:
            if sub.name.startswith("."):
                continue
            for internal, shown in read_compatibilitytool_vdf(sub / "compatibilitytool.vdf"):
                tools.setdefault(
                    internal,
                    CompatTool(internal, shown, "custom", sub, ge_version(internal) or ge_version(shown)),
                )
    return sorted(tools.values(), key=lambda t: (t.kind, t.display_name.casefold()))


def newest_ge(tools: list[CompatTool], min_major: int = 0) -> CompatTool | None:
    candidates = [t for t in tools if t.ge_version and t.ge_version[0] >= min_major]
    return max(candidates, key=lambda t: t.ge_version) if candidates else None
