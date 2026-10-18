import pytest

from autoproton.recommend.launchopts import Component, assemble, merge, parse


def texts(p):
    return [c.text for c in p.components]


@pytest.mark.parametrize("raw,expected", [
    ("--launcher-skip", ["--launcher-skip"]),
    ("%command% --launcher-skip -skipStartScreen", ["--launcher-skip", "-skipStartScreen"]),
    ("gamemoderun %command%", ["gamemoderun"]),
    ('WINEDLLOVERRIDES="winmm,version=n,b" %command% -modded', ["WINEDLLOVERRIDES=version,winmm=n,b", "-modded"]),
    ("PROTON_FSR4_UPGRADE=1", ["PROTON_FSR4_UPGRADE=1"]),
    ("gamemoderun PROTON_ENABLE_WAYLAND=1 %command%", ["PROTON_ENABLE_WAYLAND=1", "gamemoderun"]),
    ("%command% -sdlaudiodriver pulse -novid", ["-sdlaudiodriver pulse", "-novid"]),
    ("env DXVK_ASYNC=1 %command%", ["DXVK_ASYNC=1"]),
    ("gamescope -W 2560 -H 1440 -f -- %command%", ["gamescope -W 2560 -H 1440 -f --"]),
    ("none", []),
    ("%command%", []),
    ("/usr/bin/gamemoderun %command%", ["gamemoderun"]),
])
def test_parse_report_strings(raw, expected):
    p = parse(raw)
    assert sorted(texts(p)) == sorted(expected)


@pytest.mark.parametrize("raw", [
    "falcon %command% --launcher-skip",       # unknown program
    "%command%; rm -rf ~",                    # shell syntax
    "LD_PRELOAD=/tmp/x.so %command%",         # changes how programs load
    "WINEPREFIX=/home/me/pfx %command%",      # someone else's path
    "DXVK_CONFIG_FILE=/home/me/dxvk.conf %command%",
    "FOO=$(curl evil) %command%",
    "%command% -config /home/me/x.cfg",
    "bash -c 'echo hi' %command%",
    "%command% `id`",
])
def test_unsafe_pieces_are_dropped(raw):
    p = parse(raw)
    bad = {"falcon", "rm", "LD_PRELOAD", "WINEPREFIX", "DXVK_CONFIG_FILE", "FOO", "bash", "-config", "id"}
    assert not any(c.key in bad or "/" in (c.value or "") for c in p.components)
    assert p.rejected or not p.ok


def test_unbalanced_quotes_are_unparseable():
    assert not parse('WINEDLLOVERRIDES="winmm %command%').ok


def test_assemble_orders_and_quotes():
    comps = [Component("arg", "--launcher-skip"), Component("wrapper", "gamemoderun"),
             Component("env", "WINEDLLOVERRIDES", "dinput8=n,b;xinput1_3=n"), Component("env", "PROTON_LOG", "1")]
    assert assemble(comps) == 'WINEDLLOVERRIDES="dinput8=n,b;xinput1_3=n" PROTON_LOG=1 gamemoderun %command% --launcher-skip'
    assert assemble([]) == ""
    assert assemble([Component("wrapper", "gamescope", "-f --", ("-f", "--")), Component("wrapper", "mangohud")]) == \
        "mangohud gamescope -f -- %command%"


def test_merge_keeps_user_text():
    add = [Component("env", "PROTON_USE_NTSYNC", "1"), Component("wrapper", "gamemoderun"),
           Component("arg", "--launcher-skip")]
    assert merge('MY_VAR="a b" mangohud %command% -windowed', add) == \
        'PROTON_USE_NTSYNC=1 MY_VAR="a b" mangohud gamemoderun %command% --launcher-skip -windowed'
    # already present: nothing added, user's value kept
    assert merge("PROTON_USE_NTSYNC=0 %command% --launcher-skip", add[::2]) == "PROTON_USE_NTSYNC=0 %command% --launcher-skip"
    # no %command%: user's words stay after the game command
    assert merge("-novid", add) == "PROTON_USE_NTSYNC=1 gamemoderun %command% --launcher-skip -novid"
    assert merge("-novid", [Component("arg", "-high")]) == "-novid -high"
    assert merge(None, add) == assemble(add)


@pytest.mark.parametrize("raw", [
    "gamescope -W 2560 -H 1440 -f /home/bob/bin/run.sh %command%",   # program smuggled after gamescope
    "gamescope -f rm %command%",
    "gamescope -W /etc/passwd -- %command%",                          # value that isn't a plain value
    "MANGOHUD_CONFIG=fps_limit=60,exec=poweroff mangohud %command%",  # MangoHud runs exec= entries
    "GAMEMODERUNEXEC=rm gamemoderun %command%",                       # gamemoderun runs this in front of the game
    "SOMETOOL_EXEC=rm %command%",
    "PROTON_USE_WINED3D=1%command%",                                  # a second %command% glued on
    "%command% -x%command%",
])
def test_command_smuggling_is_rejected(raw):
    p = parse(raw)
    text = " ".join(c.text for c in p.components)
    for bad in ("/home/bob", "rm", "poweroff", "/etc/passwd", "%command%"):
        assert bad not in text.split(), (bad, text)
    assert "exec=" not in text and "EXEC" not in text
    assert p.rejected


def test_gamescope_values_and_following_wrappers_still_work():
    p = parse("gamescope -W 2560 -H 1440 -f --fsr-sharpness 5 gamemoderun %command%")
    assert [c.text for c in p.wrappers] == ["gamescope -W 2560 -H 1440 -f --fsr-sharpness 5 --", "gamemoderun"]
    assert p.rejected == []
    assert assemble(p.wrappers) == "gamemoderun gamescope -W 2560 -H 1440 -f --fsr-sharpness 5 -- %command%"


def test_dll_override_disable_entries_keep_their_equals_sign():
    assert parse('WINEDLLOVERRIDES="mscoree,mshtml=" %command%').env[0].value == "mscoree,mshtml="
    assert parse("WINEDLLOVERRIDES=d3d12=;dxgi=n %command%").env[0].value == "d3d12=;dxgi=n"
    assert parse("WINEDLLOVERRIDES=dinput8 %command%").env[0].value == "dinput8"


def test_pieces_without_command_are_not_counted_as_active():
    # Steam passes text without %command% to the game as arguments, so this variable does nothing
    env = Component("env", "PROTON_USE_WINED3D", "1")
    assert merge("PROTON_USE_WINED3D=1", [env]) == "PROTON_USE_WINED3D=1 %command% PROTON_USE_WINED3D=1"
    assert merge("gamemoderun", [Component("wrapper", "gamemoderun")]) == "gamemoderun %command% gamemoderun"


def test_merge_respects_shell_operators_in_the_users_text():
    add = [Component("env", "PROTON_NO_ESYNC", "1"), Component("wrapper", "gamemoderun"), Component("arg", "--launcher-skip")]
    assert merge("%command% 2>&1 | tee /tmp/game.log", add) == \
        "PROTON_NO_ESYNC=1 gamemoderun %command% --launcher-skip 2>&1 | tee /tmp/game.log"
    assert merge("%command% # windowed", add[2:]) == "%command% --launcher-skip # windowed"
    assert merge('cd "$HOME/mods" && %command%', add) == \
        'cd "$HOME/mods" && PROTON_NO_ESYNC=1 gamemoderun %command% --launcher-skip'
    assert merge('X="a;b" %command%', add[:1]) == 'PROTON_NO_ESYNC=1 X="a;b" %command%'
