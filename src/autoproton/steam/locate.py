"""Find Steam installs and the accounts signed in to them."""

from __future__ import annotations

import os
from pathlib import Path

from ..context import AppContext
from ..models import Account, SteamInstall
from .vdftext import VdfDocument, VdfError

FLATPAK_ID = "com.valvesoftware.Steam"
STEAMID64_BASE = 76561197960265728

_CANDIDATES = (
    ".steam/steam",
    ".steam/root",
    ".local/share/Steam",
    f".var/app/{FLATPAK_ID}/.local/share/Steam",
    f".var/app/{FLATPAK_ID}/data/Steam",
    f".var/app/{FLATPAK_ID}/.steam/steam",
    "snap/steam/common/.local/share/Steam",
    "snap/steam/common/.steam/steam",
)


def flavor_for(root: Path, home: Path) -> str:
    text = str(root)
    if f"/.var/app/{FLATPAK_ID}/" in text + "/":
        return "flatpak"
    if str(home / "snap" / "steam") in text:
        return "snap"
    return "native"


def _looks_like_steam(root: Path) -> bool:
    return (root / "steamapps").is_dir() or (root / "config" / "config.vdf").is_file()


def find_installs(ctx: AppContext) -> list[SteamInstall]:
    """Every Steam install for this user, most recently used first."""
    candidates = [ctx.steam_root_override] if ctx.steam_root_override else [ctx.home / c for c in _CANDIDATES]
    seen: dict[str, SteamInstall] = {}
    for cand in candidates:
        try:
            if not cand.is_dir() or not _looks_like_steam(cand):
                continue
            real = Path(os.path.realpath(cand))
        except OSError:
            continue
        if str(real) not in seen:
            seen[str(real)] = SteamInstall(real, flavor_for(real, ctx.home))

    def last_used(inst: SteamInstall) -> float:
        try:
            return inst.loginusers_vdf.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(seen.values(), key=last_used, reverse=True)


def accounts(install: SteamInstall) -> list[Account]:
    """Accounts with a userdata folder, the most recently signed-in one first."""
    users: dict[str, dict] = {}
    try:
        doc = VdfDocument.from_file(install.loginusers_vdf)
        block = doc.find(["users"])
        for node in (block.children if block and block.is_block else []):
            if node.is_block and node.key.isdigit():
                users[node.key] = node.to_dict()
    except (OSError, VdfError):
        pass

    by_account: dict[str, tuple[str, dict]] = {}
    for sid, info in users.items():
        aid = str(int(sid) - STEAMID64_BASE) if int(sid) > STEAMID64_BASE else sid
        by_account[aid] = (sid, info)

    found: list[Account] = []
    userdata = install.root / "userdata"
    try:
        dirs = sorted(p for p in userdata.iterdir() if p.is_dir() and p.name.isdigit() and p.name != "0")
    except OSError:
        dirs = []
    for d in dirs:
        lc = d / "config" / "localconfig.vdf"
        if not lc.is_file():
            continue
        sid, info = by_account.get(d.name, (None, {}))
        lowered = {k.lower(): v for k, v in info.items() if isinstance(v, str)}
        found.append(
            Account(
                account_id=d.name,
                steamid64=sid,
                persona=lowered.get("personaname", ""),
                account_name=lowered.get("accountname", ""),
                most_recent=lowered.get("mostrecent", "0") == "1",
                localconfig=lc,
            )
        )

    def rank(a: Account):
        info = by_account.get(a.account_id, (None, {}))[1]
        lowered = {k.lower(): v for k, v in info.items() if isinstance(v, str)}
        ts_raw = lowered.get("timestamp", "0")
        ts = int(ts_raw) if ts_raw.isdigit() else 0
        try:
            mtime = a.localconfig.stat().st_mtime
        except OSError:
            mtime = 0
        return (a.most_recent, ts, mtime)

    return sorted(found, key=rank, reverse=True)
