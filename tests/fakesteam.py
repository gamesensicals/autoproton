"""Builds a realistic Steam folder tree in a temp directory for tests."""

from __future__ import annotations

import os
from pathlib import Path

ACCOUNT_ID = "12345678"
STEAMID64 = str(76561197960265728 + int(ACCOUNT_ID))

LOCALCONFIG = """"UserLocalConfigStore"
{
	"Broadcast"
	{
		"Permissions"		"1"
	}
	"friends"
	{
		"PersonaName"		"Kate's PC? (main)"
		"999"
		{
			"name"		"Bob \\"the\\" builder"
			"tag"		"a\\\\b"
		}
	}
	"Software"
	{
		"Valve"
		{
			"Steam"
			{
				"apps"
				{
					"1091500"
					{
						"LastPlayed"		"1700000000"
						"LaunchOptions"		"WINEDLLOVERRIDES=\\"winmm,version=n,b\\" %command%"
					}
					"570"
					{
						"LastPlayed"		"1700000001"
						"Playtime"		"42"
					}
				}
			}
		}
	}
	"WebStorage"
	{
		"key?"		"it's 'quoted'?"
	}
}
"""

CONFIG = """"InstallConfigStore"
{
	"Software"
	{
		"Valve"
		{
			"Steam"
			{
				"AutoUpdateWindowEnabled"		"0"
				"CompatToolMapping"
				{
					"0"
					{
						"name"		"proton_experimental"
						"config"		""
						"priority"		"75"
					}
					"1245620"
					{
						"name"		"proton_9"
						"config"		""
						"priority"		"250"
					}
				}
			}
		}
	}
	"Music"
	{
		"CrawlSteamInstallFolders"		"1"
	}
}
"""

GE_VDF = """"compatibilitytools"
{
  "compat_tools"
  {
    "{name}" // Internal name of this tool
    {
      "install_path" "."
      "display_name" "{name}"
      "from_oslist"  "windows"
      "to_oslist"    "linux"
    }
  }
}
"""


class FakeSteam:
    def __init__(self, home: Path, flavor: str = "native"):
        self.home = Path(home)
        if flavor == "flatpak":
            self.root = self.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
        else:
            self.root = self.home / ".local/share/Steam"
        (self.root / "steamapps/common").mkdir(parents=True, exist_ok=True)
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        if flavor == "native":
            (self.home / ".steam").mkdir(parents=True, exist_ok=True)
            link = self.home / ".steam/steam"
            if not link.exists():
                os.symlink(self.root, link)
        self.libraries = [self.root]
        self.write_libraryfolders()
        (self.root / "config/config.vdf").write_text(CONFIG)
        self.account(ACCOUNT_ID, STEAMID64, "Kate", most_recent=True)

    # ---- builders ---------------------------------------------------------

    def write_libraryfolders(self):
        parts = ['"libraryfolders"\n{\n']
        for i, lib in enumerate(self.libraries):
            parts.append(f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{lib}"\n\t\t"label"\t\t""\n\t}}\n')
        parts.append("}\n")
        (self.root / "steamapps/libraryfolders.vdf").write_text("".join(parts))

    def add_library(self, path: Path) -> Path:
        (Path(path) / "steamapps/common").mkdir(parents=True, exist_ok=True)
        self.libraries.append(Path(path))
        self.write_libraryfolders()
        return Path(path)

    def account(self, account_id, steamid64, persona, most_recent=False, localconfig=LOCALCONFIG):
        cfg = self.root / "userdata" / account_id / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "localconfig.vdf").write_text(localconfig)
        users = self.root / "config/loginusers.vdf"
        existing = users.read_text() if users.exists() else '"users"\n{\n}\n'
        entry = (
            f'\t"{steamid64}"\n\t{{\n\t\t"AccountName"\t\t"{persona.lower()}"\n'
            f'\t\t"PersonaName"\t\t"{persona}"\n\t\t"MostRecent"\t\t"{1 if most_recent else 0}"\n'
            f'\t\t"Timestamp"\t\t"1790000000"\n\t}}\n'
        )
        users.write_text(existing.rstrip().rstrip("}") + entry + "}\n")
        return self

    def add_game(self, appid: int, name: str, library: Path | None = None, tool: bool = False):
        lib = Path(library) if library else self.root
        installdir = name.replace(":", "")
        (lib / "steamapps/common" / installdir).mkdir(parents=True, exist_ok=True)
        if tool:
            (lib / "steamapps/common" / installdir / "toolmanifest.vdf").write_text('"manifest"\n{\n}\n')
            (lib / "steamapps/common" / installdir / "proton").write_text("#!/bin/sh\n")
        (lib / f"steamapps/appmanifest_{appid}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n'
            f'\t"installdir"\t\t"{installdir}"\n\t"StateFlags"\t\t"4"\n}}\n'
        )
        return self

    def add_custom_tool(self, name: str):
        d = self.root / "compatibilitytools.d" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "compatibilitytool.vdf").write_text(GE_VDF.replace("{name}", name))
        (d / "proton").write_text("#!/bin/sh\n")
        return self

    # ---- accessors --------------------------------------------------------

    @property
    def localconfig(self) -> Path:
        return self.root / "userdata" / ACCOUNT_ID / "config/localconfig.vdf"

    @property
    def config(self) -> Path:
        return self.root / "config/config.vdf"


def make_proc(sys_root: Path, pid: int, comm: str) -> Path:
    d = sys_root / "proc" / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n")
    return d


def make_default_steam(home: Path) -> FakeSteam:
    fs = FakeSteam(home)
    fs.add_game(1091500, "Cyberpunk 2077")
    fs.add_game(570, "Dota 2")
    fs.add_game(1245620, "ELDEN RING")
    fs.add_game(1493710, "Proton Experimental", tool=True)
    fs.add_game(3658110, "Proton 10.0", tool=True)
    fs.add_game(228980, "Steamworks Common Redistributables")
    fs.add_custom_tool("GE-Proton10-25")
    return fs
