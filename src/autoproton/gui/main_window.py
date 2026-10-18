"""The main window: games on the left, the selected game on the right."""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import HOMEPAGE, __version__
from ..core import Analysis, Core
from ..models import Account, Game, SteamInstall
from .apply_dialog import apply_dialog_for_plan
from .common import app_icon, banner, plain_label
from .detail import DetailPanel
from .games import GamesFilter, GamesModel
from .history_dialog import HistoryDialog
from .workers import Runner, friendly


class MainWindow(QMainWindow):
    def __init__(self, core: Core, runner: Runner | None = None):
        super().__init__()
        self.core = core
        self.runner = runner or Runner()
        self.install: SteamInstall | None = None
        self.account: Account | None = None
        self._generation = 0
        self._tier_task = None
        self.setWindowTitle("autoproton")
        self.setWindowIcon(app_icon())
        self.resize(1200, 760)

        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.install_combo = QComboBox()
        self.install_combo.currentIndexChanged.connect(self._install_changed)
        self.install_label = QLabel("Steam: ")
        self.install_action = tb.addWidget(self.install_label)
        self.install_combo_action = tb.addWidget(self.install_combo)
        tb.addWidget(QLabel("  Account: "))
        self.account_combo = QComboBox()
        self.account_combo.currentIndexChanged.connect(self._account_changed)
        tb.addWidget(self.account_combo)
        tb.addSeparator()
        tb.addAction("Reload games", self.load)
        tb.addAction("History and backups", self._history_all)
        tb.addAction("About", self._about)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        self.top_banner = QVBoxLayout()
        outer.addLayout(self.top_banner)
        split = QSplitter()
        outer.addWidget(split, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search games…")
        self.search.setClearButtonEnabled(True)
        ll.addWidget(self.search)
        self.model = GamesModel()
        self.proxy = GamesFilter()
        self.proxy.setSourceModel(self.model)
        self.search.textChanged.connect(self.proxy.set_text)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        h.resizeSection(3, 140)
        self.table.selectionModel().currentRowChanged.connect(self._selected)
        ll.addWidget(self.table)
        split.addWidget(left)

        self.detail = DetailPanel()
        self.detail.apply_requested.connect(self._apply)
        self.detail.history_requested.connect(self._history_game)
        self.detail.refresh_requested.connect(lambda: self._analyze(refresh=True))
        self.detail.open_url.connect(self.core.open_url)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([480, 720])

        self.steam_status = QLabel()
        self.statusBar().addPermanentWidget(self.steam_status)
        self.statusBar().showMessage(f"Device: {self.core.hardware().describe()}")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_steam)
        self.timer.start(3000)
        self._poll_steam()

    # ---- loading -----------------------------------------------------------------------

    def start(self):
        try:
            msg = self.core.recover()
        except Exception as e:  # noqa: BLE001
            msg = f"An interrupted change couldn't be checked: {friendly(e)}"
        if msg:
            QMessageBox.information(self, "autoproton", msg)
        self.load()

    def _clear_banner(self):
        while self.top_banner.count():
            w = self.top_banner.takeAt(0).widget()
            if w:
                w.deleteLater()

    def load(self):
        self._clear_banner()
        installs = self.core.installs()
        self.install_combo.blockSignals(True)
        self.install_combo.clear()
        for inst in installs:
            self.install_combo.addItem(inst.label, inst)
        self.install_combo.blockSignals(False)
        many = len(installs) > 1
        self.install_action.setVisible(many)
        self.install_combo_action.setVisible(many)
        if not installs:
            self.install = None
            self.model.set_games([], {})
            self.top_banner.addWidget(banner(
                "Steam wasn't found. Install Steam and start it once, then press “Reload games”. If it lives "
                "somewhere unusual, start autoproton with AUTOPROTON_STEAM_ROOT=/path/to/Steam.", "error"))
            self.detail.show_empty()
            return
        self._set_install(installs[0])

    def _install_changed(self, idx):
        inst = self.install_combo.itemData(idx)
        if inst is not None and inst != self.install:
            self._set_install(inst)

    def _set_install(self, inst: SteamInstall):
        self.install = inst
        accounts = self.core.accounts(inst)
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        for a in accounts:
            self.account_combo.addItem(a.label, a)
        self.account_combo.blockSignals(False)
        self.account = accounts[0] if accounts else None
        if not accounts:
            self.top_banner.addWidget(banner("No signed-in Steam account was found. Sign in to Steam once, then "
                                             "press “Reload games”.", "error"))
        self._load_games()

    def _account_changed(self, idx):
        acct = self.account_combo.itemData(idx)
        if acct is not None and acct != self.account:
            self.account = acct
            self._load_games()

    def _load_games(self):
        self._generation += 1
        if self.install is None:
            return
        games = self.core.games(self.install)
        settings = {}
        if self.account:
            for g in games:
                try:
                    settings[g.appid] = self.core.settings(self.install, self.account, g.appid)
                except Exception:  # noqa: BLE001 - shown as "default"; the detail view reports errors
                    pass
        self.model.set_games(games, settings)
        self.detail.show_empty()
        if not games:
            self.top_banner.addWidget(banner("No installed games found in this Steam library.", "info"))
        self._load_tiers(games)

    def _load_tiers(self, games: list[Game]):
        if self._tier_task is not None:
            self._tier_task.cancel.set()
        missing = [g.appid for g in games if g.appid not in self.model.summaries]

        def work(task):
            for appid in missing:
                if task.cancel.is_set():
                    return
                try:
                    task.report((appid, self.core.summary(appid)))
                except Exception:  # noqa: BLE001 - one game's rating failing shouldn't stop the rest
                    task.report((appid, None))

        self._tier_task = self.runner.run(work, on_progress=self._tier_loaded, with_task=True)

    def _tier_loaded(self, value):
        appid, summary = value
        self.model.set_summary(appid, summary)

    # ---- selection -------------------------------------------------------------------

    def current_game(self) -> Game | None:
        idx = self.table.selectionModel().currentIndex()
        if not idx.isValid():
            return None
        return self.proxy.data(self.proxy.index(idx.row(), 0), GamesModel.GameRole)

    def select_appid(self, appid: int) -> None:
        row = self.model.row_of(appid)
        if row < 0:
            return
        idx = self.proxy.mapFromSource(self.model.index(row, 0))
        self.table.selectionModel().setCurrentIndex(
            idx, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)

    def _selected(self, current: QModelIndex, _previous=None):
        if current.isValid():
            self._analyze()
        else:
            self.detail.show_empty()

    def _analyze(self, refresh: bool = False):
        game = self.current_game()
        if game is None or self.install is None or self.account is None:
            return
        self._generation += 1
        gen = self._generation
        self.detail.show_loading()
        inst, acct = self.install, self.account
        self.runner.run(
            lambda: self.core.analyze(inst, acct, game, refresh=refresh),
            on_done=lambda a: self._analyzed(gen, a),
            on_failed=lambda e: gen == self._generation and self.detail.show_error(friendly(e)),
        )

    def _analyzed(self, gen: int, a: Analysis):
        if gen != self._generation:
            return  # the user picked another game meanwhile
        if a.summary is not None or a.game.appid not in self.model.summaries:
            self.model.set_summary(a.game.appid, a.summary)
        self.model.set_settings(a.game.appid, a.current)
        self.detail.show_analysis(a)

    # ---- actions ----------------------------------------------------------------------

    def _apply(self, after):
        a = self.detail.analysis
        if a is None:
            return
        tool = self.detail.selected_tool()
        ge_needed = tool.needs_download and tool.action == "set"
        plan = self.core.plan(self.install, self.account, a.game, a.game.appid, after,
                              note=f"ProtonDB {a.recommendation.confidence} confidence")
        if plan.before != a.current:
            QMessageBox.information(self, "Settings changed", "This game's settings changed in Steam since they were "
                                    "loaded. Showing the latest ones now; review them and apply again.")
            self._analyze()
            return
        dlg = apply_dialog_for_plan(self, self.core, self.runner, plan, ge_needed=ge_needed,
                                    title=f"Apply settings — {a.game.name}")
        dlg.exec()
        if dlg.result_value is not None:
            self._analyze()

    def _history_game(self):
        game = self.current_game()
        if game and self.install and self.account:
            dlg = HistoryDialog(self, self.core, self.runner, self.install, self.account, game.appid, game.name)
            dlg.exec()
            if dlg.changed:
                self._analyze()

    def _history_all(self):
        if self.install and self.account:
            dlg = HistoryDialog(self, self.core, self.runner, self.install, self.account)
            dlg.exec()
            if dlg.changed:
                self._load_games()

    def _poll_steam(self):
        running = self.core.steam_running()
        self.steam_status.setText("● Steam is running — it will be closed before saving" if running else "○ Steam is closed")

    def _about(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("About autoproton")
        lay = QVBoxLayout(dlg)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(64, 64))
        lay.addWidget(icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        text = QLabel(
            f"<h3>autoproton {__version__}</h3>"
            "<p>Picks a Proton version and launch options for your Steam games from ProtonDB reports, "
            "and saves them to Steam with a backup you can undo.</p>"
            f"<p><a href='{HOMEPAGE}'>{HOMEPAGE}</a></p>"
            "<p>Ratings and reports come from <a href='https://www.protondb.com'>ProtonDB</a>. ProtonDB data is "
            "available under the <a href='https://opendatacommons.org/licenses/odbl/1-0/'>Open Database "
            "License</a>. autoproton isn't affiliated with ProtonDB or Valve.</p>"
        )
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setOpenExternalLinks(False)
        text.linkActivated.connect(self.core.open_url)
        lay.addWidget(text)
        lay.addWidget(plain_label(f"Backups and history are kept in {self.core.store.dir}", selectable=True))
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.resize(460, 300)
        dlg.exec()
