import os

import pytest
import vdf as vdf_oracle
from fakesteam import ACCOUNT_ID, FakeSteam, make_proc

from autoproton import hardware
from autoproton.models import CompatSetting, GameSettings
from autoproton.steam import compat, config_store, library, locate, process


def test_finds_install_once_through_symlink(ctx, steam):
    installs = locate.find_installs(ctx)
    assert len(installs) == 1
    assert installs[0].root == steam.root.resolve()
    assert installs[0].flavor == "native"


def test_flatpak_install_detected(ctx, home):
    fs = FakeSteam(home, flavor="flatpak")
    (inst,) = locate.find_installs(ctx)
    assert inst.flavor == "flatpak"
    assert inst.root == fs.root.resolve()


def test_root_override(ctx, tmp_path, steam):
    other = FakeSteam(tmp_path / "otherhome")
    ctx.steam_root_override = other.root
    (inst,) = locate.find_installs(ctx)
    assert inst.root == other.root.resolve()


def test_accounts_most_recent_first(ctx, steam):
    steam.account("222", str(76561197960265728 + 222), "Other", most_recent=False)
    inst = locate.find_installs(ctx)[0]
    accts = locate.accounts(inst)
    assert [a.account_id for a in accts] == [ACCOUNT_ID, "222"]
    assert accts[0].persona == "Kate" and accts[0].most_recent


def test_installed_games_skip_tools_and_cover_all_libraries(ctx, steam, tmp_path):
    lib2 = steam.add_library(tmp_path / "games2")
    steam.add_game(271590, "Grand Theft Auto V", library=lib2)
    steam.add_game(1070560, "Steam Linux Runtime 1.0 (scout)")
    inst = locate.find_installs(ctx)[0]
    names = [g.name for g in library.installed_games(inst)]
    assert names == ["Cyberpunk 2077", "Dota 2", "ELDEN RING", "Grand Theft Auto V"]


def test_old_libraryfolders_format(ctx, steam, tmp_path):
    lib2 = tmp_path / "old"
    (lib2 / "steamapps").mkdir(parents=True)
    (steam.root / "steamapps/libraryfolders.vdf").write_text(
        f'"LibraryFolders"\n{{\n\t"TimeNextStatsReport"\t\t"1"\n\t"1"\t\t"{lib2}"\n}}\n'
    )
    inst = locate.find_installs(ctx)[0]
    assert lib2.resolve() in library.library_paths(inst)


def test_installed_tools(ctx, steam):
    inst = locate.find_installs(ctx)[0]
    tools = {t.internal_name: t for t in compat.installed_tools(ctx, inst)}
    assert set(tools) == {"proton_experimental", "proton_10", "GE-Proton10-25"}
    assert tools["GE-Proton10-25"].ge_version == (10, 25, 0)
    assert tools["proton_10"].kind == "official"


@pytest.mark.parametrize(
    "display,internal",
    [("Proton 10.0", "proton_10"), ("Proton 9.0", "proton_9"), ("Proton 6.3", "proton_63"),
     ("Proton 5.13", "proton_513"), ("Proton 4.11", "proton_411"), ("Proton 5.0", "proton_5"),
     ("Proton Experimental", "proton_experimental"), ("Proton Hotfix", "proton_hotfix"), ("Proton EasyAntiCheat Runtime", None)],
)
def test_official_internal_names(display, internal):
    assert compat.official_name_from_display(display) == internal
    if internal:
        assert compat.display_name(internal) == display


def test_ge_versions():
    assert compat.ge_version("GE-Proton9-27") == (9, 27, 0)
    assert compat.ge_version("Proton-6.21-GE-2") == (6, 21, 2)
    assert compat.ge_version("proton_9") is None


def test_read_settings(steam):
    s = config_store.read_settings(steam.localconfig.read_bytes(), steam.config.read_bytes(), 1245620)
    assert s == GameSettings(None, CompatSetting("proton_9", "", "250"))
    s = config_store.read_settings(steam.localconfig.read_bytes(), steam.config.read_bytes(), 1091500)
    assert s.launch_options == 'WINEDLLOVERRIDES="winmm,version=n,b" %command%'
    assert s.compat_tool is None


def test_build_changes_only_touches_what_changed(steam):
    local, cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    after = GameSettings("%command% --launcher-skip", CompatSetting("GE-Proton10-25"))
    new_local, new_cfg = config_store.build_changes(local, cfg, 1091500, after)
    assert config_store.read_settings(new_local, new_cfg, 1091500) == after
    ref = vdf_oracle.loads(new_cfg.decode())
    assert ref["InstallConfigStore"]["Software"]["Valve"]["Steam"]["CompatToolMapping"]["1091500"] == {
        "name": "GE-Proton10-25", "config": "", "priority": "250"}
    # only launch options changed in localconfig: same number of lines
    assert len(new_local.splitlines()) == len(local.splitlines())
    # nothing to do -> no new bytes
    assert config_store.build_changes(new_local, new_cfg, 1091500, after) == (None, None)


def test_build_changes_can_remove_entries(steam):
    local, cfg = steam.localconfig.read_bytes(), steam.config.read_bytes()
    new_local, new_cfg = config_store.build_changes(local, cfg, 1245620, GameSettings(None, None))
    assert new_local is None
    assert b'"1245620"' not in new_cfg
    new_local, _ = config_store.build_changes(local, cfg, 1091500, GameSettings(None, None))
    assert config_store.read_settings(new_local, cfg, 1091500).launch_options is None
    assert b'"LastPlayed"\t\t"1700000000"' in new_local


