"""Read and change a game's launch options and compatibility tool.

- Launch options live per account in
  ``userdata/<account>/config/localconfig.vdf`` at
  ``UserLocalConfigStore/Software/Valve/Steam/apps/<appid>/LaunchOptions``.
- The forced compatibility tool lives per install in ``config/config.vdf`` at
  ``InstallConfigStore/Software/Valve/Steam/CompatToolMapping/<appid>``.

Everything here works on bytes in, bytes out, so it can be checked before
anything touches the disk.
"""

from __future__ import annotations

from ..models import CompatSetting, GameSettings
from .vdftext import VdfDocument

APPS_PATH = ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps")
COMPAT_PATH = ("InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping")

_CONTROL = {chr(c) for c in range(32)} | {"\x7f"}


class InvalidSetting(ValueError):
    pass


def read_launch_options(doc: VdfDocument, appid: int) -> str | None:
    return doc.get((*APPS_PATH, str(appid), "LaunchOptions"))


def read_compat(doc: VdfDocument, appid: int) -> CompatSetting | None:
    node = doc.find((*COMPAT_PATH, str(appid)))
    if node is None or not node.is_block:
        return None
    name = node.get("name") or ""
    if not name:
        return None
    return CompatSetting(name, node.get("config") or "", node.get("priority") or "250")


def read_settings(localconfig: bytes, config: bytes, appid: int) -> GameSettings:
    return GameSettings(
        read_launch_options(VdfDocument.from_bytes(localconfig), appid),
        read_compat(VdfDocument.from_bytes(config), appid),
    )


def validate_launch_options(value: str) -> None:
    bad = sorted({repr(c) for c in value if c in _CONTROL})
    if bad:
        raise InvalidSetting(f"launch options contain control characters: {', '.join(bad)}")


def edit_launch_options(doc: VdfDocument, appid: int, value: str | None) -> None:
    path = (*APPS_PATH, str(appid), "LaunchOptions")
    if value is None:
        doc.delete(path)
    else:
        validate_launch_options(value)
        doc.set(path, value)


def edit_compat(doc: VdfDocument, appid: int, value: CompatSetting | None) -> None:
    path = (*COMPAT_PATH, str(appid))
    if value is None:
        doc.delete(path)
        return
    for field_name, field_value in (("name", value.name), ("config", value.config), ("priority", value.priority)):
        if any(c in _CONTROL for c in field_value):
            raise InvalidSetting(f"compat tool {field_name} contains control characters")
        doc.set((*path, field_name), field_value)


def build_changes(
    localconfig: bytes, config: bytes, appid: int, after: GameSettings
) -> tuple[bytes | None, bytes | None]:
    """New file contents that give ``appid`` the settings in ``after``.

    Returns None for a file that doesn't need to change.
    """
    current = read_settings(localconfig, config, appid)
    new_local = new_config = None
    if current.launch_options != after.launch_options:
        doc = VdfDocument.from_bytes(localconfig)
        edit_launch_options(doc, appid, after.launch_options)
        new_local = doc.to_bytes()
    if current.compat_tool != after.compat_tool:
        doc = VdfDocument.from_bytes(config)
        edit_compat(doc, appid, after.compat_tool)
        new_config = doc.to_bytes()
    return new_local, new_config
