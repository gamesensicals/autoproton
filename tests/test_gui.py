import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.gui

from fakenet import FakeNet  # noqa: E402
from fakesteam import make_proc  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from test_ge_proton import TAG, build_tar, release_json  # noqa: E402
from test_protondb import COUNTS, load_game, reports_url  # noqa: E402

from autoproton import ge_proton  # noqa: E402
from autoproton.core import Core  # noqa: E402
from autoproton.gui import apply_dialog as ad  # noqa: E402
from autoproton.gui.history_dialog import HistoryDialog  # noqa: E402
from autoproton.gui.main_window import MainWindow  # noqa: E402
from autoproton.gui.workers import Runner, SyncRunner  # noqa: E402
from autoproton.protondb import client as pdb  # noqa: E402
from autoproton.steam import config_store  # noqa: E402

GAMES = {1091500: "Cyberpunk 2077", 570: "Dota 2", 1245620: "ELDEN RING", 413150: "Stardew Valley",
         2358720: "Black Myth: Wukong", 1174180: "Red Dead Redemption 2"}
SHOTS = os.environ.get("AUTOPROTON_SCREENSHOTS")


def shot(widget, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        widget.grab().save(str(Path(SHOTS) / f"{name}.png"))


@pytest.fixture
def gui_core(ctx, steam):
    routes = {f"{pdb.BASE}/data/counts.json": COUNTS}
    recorded = 0
    for appid in GAMES:
        g = load_game(appid)
        recorded = max(recorded, g["recorded"])
        routes[reports_url(appid)] = {"reports": g["reports"], "total": len(g["reports"])}
        routes[f"{pdb.BASE}/api/v1/reports/summaries/{appid}.json"] = g["summary"]
    for appid, name in GAMES.items():
        if appid not in (1091500, 570, 1245620):
            steam.add_game(appid, name)
    steam.add_game(99999, "Some Tiny Indie Game")
    ctx.net = FakeNet(routes)
    ctx.clock = lambda: recorded
    ctx.env = {**ctx.env, "AUTOPROTON_GPU": "nvidia"}
    ctx.which = lambda n: "/usr/bin/" + n if n in ("gamemoderun", "mangohud", "steam") else None
    return Core(ctx)


@pytest.fixture
def window(qtbot, gui_core):
    w = MainWindow(gui_core, SyncRunner())
    qtbot.addWidget(w)
    w.resize(1280, 820)
    w.show()
    w.load()
    return w


@pytest.fixture
def auto_exec(monkeypatch):
    """Dialogs run their steps right away instead of waiting for clicks."""
    opened = []

    def fake_exec(self):
        opened.append(self)
        if isinstance(self, ad.ApplyDialog):
            self._start()
        return 1

    monkeypatch.setattr(ad.ApplyDialog, "exec", fake_exec)
    return opened


def settings(core, appid):
    inst = core.default_install()
    return core.settings(inst, core.accounts(inst)[0], appid)


def test_lists_games_with_tiers(window):
    m = window.model
    assert m.rowCount() == 7
    assert m.summaries[1091500].tier == "gold"
    assert m.summaries[99999] is None
    elden = m.row_of(1245620)
    assert m.data(m.index(elden, 2)) == "Proton 9.0"  # settings read in the background, from one parse
    assert "%command%" in m.data(m.index(m.row_of(1091500), 3))
    names = [window.proxy.index(r, 0).data() for r in range(window.proxy.rowCount())]
    assert names == sorted(names, key=str.casefold)
    window.search.setText("dota")
    assert window.proxy.rowCount() == 1


def test_detail_shows_recommendation(window):
    window.select_appid(1091500)
    d = window.detail
    assert d.currentWidget() is d.detail
    assert "Proton 10.0" in d.tool_combo.itemText(0) and "recommended" in d.tool_combo.itemText(0)
    assert "--launcher-skip" in d.final.text()
    assert d.final.text().startswith('WINEDLLOVERRIDES="winmm,version=n,b" %command%')  # user's text kept
    assert d.apply_btn.isEnabled()
    shot(window, "main-window")


def test_toggling_options_updates_result(window):
    window.select_appid(1091500)
    d = window.detail
    row = next(i for i, o in enumerate(d._row_options) if o.component.key == "gamemoderun")
    d.opts_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    assert "gamemoderun %command%" in d.final.text()
    d.replace_radio.setChecked(True)
    assert not d.final.text().startswith("WINEDLLOVERRIDES")
    d.final.setText("%command% -custom")
    d.final.textEdited.emit("%command% -custom")
    assert d.chosen_settings().launch_options == "%command% -custom"


def test_apply_writes_steam_files(window, gui_core, auto_exec):
    window.select_appid(1091500)
    window.detail.apply_btn.click()
    (dlg,) = auto_exec
    assert dlg.pages.currentIndex() == 3 and dlg.result_value is not None
    s = settings(gui_core, 1091500)
    assert s.compat_tool.name == "proton_10" and "--launcher-skip" in s.launch_options
    assert window.model.settings[1091500] == s


def test_apply_closes_steam_first(qtbot, window, gui_core, steam):
    proc = make_proc(gui_core.ctx.sys_root, 4242, "steam")

    def spawn(argv, env):
        if "-shutdown" in argv:
            (proc / "comm").unlink()

    gui_core.ctx.spawn = spawn
    window.select_appid(570)
    a = window.detail.analysis
    plan = gui_core.plan(window.install, window.account, a.game, 570, window.detail.chosen_settings())
    dlg = ad.apply_dialog_for_plan(window, gui_core, window.runner, plan)
    qtbot.addWidget(dlg)
    shot(dlg, "apply-review")
    dlg._start()
    assert dlg.pages.currentIndex() == 2  # "Steam is running"
    shot(dlg, "apply-steam-running")
    dlg.steam_buttons[2].click()  # Close Steam for me
    assert dlg.pages.currentIndex() == 3
    assert "-sdlaudiodriver pulse" in settings(gui_core, 570).launch_options


def test_game_mode_hides_close_button(qtbot, window, gui_core):
    make_proc(gui_core.ctx.sys_root, 4243, "steam")
    gui_core.ctx.env = {**gui_core.ctx.env, "XDG_CURRENT_DESKTOP": "gamescope"}
    window.select_appid(570)
    plan = gui_core.plan(window.install, window.account, None, 570, window.detail.chosen_settings())
    dlg = ad.apply_dialog_for_plan(window, gui_core, window.runner, plan)
    qtbot.addWidget(dlg)
    dlg._start()
    assert dlg.pages.currentIndex() == 2 and dlg.steam_buttons[2].isHidden()
    assert "Desktop Mode" in dlg.steam_label.text()


def test_ge_download_flow(qtbot, window, gui_core, steam):
    tar = build_tar()
    gui_core.ctx.net.routes.update({
        ge_proton.LATEST_URL: release_json(len(tar)),
        "https://dl/ge.sha512sum": f"{hashlib.sha512(tar).hexdigest()}  {TAG}.tar.gz".encode(),
        "https://dl/ge.tar.gz": tar,
    })
    window.select_appid(1091500)
    d = window.detail
    ge_index = next(i for i, c in enumerate(d._choices) if c.family == "ge")
    # pretend GE isn't installed so the choice needs a download
    choice = d._choices[ge_index]
    d._choices[ge_index] = replace(choice, setting=None, needs_download=True, label="GE-Proton (latest)")
    d.tool_combo.setCurrentIndex(ge_index)
    tool = d.selected_tool()
    assert tool.needs_download
    plan = gui_core.plan(window.install, window.account, d.analysis.game, 1091500, d.chosen_settings())
    dlg = ad.apply_dialog_for_plan(window, gui_core, window.runner, plan, ge_needed=True)
    qtbot.addWidget(dlg)
    dlg._start()
    assert dlg.pages.currentIndex() == 3, dlg.end_box.itemAt(0).widget().text()
    assert settings(gui_core, 1091500).compat_tool.name == TAG
    assert (steam.root / "compatibilitytools.d" / TAG / "proton").exists()


def test_history_undo(qtbot, window, gui_core, steam, auto_exec, monkeypatch):
    original = steam.localconfig.read_bytes(), steam.config.read_bytes()
    window.select_appid(1091500)
    window.detail.apply_btn.click()
    assert settings(gui_core, 1091500).compat_tool is not None
    dlg = HistoryDialog(window, gui_core, window.runner, window.install, window.account, 1091500, "Cyberpunk 2077")
    qtbot.addWidget(dlg)
    dlg.resize(1000, 560)
    shot(dlg, "history")
    assert dlg.table.rowCount() == 1
    dlg.works_btn.click()
    assert "works" in dlg.table.item(0, 5).text()
    dlg.undo_btn.click()
    assert (steam.localconfig.read_bytes(), steam.config.read_bytes()) == original
    assert dlg.table.rowCount() == 2 and dlg.changed


def test_restore_whole_file_from_backups_tab(qtbot, window, gui_core, steam, auto_exec):
    original = steam.localconfig.read_bytes()
    window.select_appid(1091500)
    window.detail.apply_btn.click()
    dlg = HistoryDialog(window, gui_core, window.runner, window.install, window.account)
    qtbot.addWidget(dlg)
    row = next(r for r, b in enumerate(dlg.backups) if b.reason == "apply")
    dlg.btable.selectRow(row)
    dlg.file_combo.setCurrentIndex(dlg.file_combo.findData("localconfig"))
    dlg.restore_btn.click()
    assert steam.localconfig.read_bytes() == original


def test_stale_settings_are_caught(qtbot, window, gui_core, steam, monkeypatch):
    window.select_appid(1091500)
    after = window.detail.chosen_settings()
    plan = gui_core.plan(window.install, window.account, None, 1091500, after)
    steam.localconfig.write_text(steam.localconfig.read_text().replace("winmm,version=n,b", "dxgi=n"))
    dlg = ad.apply_dialog_for_plan(window, gui_core, window.runner, plan)
    qtbot.addWidget(dlg)
    dlg._start()
    assert dlg.pages.currentIndex() == 3 and "changed in Steam" in dlg.end_box.itemAt(0).widget().text()
    assert config_store.read_settings(steam.localconfig.read_bytes(), steam.config.read_bytes(), 1091500).compat_tool is None


def test_real_thread_pool(qtbot, gui_core):
    w = MainWindow(gui_core, Runner())
    qtbot.addWidget(w)
    w.load()
    qtbot.waitUntil(lambda: len(w.model.summaries) == 7, timeout=10000)
    w.select_appid(570)
    qtbot.waitUntil(lambda: w.detail.analysis is not None, timeout=10000)
    assert w.detail.analysis.game.appid == 570
    w.runner.wait()


def test_report_text_is_never_rendered_as_html(window, gui_core):
    window.select_appid(1091500)
    from PySide6.QtWidgets import QLabel
    notes = [w for w in window.detail.notes_box.findChildren(QLabel)]
    assert notes and all(n.textFormat() == Qt.TextFormat.PlainText for n in notes)


def test_no_steam_banner(qtbot, ctx):
    w = MainWindow(Core(ctx), SyncRunner())
    qtbot.addWidget(w)
    w.load()
    texts = [w.top_banner.itemAt(i).widget().text() for i in range(w.top_banner.count())]
    assert any("Steam wasn't found" in t for t in texts)


def test_history_keeps_the_picked_entry_selected_after_marking(qtbot, window, gui_core, auto_exec):
    window.select_appid(1091500)
    window.detail.apply_btn.click()
    window.select_appid(570)
    window.detail.apply_btn.click()
    dlg = HistoryDialog(window, gui_core, window.runner, window.install, window.account)
    qtbot.addWidget(dlg)
    older = dlg.entries[1]
    dlg.table.selectRow(1)
    dlg.works_btn.click()
    assert dlg._current().id == older.id  # not the newest row
    dlg.undo_btn.click()  # acts on the entry the user picked
    assert settings(gui_core, 1091500).compat_tool is None
    assert settings(gui_core, 570).launch_options is not None
    assert dlg._current().id == older.id


def test_failed_rating_lookup_shows_unknown_and_retries(qtbot, gui_core):
    from autoproton.net import NetError

    url = f"{pdb.BASE}/api/v1/reports/summaries/570.json"
    good = gui_core.ctx.net.routes[url]
    gui_core.ctx.net.routes[url] = NetError("offline")
    w = MainWindow(gui_core, SyncRunner())
    qtbot.addWidget(w)
    w.load()
    row = w.model.row_of(570)
    assert w.model.data(w.model.index(row, 1)) == "?"
    gui_core.ctx.net.routes[url] = good
    w.load()  # "Reload games"
    assert w.model.data(w.model.index(w.model.row_of(570), 1)) == "Gold"


def test_cancelling_ge_download_is_not_an_error(qtbot, window, gui_core):
    from autoproton.net import Cancelled

    window.select_appid(570)
    plan = gui_core.plan(window.install, window.account, None, 570, window.detail.chosen_settings())
    dlg = ad.apply_dialog_for_plan(window, gui_core, window.runner, plan, ge_needed=True)
    qtbot.addWidget(dlg)
    dlg._fail(Cancelled())
    assert dlg.pages.currentIndex() == 3
    assert "Nothing was changed" in dlg.end_box.itemAt(0).widget().text()


def test_backups_tab_keeps_the_picked_backup_after_a_restore(qtbot, window, gui_core, steam, auto_exec):
    original = steam.localconfig.read_bytes()
    window.select_appid(1091500)
    window.detail.apply_btn.click()
    window.select_appid(570)
    window.detail.apply_btn.click()
    dlg = HistoryDialog(window, gui_core, window.runner, window.install, window.account)
    qtbot.addWidget(dlg)
    first = next(b for b in dlg.backups if b.game == "Cyberpunk 2077")
    dlg.btable.selectRow(dlg.backups.index(first))
    dlg.file_combo.setCurrentIndex(dlg.file_combo.findData("localconfig"))
    dlg.restore_btn.click()  # adds a new safety backup at the top
    assert steam.localconfig.read_bytes() == original
    assert dlg._current_backup().id == first.id  # the selection didn't slide onto another backup


def test_cancel_while_github_doesnt_answer_says_cancelled(qtbot, window, gui_core, monkeypatch):
    import threading
    from types import SimpleNamespace

    from autoproton.net import Cancelled

    window.select_appid(570)
    plan = gui_core.plan(window.install, window.account, None, 570, window.detail.chosen_settings())
    dlg = ad.apply_dialog_for_plan(window, gui_core, window.runner, plan, ge_needed=True)
    qtbot.addWidget(dlg)
    task = SimpleNamespace(cancel=threading.Event(), report=lambda v: None)

    def stuck():
        task.cancel.set()  # Cancel is pressed while the release lookup hangs, then it fails
        raise ge_proton.GEError("Couldn't reach GitHub: timed out")

    monkeypatch.setattr(gui_core, "ge_release", stuck)
    with pytest.raises(Cancelled):
        dlg._install_ge(task)
    task.cancel.clear()
    monkeypatch.setattr(gui_core, "ge_release", lambda: (_ for _ in ()).throw(ge_proton.GEError("rate limited")))
    with pytest.raises(ge_proton.GEError):  # without Cancel it's still the real error
        dlg._install_ge(task)