def test_control_characters_rejected(steam):
    with pytest.raises(config_store.InvalidSetting):
        config_store.build_changes(
            steam.localconfig.read_bytes(), steam.config.read_bytes(), 570, GameSettings("a\nb", None)
        )


def test_steam_running_detection(ctx):
    assert not process.is_running(ctx)
    make_proc(ctx.sys_root, 4242, "bash")
    assert not process.is_running(ctx)
    make_proc(ctx.sys_root, 4343, "steamwebhelper")
    assert process.steam_pids(ctx) == [4343]


def test_zombie_steam_is_not_running(ctx):
    d = make_proc(ctx.sys_root, 4444, "steam")
    (d / "stat").write_text("4444 (steam) Z 1 4444 4444 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 1 0 0\n")
    assert not process.is_running(ctx)
    (d / "stat").write_text("4444 (steam) S 1 4444 4444 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 1 0 0\n")
    assert process.is_running(ctx)


def test_default_spawn_reaps_children(tmp_path):
    import time

    from autoproton.context import default_spawn

    script = tmp_path / "steam"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    proc = default_spawn([str(script), "-shutdown"], {"PATH": "/usr/bin:/bin"})
    for _ in range(50):
        if not os.path.exists(f"/proc/{proc.pid}"):
            break
        time.sleep(0.05)
    assert not os.path.exists(f"/proc/{proc.pid}"), "finished child was left as a zombie"


def test_own_process_is_ignored(ctx):
    make_proc(ctx.sys_root, ctx.own_pid, "steam")
    assert not process.is_running(ctx)


def test_shutdown_commands_use_clean_env(ctx, steam):
    ctx.env = {**ctx.env, "LD_LIBRARY_PATH": "/tmp/_MEI/lib", "LD_LIBRARY_PATH_ORIG": "/usr/lib/x",
               "QT_PLUGIN_PATH": "/tmp/_MEI/plugins", "_PYI_APPLICATION_HOME_DIR": "/tmp/_MEI"}
    ctx.which = lambda n: "/usr/bin/steam" if n == "steam" else None
    inst = locate.find_installs(ctx)[0]
    process.shutdown(ctx, inst)
    argv, env = ctx.spawned[-1]
    assert argv == ["/usr/bin/steam", "-shutdown"]
    assert env["LD_LIBRARY_PATH"] == "/usr/lib/x"
    assert "QT_PLUGIN_PATH" not in env and "_PYI_APPLICATION_HOME_DIR" not in env


def test_clean_env_drops_bundle_library_path_without_orig():
    env = process.clean_env({"LD_LIBRARY_PATH": "/tmp/.mount_x/usr/lib", "APPIMAGE": "/a.AppImage", "HOME": "/h",
                             "_PYI_LINUX_PROCESS_NAME": "autoproton"})
    assert env == {"HOME": "/h"}
    # outside a bundle, the user's own LD_LIBRARY_PATH is kept
    assert process.clean_env({"LD_LIBRARY_PATH": "/opt/x"}) == {"LD_LIBRARY_PATH": "/opt/x"}


def test_flatpak_steam_command(ctx, home):
    FakeSteam(home, flavor="flatpak")
    inst = locate.find_installs(ctx)[0]
    process.launch(ctx, inst, "steam://rungameid/570")
    assert ctx.spawned[-1][0] == ["flatpak", "run", "com.valvesoftware.Steam", "steam://rungameid/570"]


def test_wait_for_exit(ctx):
    d = make_proc(ctx.sys_root, 5000, "steam")
    t = [0.0]
    ctx.clock = lambda: t[0]

    def sleep(s):
        t[0] += s
        if t[0] >= 2:
            (d / "comm").unlink(missing_ok=True)

    ctx.sleep = sleep
    assert process.wait_for_exit(ctx, timeout=10)
    make_proc(ctx.sys_root, 5001, "steam")
    assert not process.wait_for_exit(ctx, timeout=3)


def test_game_mode_detection():
    assert process.in_game_mode({"XDG_CURRENT_DESKTOP": "gamescope"})
    assert not process.in_game_mode({"XDG_CURRENT_DESKTOP": "KDE"})


def _sys(ctx, vendor_ids, board="", product=""):
    dmi = ctx.sys_root / "sys/devices/virtual/dmi/id"
    dmi.mkdir(parents=True, exist_ok=True)
    (dmi / "board_vendor").write_text(board + "\n")
    (dmi / "product_name").write_text(product + "\n")
    for i, vid in enumerate(vendor_ids):
        dev = ctx.sys_root / f"sys/class/drm/card{i}/device"
        dev.mkdir(parents=True, exist_ok=True)
        (dev / "vendor").write_text(vid + "\n")
        (ctx.sys_root / f"sys/class/drm/card{i}-DP-1").mkdir(exist_ok=True)


def test_hardware_hybrid_laptop(ctx):
    _sys(ctx, ["0x8086", "0x10de"])
    hw = hardware.detect(ctx)
    assert not hw.is_deck and hw.primary_vendor == "nvidia" and hw.gpu_vendors == {"intel", "nvidia"}


def test_hardware_deck(ctx):
    _sys(ctx, ["0x1002"], board="Valve", product="Galileo")
    hw = hardware.detect(ctx)
    assert hw.is_deck and hw.hw_class == "deck" and hw.primary_vendor == "amd"


def test_hardware_override(ctx):
    _sys(ctx, ["0x1002"])
    ctx.env = {**ctx.env, "AUTOPROTON_DEVICE": "deck"}
    assert hardware.detect(ctx).is_deck
