"""The right-hand panel: one game's current settings, the recommendation, and the choices."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import Analysis
from ..models import GameSettings
from ..protondb.client import game_url
from ..recommend import knowledge
from ..recommend.engine import OptionSuggestion, ToolChoice
from ..recommend.launchopts import Component
from .common import FAMILY_NAMES, banner, hline, plain_label, pretty_tool, tier_color, tier_text, when

CONFIDENCE_TEXT = {"high": "High confidence", "medium": "Medium confidence", "low": "Low confidence", "none": "No recommendation"}
KEEP = ToolChoice("keep", None, None, "Don't change", "Leave the Proton version as it is.")


class DetailPanel(QStackedWidget):
    apply_requested = Signal(object)  # GameSettings to apply
    history_requested = Signal()
    refresh_requested = Signal()
    open_url = Signal(str)

    def __init__(self):
        super().__init__()
        self.analysis: Analysis | None = None
        self._manual = False
        self._building = False

        self.empty = self._message_page("Pick a game on the left to see what ProtonDB suggests for it.")
        self.loading = self._message_page("Reading ProtonDB reports…")
        self.error_page = QWidget()
        el = QVBoxLayout(self.error_page)
        self.error_label = banner("", "error")
        retry = QPushButton("Try again")
        retry.clicked.connect(self.refresh_requested.emit)
        el.addWidget(self.error_label)
        el.addWidget(retry, alignment=Qt.AlignmentFlag.AlignLeft)
        el.addStretch(1)
        self.detail = self._build_detail()
        for page in (self.empty, self.loading, self.error_page, self.detail):
            self.addWidget(page)
        self.setCurrentWidget(self.empty)

    # ---- pages -------------------------------------------------------------------

    def _message_page(self, text: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addStretch(1)
        label = plain_label(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: palette(mid); font-size: 14px;")
        lay.addWidget(label)
        lay.addStretch(2)
        return page

    def _build_detail(self) -> QWidget:
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(10)

        head = QHBoxLayout()
        self.title = plain_label(wrap=True)
        self.title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.tier = QLabel()
        self.tier.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tier.setMinimumWidth(90)
        head.addWidget(self.title, 1)
        head.addWidget(self.tier, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        self.subtitle = plain_label()
        self.subtitle.setStyleSheet("color: palette(mid);")
        lay.addWidget(self.subtitle)
        links = QHBoxLayout()
        self.protondb_link = QPushButton("Open on ProtonDB")
        self.protondb_link.setFlat(True)
        self.protondb_link.setStyleSheet("text-align: left; color: palette(link); padding: 0;")
        self.protondb_link.clicked.connect(lambda: self.analysis and self.open_url.emit(game_url(self.analysis.game.appid)))
        refresh = QPushButton("Refresh reports")
        refresh.setFlat(True)
        refresh.setStyleSheet("color: palette(link); padding: 0;")
        refresh.clicked.connect(self.refresh_requested.emit)
        links.addWidget(self.protondb_link)
        links.addWidget(refresh)
        links.addStretch(1)
        lay.addLayout(links)
        self.warnings = QVBoxLayout()
        lay.addLayout(self.warnings)

        # Proton version
        tool_box = QGroupBox("Proton version")
        tl = QVBoxLayout(tool_box)
        self.tool_now = plain_label()
        tl.addWidget(self.tool_now)
        row = QHBoxLayout()
        row.addWidget(QLabel("Use:"))
        self.tool_combo = QComboBox()
        self.tool_combo.currentIndexChanged.connect(self._tool_changed)
        row.addWidget(self.tool_combo, 1)
        tl.addLayout(row)
        self.tool_detail = plain_label()
        self.tool_detail.setStyleSheet("color: palette(mid);")
        tl.addWidget(self.tool_detail)
        self.tool_scores = plain_label()
        self.tool_scores.setStyleSheet("color: palette(mid); font-size: 11px;")
        tl.addWidget(self.tool_scores)
        lay.addWidget(tool_box)

        # Launch options
        opt_box = QGroupBox("Launch options")
        ol = QVBoxLayout(opt_box)
        self.opts_now = plain_label(selectable=True)
        ol.addWidget(self.opts_now)
        self.opts_table = QTableWidget(0, 4)
        self.opts_table.setHorizontalHeaderLabels(["Option", "Kind", "Reports", "Why"])
        self.opts_table.verticalHeader().setVisible(False)
        self.opts_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.opts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.opts_table.setWordWrap(True)
        header = self.opts_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.opts_table.itemChanged.connect(self._option_toggled)
        self.opts_table.setMinimumHeight(160)
        ol.addWidget(self.opts_table)
        self.show_all = QCheckBox("Show options used by very few reports, or not for this computer")
        self.show_all.toggled.connect(lambda _: self._fill_options())
        ol.addWidget(self.show_all)
        mode = QHBoxLayout()
        self.merge_radio = QRadioButton("Add to my current launch options")
        self.replace_radio = QRadioButton("Replace them")
        self.merge_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.merge_radio)
        group.addButton(self.replace_radio)
        self.merge_radio.toggled.connect(lambda _: self._regenerate())
        mode.addWidget(self.merge_radio)
        mode.addWidget(self.replace_radio)
        mode.addStretch(1)
        ol.addLayout(mode)
        ol.addWidget(QLabel("Steam will use:"))
        final_row = QHBoxLayout()
        self.final = QLineEdit()
        self.final.setPlaceholderText("(no launch options)")
        self.final.textEdited.connect(self._edited)
        self.reset_final = QPushButton("Reset")
        self.reset_final.setToolTip("Go back to the options ticked above")
        self.reset_final.clicked.connect(self._reset_final)
        final_row.addWidget(self.final, 1)
        final_row.addWidget(self.reset_final)
        ol.addLayout(final_row)
        self.final_warning = plain_label()
        self.final_warning.setStyleSheet("color: #b35c00;")
        ol.addWidget(self.final_warning)
        lay.addWidget(opt_box)

        # Notes
        self.notes_box = QGroupBox("What recent reporters said")
        self.notes_layout = QVBoxLayout(self.notes_box)
        lay.addWidget(self.notes_box)

        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(inner)
        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(scroll, 1)
        page_lay.addWidget(hline())
        buttons = QHBoxLayout()
        buttons.setContentsMargins(8, 4, 8, 8)
        self.history_btn = QPushButton("History and undo…")
        self.history_btn.clicked.connect(self.history_requested.emit)
        self.apply_btn = QPushButton("Review and apply…")
        self.apply_btn.setDefault(True)
        self.apply_btn.setStyleSheet("QPushButton { font-weight: 600; padding: 6px 14px; }")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_hint = plain_label()
        self.apply_hint.setStyleSheet("color: palette(mid);")
        buttons.addWidget(self.history_btn)
        buttons.addWidget(self.apply_hint, 1)
        buttons.addWidget(self.apply_btn)
        page_lay.addLayout(buttons)
        return page

    # ---- public --------------------------------------------------------------------

    def show_empty(self):
        self.analysis = None
        self.setCurrentWidget(self.empty)

    def show_loading(self):
        self.setCurrentWidget(self.loading)

    def show_error(self, text: str):
        self.error_label.setText(text)
        self.setCurrentWidget(self.error_page)

    def show_analysis(self, a: Analysis):
        self._building = True
        self.analysis = a
        rec, game = a.recommendation, a.game
        self.title.setText(game.name)
        if a.summary:
            self.tier.setText(f"  {tier_text(a.summary.tier)}  ")
            color = tier_color(a.summary.tier)
            self.tier.setStyleSheet(
                f"background: {color.name() if color else 'transparent'}; color: black; border-radius: 6px;"
                "font-weight: 600; padding: 4px;"
            )
            sub = f"AppID {game.appid} · ProtonDB {tier_text(a.summary.tier)} from {a.summary.total} reports"
        else:
            self.tier.setText("  No rating  ")
            self.tier.setStyleSheet("border: 1px solid palette(mid); border-radius: 6px; padding: 4px;")
            sub = f"AppID {game.appid} · no ProtonDB rating yet"
        sub += f" · {CONFIDENCE_TEXT[rec.confidence]}"
        if rec.used_reports:
            sub += f"\n{rec.pool_description}; newest {when(rec.newest)}."
        self.subtitle.setText(sub)

        while self.warnings.count():
            w = self.warnings.takeAt(0).widget()
            if w:
                w.deleteLater()
        if a.stale:
            self.warnings.addWidget(banner(f"ProtonDB couldn't be reached, so these are reports saved on {when(a.fetched_at)}.", "info"))
        for text in rec.warnings:
            self.warnings.addWidget(banner(text))

        self.tool_now.setText(f"Now: {pretty_tool(a.current.compat_tool)}")
        self.tool_combo.clear()
        choices = [rec.tool] + [t for t in rec.alternatives if t.label != rec.tool.label or t.action != rec.tool.action]
        if rec.tool.action != "keep":
            choices.append(KEEP)
        self._choices = choices
        for i, choice in enumerate(choices):
            label = choice.label
            if choice.action == "keep" and choice is not KEEP:
                label += " (no change needed)"
            if choice.action == "clear":
                label += " — stop forcing Proton"
            if i == 0:
                label += "  ★ recommended"
            self.tool_combo.addItem(label)
        self.tool_combo.setCurrentIndex(0)
        if rec.families:
            self.tool_scores.setText("Reports by option: " + "; ".join(
                f"{FAMILY_NAMES.get(f.family, f.family)}: {f.working} of {f.reports} work"
                for f in rec.families))
        else:
            self.tool_scores.setText("")

        self.opts_now.setText(f"Now: {a.current.describe_options()}")
        self.show_all.setChecked(False)
        self.replace_radio.setChecked(False)
        self.merge_radio.setChecked(True)
        self._checked = {o.component.vote_key for o in rec.options if o.default_on}
        self._manual = False
        self._fill_options()

        while self.notes_layout.count():
            w = self.notes_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        for ts, tool, note in rec.notes:
            self.notes_layout.addWidget(plain_label(f"{when(ts)[:10]} · {tool}: {note}"))
        self.notes_box.setVisible(bool(rec.notes))

        self._building = False
        self._tool_changed()
        self._regenerate()
        self.setCurrentWidget(self.detail)

    # ---- choices --------------------------------------------------------------------

    def selected_tool(self) -> ToolChoice:
        idx = self.tool_combo.currentIndex()
        return self._choices[idx] if 0 <= idx < len(self._choices) else KEEP

    def _tool_changed(self, *_):
        if self._building or not self.analysis:
            return
        self.tool_detail.setText(self.selected_tool().detail)
        self._update_apply()

    def _visible_options(self) -> list[OptionSuggestion]:
        opts = self.analysis.recommendation.options if self.analysis else []
        return [o for o in opts if self.show_all.isChecked() or not o.hidden]

    def _fill_options(self):
        if not self.analysis:
            return
        building = self._building
        self._building = True
        opts = self._visible_options()
        self._row_options = opts
        self.opts_table.setRowCount(len(opts))
        for row, o in enumerate(opts):
            item = QTableWidgetItem(o.component.text)
            flags = Qt.ItemFlag.ItemIsEnabled
            if o.available:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
            item.setCheckState(Qt.CheckState.Checked if o.component.vote_key in self._checked else Qt.CheckState.Unchecked)
            tip = o.description or ""
            if o.alternatives:
                tip += ("\n" if tip else "") + "Other values seen: " + ", ".join(f"{v} ({n})" for v, n in o.alternatives)
            item.setToolTip(tip)
            self.opts_table.setItem(row, 0, item)
            self.opts_table.setItem(row, 1, self._ro(knowledge.CATEGORY_LABELS.get(o.category, o.category)))
            self.opts_table.setItem(row, 2, self._ro(f"{o.share:.0%} ({o.count})"))
            why = o.reason or o.description or ("Commonly used by working reports." if o.default_on else "")
            self.opts_table.setItem(row, 3, self._ro(why))
            if not o.available or o.hidden:
                for col in range(4):
                    self.opts_table.item(row, col).setForeground(self.palette().color(self.foregroundRole()).lighter(160))
        self.opts_table.resizeRowsToContents()
        if not opts:
            self.opts_table.setRowCount(1)
            self.opts_table.setItem(0, 0, self._ro("No launch options are needed according to the reports."))
            self.opts_table.setSpan(0, 0, 1, 4)
        else:
            self.opts_table.clearSpans()
        self._building = building

    @staticmethod
    def _ro(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        return item

    def _option_toggled(self, item: QTableWidgetItem):
        if self._building or item.column() != 0 or not getattr(self, "_row_options", None):
            return
        if item.row() >= len(self._row_options):
            return
        key = self._row_options[item.row()].component.vote_key
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(key)
        else:
            self._checked.discard(key)
        self._manual = False
        self._regenerate()

    def selected_components(self) -> list[Component]:
        if not self.analysis:
            return []
        return [o.component for o in self.analysis.recommendation.options if o.component.vote_key in self._checked]

    def _generated(self) -> str | None:
        a = self.analysis
        return a.recommendation.launch_options(a.current.launch_options, self.selected_components(),
                                               replace=self.replace_radio.isChecked())

    def _regenerate(self):
        if self._building or not self.analysis:
            return
        if not self._manual:
            self.final.setText(self._generated() or "")
        self._update_apply()

    def _edited(self, _text):
        self._manual = True
        self._update_apply()

    def _reset_final(self):
        self._manual = False
        self._regenerate()

    def chosen_settings(self) -> GameSettings:
        a = self.analysis
        text = self.final.text().strip()
        if text:
            launch = text
        elif a.current.launch_options in (None, ""):
            launch = a.current.launch_options  # empty stays empty; no pointless change
        elif self._manual or self.replace_radio.isChecked():
            launch = None  # the user cleared the box, or replaced with nothing: remove them
        else:
            launch = a.current.launch_options
        return GameSettings(launch, self.selected_tool().resulting(a.current.compat_tool))

    def _update_apply(self):
        if not self.analysis:
            return
        a = self.analysis
        warn = ""
        text = self.final.text()
        if any(c in text for c in "`$;|&<>\n"):
            warn = "These launch options contain shell symbols. They'll be saved exactly as typed."
        self.final_warning.setText(warn)
        self.final_warning.setVisible(bool(warn))
        tool = self.selected_tool()
        after = self.chosen_settings()
        changes = after != a.current or (tool.needs_download and tool.action == "set")
        self.apply_btn.setEnabled(changes)
        self.apply_hint.setText("" if changes else "Steam already has these settings.")

    def _apply(self):
        if self.analysis:
            self.apply_requested.emit(self.chosen_settings())
