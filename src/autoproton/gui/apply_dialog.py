"""The confirm-and-save dialog: shows exactly what will change, downloads
GE-Proton if needed, makes sure Steam is closed, then saves."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import Core
from ..ge_proton import GEError
from ..models import CompatSetting, SteamInstall
from ..net import Cancelled
from ..transaction import ChangePlan, StaleState
from .common import banner, plain_label, pretty_tool
from .workers import Runner, friendly

STAGES = {"checksum": "Fetching checksum…", "download": "Downloading GE-Proton…", "verify": "Checking the download…",
          "extract": "Unpacking…", "done": "Installed."}


class ApplyDialog(QDialog):
    """Generic over what gets saved: ``save`` runs on a worker thread once Steam is closed."""

    def __init__(self, parent, core: Core, runner: Runner, install: SteamInstall, title: str, intro: str,
                 rows: list[tuple[str, str, str]], save: Callable[[], object], ge_needed: bool = False,
                 on_ge_installed: Callable | None = None, on_ge_skipped: Callable | None = None,
                 done_text: Callable[[object], str] | None = None, steam_url_appid: int | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(620)
        self.core, self.runner, self.install = core, runner, install
        self.save_fn, self.ge_needed = save, ge_needed
        self.on_ge_installed, self.on_ge_skipped = on_ge_installed, on_ge_skipped
        self.done_text = done_text or (lambda result: "Saved.")
        self.appid = steam_url_appid
        self.result_value = None
        self.task = None

        self.pages = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(self.pages)

        # review
        review = QWidget()
        rl = QVBoxLayout(review)
        rl.addWidget(plain_label(intro))
        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["Setting", "Now", "After"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for r, (name, before, after) in enumerate(rows):
            for c, text in enumerate((name, before, after)):
                table.setItem(r, c, QTableWidgetItem(text))
        table.resizeRowsToContents()
        table.setMaximumHeight(60 + 48 * max(1, len(rows)))
        rl.addWidget(table)
        self.ge_check = QCheckBox("Download the latest GE-Proton from GitHub, check its published checksum, and install it")
        self.ge_check.setChecked(True)
        self.ge_check.setVisible(ge_needed)
        rl.addWidget(self.ge_check)
        rl.addWidget(plain_label(
            "Steam has to be closed while saving, because it overwrites these settings when it exits. "
            "A backup is taken first, and you can undo this from History at any time."))
        rl.addStretch(1)
        self.review_buttons = self._buttons(review, rl, [("Cancel", self.reject), ("Apply", self._start)], default=1)
        self.pages.addWidget(review)

        # working
        work = QWidget()
        wl = QVBoxLayout(work)
        self.work_label = plain_label("Working…")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        wl.addStretch(1)
        wl.addWidget(self.work_label)
        wl.addWidget(self.progress)
        wl.addStretch(1)
        self.cancel_btn = self._buttons(work, wl, [("Cancel", self._cancel_task)])[0]
        self.pages.addWidget(work)

        # steam is running
        steam = QWidget()
        sl = QVBoxLayout(steam)
        self.steam_label = plain_label()
        sl.addWidget(self.steam_label)
        sl.addStretch(1)
        self.steam_buttons = self._buttons(steam, sl, [
            ("Cancel", self.reject), ("I've closed it", self._after_ge), ("Close Steam for me", self._close_steam)],
            default=2)
        self.pages.addWidget(steam)

        # finished / failed
        end = QWidget()
        el = QVBoxLayout(end)
        self.end_box = QVBoxLayout()
        el.addLayout(self.end_box)
        el.addStretch(1)
        self.end_buttons = self._buttons(end, el, [("Start Steam", self._start_steam), ("Close", self.accept)], default=1)
        self.pages.addWidget(end)

    def _buttons(self, page, layout, specs, default=None):
        row = QHBoxLayout()
        row.addStretch(1)
        out = []
        for i, (text, slot) in enumerate(specs):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if i == default:
                b.setDefault(True)
            row.addWidget(b)
            out.append(b)
        layout.addLayout(row)
        return out

    # ---- flow ------------------------------------------------------------------------

    def _working(self, text: str, cancellable: bool = False):
        self.work_label.setText(text)
        self.progress.setRange(0, 0)
        self.cancel_btn.setVisible(cancellable)
        self.pages.setCurrentIndex(1)

    def _start(self):
        if self.ge_needed and self.ge_check.isChecked():
            self._working("Looking up the latest GE-Proton…", cancellable=True)
            self.task = self.runner.run(self._install_ge, self._ge_done, self._fail, self._ge_progress, with_task=True)
            return
        if self.ge_needed and self.on_ge_skipped:
            if not self.on_ge_skipped():
                self._finish_error("Without GE-Proton there is nothing left to change.")
                return
        self._after_ge()

    def _install_ge(self, task):
        try:
            release = self.core.ge_release()
        except GEError:
            if task.cancel.is_set():
                raise Cancelled() from None  # the user pressed Cancel while GitHub didn't answer
            raise
        task.report(("release", release.tag, release.size_mb))
        return self.core.install_ge(self.install, release, lambda s, d, t: task.report((s, d, t)), task.cancel)

    def _ge_progress(self, value):
        if value[0] == "release":
            self.work_label.setText(f"Downloading {value[1]} ({value[2]} MB)…")
            return
        stage, done, total = value
        if stage == "download" and total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done * 1000 / total))
            self.work_label.setText(f"{STAGES['download']} {done / 1e6:.0f} of {total / 1e6:.0f} MB")
        elif stage == "extract" and total:
            self.cancel_btn.setVisible(False)
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.work_label.setText(STAGES["extract"])
        else:
            self.progress.setRange(0, 0)
            self.work_label.setText(STAGES.get(stage, "Working…"))

    def _ge_done(self, tool):
        if self.on_ge_installed:
            self.on_ge_installed(tool)
        self._after_ge()

    def _after_ge(self):
        if self.core.steam_running():
            game_mode = self.core.in_game_mode()
            self.steam_label.setText(
                "Steam is running in Game Mode. Switch to Desktop Mode (Steam menu → Power → Switch to Desktop), "
                "then open autoproton there." if game_mode else
                "Steam is running. It has to be closed while saving, because it rewrites these settings when it exits.")
            self.steam_buttons[2].setVisible(not game_mode)
            self.pages.setCurrentIndex(2)
            return
        self._save()

    def _close_steam(self):
        self._working("Closing Steam…", cancellable=True)
        self.task = self.runner.run(lambda task: self.core.close_steam(self.install, 60, task.cancel),
                                    self._steam_closed, self._fail, with_task=True)

    def _steam_closed(self, ok):
        if ok:
            self._save()
        else:
            self.steam_label.setText("Steam is still running. Close it from its own menu (Steam → Exit), then press "
                                     "“I've closed it”.")
            self.pages.setCurrentIndex(2)

    def _save(self):
        self._working("Saving to Steam…")
        self.task = self.runner.run(self.save_fn, self._saved, self._fail)

    def _saved(self, result):
        self.result_value = result
        self._finish(banner(self.done_text(result), "info"), ok=True)

    def _fail(self, exc):
        if isinstance(exc, Cancelled):
            self._finish(banner("Cancelled. Nothing was changed.", "info"), ok=False)
            return
        if isinstance(exc, StaleState):
            self._finish_error(str(exc) + " Close this window and look again.")
        else:
            self._finish_error(friendly(exc))

    def _finish_error(self, text: str):
        self._finish(banner(text, "error"), ok=False)

    def _finish(self, widget: QLabel, ok: bool):
        while self.end_box.count():
            w = self.end_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.end_box.addWidget(widget)
        self.end_buttons[0].setVisible(ok and not self.core.steam_running())
        self.pages.setCurrentIndex(3)

    def _cancel_task(self):
        if self.task is not None:
            self.task.cancel.set()
        self.work_label.setText("Cancelling…")

    def _start_steam(self):
        self.core.start_steam(self.install)
        self.end_buttons[0].setEnabled(False)
        self.end_buttons[0].setText("Steam is starting…")
        QTimer.singleShot(1500, self.accept)

    def reject(self):
        if self.pages.currentIndex() == 1 and self.task is not None:
            self._cancel_task()
            return
        super().reject()


def apply_dialog_for_plan(parent, core: Core, runner: Runner, plan: ChangePlan, ge_needed: bool = False,
                          title: str = "Apply settings") -> ApplyDialog:
    holder = {"plan": plan}

    def on_ge(tool):
        p = holder["plan"]
        holder["plan"] = replace(p, after=replace(p.after, compat_tool=CompatSetting(tool.internal_name)))

    def skipped():
        p = holder["plan"]
        holder["plan"] = replace(p, after=replace(p.after, compat_tool=p.before.compat_tool))
        return holder["plan"].after != p.before

    rows = []
    if ge_needed or plan.before.compat_tool != plan.after.compat_tool:
        after_tool = "latest GE-Proton (downloaded first)" if ge_needed else pretty_tool(plan.after.compat_tool)
        rows.append(("Proton version", pretty_tool(plan.before.compat_tool), after_tool))
    if plan.before.launch_options != plan.after.launch_options:
        rows.append(("Launch options", plan.before.describe_options(), plan.after.describe_options()))
    verb = {"apply": "These changes will be saved to Steam for", "revert": "This undoes the change to",
            "restore": "This brings back earlier settings for"}.get(plan.action, "Changes for")
    dlg = ApplyDialog(
        parent, core, runner, plan.install, title, f"{verb} {plan.game}:", rows,
        save=lambda: core.apply(holder["plan"]), ge_needed=ge_needed, on_ge_installed=on_ge, on_ge_skipped=skipped,
        done_text=lambda entry: f"Saved. Start the game from Steam to try it. If it's worse, use History to undo "
                                f"(backup {entry.backup_set}).",
    )
    dlg.plan_holder = holder
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    return dlg
