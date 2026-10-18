"""Plain data types shared by the core, the CLI and the GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SteamInstall:
    root: Path  # real path, symlinks resolved
    flavor: str  # "native" | "flatpak" | "snap"

    @property
    def config_vdf(self) -> Path:
        return self.root / "config" / "config.vdf"

    @property
    def loginusers_vdf(self) -> Path:
        return self.root / "config" / "loginusers.vdf"

    @property
    def compat_dir(self) -> Path:
        return self.root / "compatibilitytools.d"

    @property
    def label(self) -> str:
        names = {"native": "Steam", "flatpak": "Steam (Flatpak)", "snap": "Steam (Snap)"}
        return f"{names.get(self.flavor, 'Steam')} — {self.root}"


@dataclass(frozen=True)
class Account:
    account_id: str  # the folder name under userdata/
    steamid64: str | None
    persona: str
    account_name: str
    most_recent: bool
    localconfig: Path

    @property
    def label(self) -> str:
        name = self.persona or self.account_name or self.account_id
        return f"{name} ({self.account_id})"


@dataclass(frozen=True)
class Game:
    appid: int
    name: str
    library: Path
    installdir: str


@dataclass(frozen=True)
class CompatTool:
    internal_name: str  # what goes in config.vdf, e.g. "proton_10" or "GE-Proton10-34"
    display_name: str
    kind: str  # "official" | "custom"
    path: Path | None = None
    ge_version: tuple[int, int, int] | None = None

    @property
    def is_ge(self) -> bool:
        return self.ge_version is not None


@dataclass(frozen=True)
class CompatSetting:
    """One CompatToolMapping entry."""

    name: str
    config: str = ""
    priority: str = "250"

    def to_json(self) -> dict:
        return {"name": self.name, "config": self.config, "priority": self.priority}

    @classmethod
    def from_json(cls, d: dict | None) -> "CompatSetting | None":
        if not d:
            return None
        return cls(str(d.get("name", "")), str(d.get("config", "")), str(d.get("priority", "250")))


@dataclass(frozen=True)
class GameSettings:
    """The two things autoproton changes for a game. None means "no entry in Steam"."""

    launch_options: str | None = None
    compat_tool: CompatSetting | None = None

    def to_json(self) -> dict:
        return {
            "launch_options": self.launch_options,
            "compat_tool": self.compat_tool.to_json() if self.compat_tool else None,
        }

    @classmethod
    def from_json(cls, d: dict) -> "GameSettings":
        return cls(d.get("launch_options"), CompatSetting.from_json(d.get("compat_tool")))

    def describe_tool(self) -> str:
        return self.compat_tool.name if self.compat_tool else "Steam default"

    def describe_options(self) -> str:
        return self.launch_options if self.launch_options else "(none)"


@dataclass(frozen=True)
class HardwareProfile:
    is_deck: bool
    gpu_vendors: frozenset[str] = field(default_factory=frozenset)
    primary_vendor: str = "unknown"  # "nvidia" | "amd" | "intel" | "unknown"

    @property
    def hw_class(self) -> str:
        return "deck" if self.is_deck else "pc"

    def describe(self) -> str:
        if self.is_deck:
            return "Steam Deck"
        names = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}
        return f"PC ({names.get(self.primary_vendor, 'unknown')} GPU)"
