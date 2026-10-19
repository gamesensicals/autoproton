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

# Tabs are fine (Steam stores them escaped, and its own text box allows them);
# other control characters have no business in a launch command.
_CONTROL = ({chr(c) for c in range(32)} - {"\t"}) | {"\x7f"}


class InvalidSetting(ValueError):
    pass


def read_launch_options(doc: VdfDocument, appid: int) -> str | None:
    return doc.get((*APPS_PATH, str(appid), "LaunchOptions"))


def read_compat(doc: VdfDocument, appid: int) -> CompatSetting | None:
    node = doc.first((*COMPAT_PATH, str(appid)))
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


def read_all_settings(localconfig: bytes, config: bytes, appids) -> dict[int, GameSettings]:
    """Settings for many games from a single parse of each file."""
    local_doc, cfg_doc = VdfDocument.from_bytes(localconfig), VdfDocument.from_bytes(config)
    return {a: GameSettings(read_launch_options(local_doc, a), read_compat(cfg_doc, a)) for a in appids}


def validate_launch_options(value: str) -> None:
    bad = sorted({repr(c) for c in value if c in _CONTROL})
    if bad:
        raise InvalidSetting(f"launch options contain control characters: {', '.join(bad)}")


# Blocks a change can create, recorded in its history entry so undo removes
# exactly those and never a block that was already in the file.
APP_BLOCK = "app_block"  # apps/<appid> in localconfig.vdf
COMPAT_BLOCK = "compat_block"  # CompatToolMapping in config.vdf


def edit_launch_options(doc: VdfDocument, appid: int, value: str | None, check: bool = True,
                        prune: frozenset[str] = frozenset()) -> None:
    """Set or remove a game's launch options.

    ``check`` validates new text. Undo turns it off: it only ever writes back
    what was read from Steam, which must go back exactly as it was. ``prune``
    names blocks the undone change created, to remove once they're empty.
    """
    path = (*APPS_PATH, str(appid), "LaunchOptions")
    if value is None:
        doc.delete(path)
        if APP_BLOCK in prune:
            doc.delete_if_empty((*APPS_PATH, str(appid)))
    else:
        if check:
            validate_launch_options(value)
        doc.set(path, value)


def edit_compat(doc: VdfDocument, appid: int, value: CompatSetting | None, check: bool = True,
                prune: frozenset[str] = frozenset()) -> None:
    path = (*COMPAT_PATH, str(appid))
    if value is None:
        doc.delete(path)
        if COMPAT_BLOCK in prune:
            doc.delete_if_empty(COMPAT_PATH)
        return
    for field_name, field_value in (("name", value.name), ("config", value.config), ("priority", value.priority)):
        if check and any(c in _CONTROL for c in field_value):
            raise InvalidSetting(f"compat tool {field_name} contains control characters")
        doc.set((*path, field_name), field_value)


def created_blocks(old_local: bytes, new_local: bytes | None, old_cfg: bytes, new_cfg: bytes | None,
                   appid: int) -> list[str]:
    """Which of APP_BLOCK / COMPAT_BLOCK a change adds to the files."""
    out = []
    if new_local is not None:
        path = (*APPS_PATH, str(appid))
        if VdfDocument.from_bytes(old_local).first(path) is None and VdfDocument.from_bytes(new_local).first(path):
            out.append(APP_BLOCK)
    if new_cfg is not None:
        if VdfDocument.from_bytes(old_cfg).first(COMPAT_PATH) is None and VdfDocument.from_bytes(new_cfg).first(COMPAT_PATH):
            out.append(COMPAT_BLOCK)
    return out


def build_changes(
    localconfig: bytes, config: bytes, appid: int, after: GameSettings, current: GameSettings | None = None,
    check: bool = True, prune: frozenset[str] = frozenset(),
) -> tuple[bytes | None, bytes | None]:
    """New file contents that give ``appid`` the settings in ``after``.

    Returns None for a file that doesn't need to change. Each edited file is
    re-parsed and checked to hold exactly the new value before it's returned.
    """
    if current is None:
        current = read_settings(localconfig, config, appid)
    new_local = new_config = None
    if current.launch_options != after.launch_options:
        doc = VdfDocument.from_bytes(localconfig)
        edit_launch_options(doc, appid, after.launch_options, check, prune)
        if read_launch_options(doc, appid) != after.launch_options:
            raise InvalidSetting("the edited localconfig.vdf didn't read back as intended")
        new_local = doc.to_bytes()
    if current.compat_tool != after.compat_tool:
        doc = VdfDocument.from_bytes(config)
        edit_compat(doc, appid, after.compat_tool, check, prune)
        if read_compat(doc, appid) != after.compat_tool:
            raise InvalidSetting("the edited config.vdf didn't read back as intended")
        new_config = doc.to_bytes()
    return new_local, new_config
