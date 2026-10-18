"""The list of installed games on the left of the window."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from ..models import Game, GameSettings
from ..protondb.client import Summary
from .common import pretty_tool, tier_color, tier_text

COLUMNS = ("Game", "ProtonDB", "Proton version", "Launch options")
TIER_ORDER = {"platinum": 0, "gold": 1, "silver": 2, "bronze": 3, "borked": 4, "pending": 5}


class GamesModel(QAbstractTableModel):
    GameRole = Qt.ItemDataRole.UserRole + 1
    SortRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self):
        super().__init__()
        self.games: list[Game] = []
        self.settings: dict[int, GameSettings] = {}
        self.summaries: dict[int, Summary | None] = {}
        self.loading: set[int] = set()

    def set_games(self, games: list[Game], settings: dict[int, GameSettings]) -> None:
        self.beginResetModel()
        self.games = list(games)
        self.settings = dict(settings)
        self.endResetModel()

    def set_summary(self, appid: int, summary: Summary | None) -> None:
        self.summaries[appid] = summary
        self.loading.discard(appid)
        self._changed(appid, 1)

    def set_settings(self, appid: int, settings: GameSettings) -> None:
        self.settings[appid] = settings
        self._changed(appid, 2, 3)

    def _changed(self, appid: int, first: int, last: int | None = None) -> None:
        for row, g in enumerate(self.games):
            if g.appid == appid:
                self.dataChanged.emit(self.index(row, first), self.index(row, last or first))

    def row_of(self, appid: int) -> int:
        return next((i for i, g in enumerate(self.games) if g.appid == appid), -1)

    # ---- Qt model API ----------------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.games)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        game = self.games[index.row()]
        col = index.column()
        summary = self.summaries.get(game.appid)
        settings = self.settings.get(game.appid, GameSettings())
        if role == self.GameRole:
            return game
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, self.SortRole):
            if col == 0:
                return game.name if role != self.SortRole else game.name.casefold()
            if col == 1:
                if game.appid not in self.summaries:
                    return "…" if role == Qt.ItemDataRole.DisplayRole else (99 if role == self.SortRole else "Loading")
                if summary is None:
                    return "No reports" if role != self.SortRole else 98
                if role == self.SortRole:
                    return TIER_ORDER.get(summary.tier, 50)
                if role == Qt.ItemDataRole.ToolTipRole:
                    return f"{tier_text(summary.tier)} from {summary.total} reports"
                return tier_text(summary.tier)
            if col == 2:
                return pretty_tool(settings.compat_tool)
            if col == 3:
                return settings.launch_options or ""
        if role == Qt.ItemDataRole.BackgroundRole and col == 1 and summary is not None:
            return tier_color(summary.tier)
        if role == Qt.ItemDataRole.ForegroundRole and col == 1 and summary is not None and tier_color(summary.tier):
            return Qt.GlobalColor.black
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 1:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None


class GamesFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortRole(GamesModel.SortRole)
        self.text = ""

    def set_text(self, text: str) -> None:
        if hasattr(self, "beginFilterChange"):  # Qt 6.10+
            self.beginFilterChange()
            self.text = text.strip().casefold()
            self.endFilterChange()
        else:
            self.text = text.strip().casefold()
            self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self.text:
            return True
        game = self.sourceModel().games[row]
        return self.text in game.name.casefold() or self.text == str(game.appid)
