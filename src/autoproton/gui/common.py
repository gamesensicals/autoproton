"""Small shared GUI helpers."""

from __future__ import annotations

import time
from importlib import resources

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy

from ..models import CompatSetting, GameSettings
from ..steam.compat import display_name

# ProtonDB's own tier colours
TIER_COLORS = {
    "platinum": "#b4c7dc",
    "gold": "#cfb53b",
    "silver": "#a6a6a6",
    "bronze": "#cd7f32",
    "borked": "#ff4d4d",
    "pending": "#6f6f6f",
}


def tier_color(tier: str | None) -> QColor | None:
    c = TIER_COLORS.get((tier or "").lower())
    return QColor(c) if c else None


def tier_text(tier: str | None) -> str:
    return (tier or "").capitalize() or "—"


def app_icon() -> QIcon:
    try:
        path = resources.files("autoproton") / "resources" / "autoproton.svg"
        return QIcon(str(path))
    except (FileNotFoundError, ModuleNotFoundError):
        return QIcon()


def when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def plain_label(text: str = "", wrap: bool = True, selectable: bool = False) -> QLabel:
    """A label that never interprets its text as HTML (report notes are written by strangers)."""
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(text)
    label.setWordWrap(wrap)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def banner(text: str, kind: str = "warning") -> QLabel:
    colors = {"warning": ("#fff4ce", "#5c4400"), "error": ("#fde7e9", "#7a0010"), "info": ("#e6f2ff", "#0b3d78")}
    bg, fg = colors.get(kind, colors["info"])
    label = plain_label(text)
    label.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; border-radius: 6px; padding: 8px; }}")
    return label


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


FAMILY_NAMES = {"native": "Native", "official": "Proton", "experimental": "Proton Experimental", "ge": "GE-Proton"}


def pretty_tool(setting: CompatSetting | None) -> str:
    """'Proton 9.0' rather than Steam's internal 'proton_9'."""
    return display_name(setting.name) if setting else "Steam default"


def pretty_settings_tool(s: GameSettings) -> str:
    return pretty_tool(s.compat_tool)
