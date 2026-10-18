import io
import subprocess
import sys

from fakenet import FakeNet
from fakesteam import make_proc
from test_protondb import COUNTS, load_game, reports_url

from autoproton import cli
from autoproton.core import Core
from autoproton.protondb import client as pdb


def core_for(ctx):
    g = load_game(1091500)
    ctx.net = FakeNet({
        f"{pdb.BASE}/data/counts.json": COUNTS,
        reports_url(1091500): {"reports": g["reports"], "total": len(g["reports"])},
        f"{pdb.BASE}/api/v1/reports/summaries/1091500.json": g["summary"],
    })
    ctx.clock = lambda: g["recorded"]
    return Core(ctx)


def run(core, *argv):
    out = io.StringIO()
    code = cli.main(list(argv), core=core, out=out)
    return code, out.getvalue()


def test_status_and_games(ctx, steam):
    core = core_for(ctx)
    code, out = run(core, "status", "1245620")
    assert code == 0 and "Kate" in out and "proton_9" in out
    code, out = run(core, "games")
    assert "Cyberpunk 2077" in out and "Proton" not in out.split("Cyberpunk")[0]


def test_recommend_changes_nothing(ctx, steam):
    core = core_for(ctx)
    before = steam.localconfig.read_bytes(), steam.config.read_bytes()
    code, out = run(core, "recommend", "1091500")
    assert code == 0 and "Proton 10.0" in out and "[x] --launcher-skip" in out
    assert (steam.localconfig.read_bytes(), steam.config.read_bytes()) == before


def test_apply_history_revert_round_trip(ctx, steam):
    core = core_for(ctx)
    original = steam.localconfig.read_bytes(), steam.config.read_bytes()
    code, out = run(core, "apply", "1091500", "--yes")
    assert code == 0, out
    s = core.settings(core.default_install(), core.accounts(core.default_install())[0], 1091500)
    assert s.compat_tool.name == "proton_10"
    # the user's own text is kept, the recommended options are added after %command%
    assert s.launch_options.startswith('WINEDLLOVERRIDES="winmm,version=n,b" %command% --launcher-skip')
    code, out = run(core, "history", "1091500")
    assert "apply" in out
    entry_id = out.split()[0]
    assert run(core, "mark", entry_id, "works")[0] == 0
    code, out = run(core, "revert", "1091500", "--yes")
    assert code == 0, out
    assert (steam.localconfig.read_bytes(), steam.config.read_bytes()) == original
    # and back to the version marked as working
    code, out = run(core, "revert", "1091500", "--last-good", "--yes")
    assert code == 0
    assert core.settings(core.default_install(), core.accounts(core.default_install())[0], 1091500).compat_tool.name == "proton_10"


def test_apply_refuses_while_steam_runs(ctx, steam, capsys):
    core = core_for(ctx)
    make_proc(ctx.sys_root, 999, "steam")
    code, _ = run(core, "apply", "1091500", "--yes")
    assert code == 2 and "Steam is running" in capsys.readouterr().err


def test_apply_can_close_steam(ctx, steam):
    core = core_for(ctx)
    proc = make_proc(ctx.sys_root, 999, "steam")
    ctx.which = lambda n: "/usr/bin/steam" if n == "steam" else None

    def spawn(argv, env):
        ctx.spawned.append(argv)
        if "-shutdown" in argv:
            (proc / "comm").unlink()

    ctx.spawn = spawn
    code, out = run(core, "apply", "1091500", "--yes", "--close-steam")
    assert code == 0, out
    assert ["/usr/bin/steam", "-shutdown"] in ctx.spawned


def test_backups_and_restore_file(ctx, steam):
    core = core_for(ctx)
    original = steam.localconfig.read_bytes()
    run(core, "apply", "1091500", "--yes")
    code, out = run(core, "backups")
    set_id = next(line.split()[0] for line in out.splitlines() if "apply" in line)
    code, out = run(core, "restore-file", set_id, "--file", "localconfig", "--yes")
    assert code == 0, out
    assert steam.localconfig.read_bytes() == original


def test_errors_are_friendly(ctx, home, capsys):
    core = core_for(ctx)
    code, _ = run(core, "status")
    assert code == 2 and "Couldn't find Steam" in capsys.readouterr().err


def test_cli_does_not_import_qt(tmp_path):
    code = ("import sys; from autoproton import cli; cli.build_parser(); "
            "import autoproton.core; assert not any(m.startswith('PySide6') for m in sys.modules), 'Qt imported'")
    subprocess.run([sys.executable, "-c", code], check=True)
