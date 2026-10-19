"""History of changes, with undo, and the whole-file backups."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import Core
from ..models import Account, SteamInstall
from .apply_dialog import ApplyDialog, apply_dialog_for_plan
from .common import plain_label, pretty_tool, when
from .workers import Runner, friendly

ACTIONS = {"apply": "Applied", "revert": "Undid a change", "restore": "Brought back", "restore-file": "Restored file"}
MARKS = {"untested": "", "works": "✔ works", "broken": "✘ doesn't work"}


class HistoryDialog(QDialog):
    def __init__(self, parent, core: Core, runner: Runner, install: SteamInstall, account: Account,
                 appid: int | None = None, game_name: str | None = None):
        super().__init__(parent)
        self.core, self.runner, self.install, self.account, self.appid = core, runner, install, account, appid
        self.setWindowTitle(f"History — {game_name}" if game_name else "History")
        self.resize(900, 520)
        self.changed = False
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # changes
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.addWidget(plain_label("Every change autoproton made. Undo one to put back what Steam had before it, or "
                                 "bring a change back if it worked better."))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["When", "Game", "What", "Proton version", "Launch options", "Result"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        h = self.table.horizontalHeader()
        for col in (0, 1, 2, 5):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection)
        pl.addWidget(self.table)
        row = QHBoxLayout()
        self.undo_btn = QPushButton("Undo this change")
        self.redo_btn = QPushButton("Use these settings again")
        self.works_btn = QPushButton("Mark: works")
        self.broken_btn = QPushButton("Mark: doesn't work")
        self.undo_btn.clicked.connect(lambda: self._revert(restore=False))
        self.redo_btn.clicked.connect(lambda: self._revert(restore=True))
        self.works_btn.clicked.connect(lambda: self._mark("works"))
        self.broken_btn.clicked.connect(lambda: self._mark("broken"))
        for b in (self.undo_btn, self.redo_btn, self.works_btn, self.broken_btn):
            row.addWidget(b)
        row.addStretch(1)
        pl.addLayout(row)
        tabs.addTab(page, "Changes")

        # backups
        bpage = QWidget()
        bl = QVBoxLayout(bpage)
        bl.addWidget(plain_label(
            "Full copies of Steam's files, taken before every change. Restoring one replaces the whole file, which "
            "also undoes anything Steam changed in it since (playtime, other games' settings), so prefer undoing "
            "single changes above. The current file is backed up before a restore. “first backup” copies are from "
            "before autoproton ever changed anything and are never deleted."))
        self.btable = QTableWidget(0, 4)
        self.btable.setHorizontalHeaderLabels(["When", "Why", "Game", "Files"])
        self.btable.verticalHeader().setVisible(False)
        self.btable.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.btable.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.btable.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.btable.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        bl.addWidget(self.btable)
        brow = QHBoxLayout()
        self.file_combo = QComboBox()
        self.restore_btn = QPushButton("Restore this file…")
        self.restore_btn.clicked.connect(self._restore_file)
        self.btable.itemSelectionChanged.connect(self._bselection)
        brow.addWidget(self.file_combo)
        brow.addWidget(self.restore_btn)
        brow.addStretch(1)
        open_btn = QPushButton("Open backups folder")
        open_btn.clicked.connect(lambda: self.core.open_url(self.core.store.backups.root.as_uri()))
        brow.addWidget(open_btn)
        bl.addLayout(brow)
        tabs.addTab(bpage, "Backups")

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        crow = QHBoxLayout()
        crow.addStretch(1)
        crow.addWidget(close)
        lay.addLayout(crow)
        self.reload()

    # ---- data -----------------------------------------------------------------------

    def reload(self):
        # Keep the same entry and backup selected across a reload. Rows shift when
        # a new change or backup is added at the top, and the next click must act
        # on what the user picked.
        keep_entry = self._current() if getattr(self, "entries", None) else None
        keep_backup = self._current_backup() if getattr(self, "backups", None) else None
        self.entries = self.core.history(self.install, self.account, self.appid)
        self.table.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            if e.appid is None:
                tool, opts = "", ""
            else:
                tool = f"{pretty_tool(e.before.compat_tool)}  →  {pretty_tool(e.after.compat_tool)}"
                opts = f"{e.before.describe_options()}  →  {e.after.describe_options()}"
            values = (when(e.ts), e.game, ACTIONS.get(e.action, e.action), tool, opts, MARKS.get(e.mark, ""))
            for c, text in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeRowsToContents()
        row = next((r for r, e in enumerate(self.entries) if keep_entry and e.id == keep_entry.id),
                   0 if self.entries else None)
        if row is not None:
            self.table.selectRow(row)
        self._selection()

        self.backups = self.core.backups()
        self.btable.setRowCount(len(self.backups))
        for r, b in enumerate(self.backups):
            files = ", ".join(f"{f.label}.vdf" for f in b.files)
            for c, text in enumerate((when(b.created), b.reason, b.game or "", files)):
                self.btable.setItem(r, c, QTableWidgetItem(text))
        brow = next((r for r, b in enumerate(self.backups) if keep_backup and b.id == keep_backup.id), None)
        if brow is not None:
            self.btable.selectRow(brow)
        else:
            self.btable.clearSelection()
        self._bselection()

    def _current(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.entries[rows[0].row()] if rows else None

    def _selection(self):
        e = self._current()
        game_change = e is not None and e.appid is not None
        for b in (self.undo_btn, self.redo_btn, self.works_btn, self.broken_btn):
            b.setEnabled(game_change)

    def _current_backup(self):
        rows = self.btable.selectionModel().selectedRows() if self.btable.selectionModel() else []
        return self.backups[rows[0].row()] if rows else None

    def _bselection(self):
        rows = self.btable.selectionModel().selectedRows() if self.btable.selectionModel() else []
        self.file_combo.clear()
        if rows:
            for f in self.backups[rows[0].row()].files:
                self.file_combo.addItem(f"{f.label}.vdf", f.label)
        self.restore_btn.setEnabled(bool(rows) and self.file_combo.count() > 0)

    # ---- actions ----------------------------------------------------------------------

    def _mark(self, mark: str):
        e = self._current()
        if e:
            self.core.mark(e.id, mark)
            self.reload()

    def _revert(self, restore: bool):
        e = self._current()
        if e is None:
            return
        try:
            plan = self.core.revert_plan(self.install, self.account, e, restore=restore)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Can't do that", friendly(exc))
            return
        if plan.before == plan.after:
            QMessageBox.information(self, "Nothing to do", "Steam already has exactly these settings.")
            return
        if not restore and plan.before != e.after:
            answer = QMessageBox.question(
                self, "Settings changed since",
                "This game's settings were changed outside autoproton after this entry. Undoing it will "
                "replace the current settings too. Continue?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        dlg = apply_dialog_for_plan(self, self.core, self.runner, plan, title="Undo" if not restore else "Bring back")
        dlg.exec()
        if dlg.result_value is not None:
            self.changed = True
        self.reload()

    def _restore_file(self):
        backup = self._current_backup()
        if backup is None:
            return
        label = self.file_combo.currentData()
        info = backup.file(label)
        dlg = ApplyDialog(
            self, self.core, self.runner, self.install, "Restore file",
            f"Replace {info.source} with the copy from {when(backup.created)}?",
            [(f"{label}.vdf", "current file", f"backup from {when(backup.created)}")],
            save=lambda: self.core.restore_file(self.install, self.account, backup.id, label),
            done_text=lambda entry: f"Restored. The file as it was just before is kept as backup {entry.backup_set}.",
        )
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.exec()
        if dlg.result_value is not None:
            self.changed = True
        self.reload()
