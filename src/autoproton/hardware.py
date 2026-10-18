"""What machine are we on: Steam Deck or PC, and which GPU vendor."""

from __future__ import annotations

from pathlib import Path

from .context import AppContext
from .models import HardwareProfile

VENDOR_IDS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
DECK_PRODUCTS = {"Jupiter", "Galileo"}


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def detect(ctx: AppContext) -> HardwareProfile:
    forced = (ctx.env.get("AUTOPROTON_DEVICE") or "").lower()
    dmi = ctx.sys_root / "sys/devices/virtual/dmi/id"
    is_deck = _read(dmi / "board_vendor") == "Valve" and _read(dmi / "product_name") in DECK_PRODUCTS
    if forced in ("deck", "pc"):
        is_deck = forced == "deck"

    vendors: set[str] = set()
    drm = ctx.sys_root / "sys/class/drm"
    try:
        cards = sorted(p for p in drm.iterdir() if p.name.startswith("card") and "-" not in p.name)
    except OSError:
        cards = []
    for card in cards:
        vendor = VENDOR_IDS.get(_read(card / "device" / "vendor").lower())
        if vendor:
            vendors.add(vendor)

    primary = "unknown"
    for candidate in ("nvidia", "amd", "intel"):
        if candidate in vendors:
            primary = candidate
            break
    if is_deck:
        primary, vendors = "amd", vendors | {"amd"}
    forced_gpu = (ctx.env.get("AUTOPROTON_GPU") or "").lower()
    if forced_gpu in ("nvidia", "amd", "intel"):
        primary = forced_gpu
        vendors.add(forced_gpu)
    return HardwareProfile(is_deck, frozenset(vendors), primary)
