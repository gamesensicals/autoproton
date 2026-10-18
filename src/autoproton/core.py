"""One object the GUI and the CLI both talk to."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from . import ge_proton, hardware
from .cache import Cache
from .context import AppContext
from .history import HistoryEntry
from .models import Account, CompatSetting, CompatTool, Game, GameSettings, HardwareProfile, SteamInstall
from .protondb.client import ProtonDB, Summary
from .protondb.parse import parse_reports
from .recommend.engine import Recommendation, ToolChoice, recommend
from .steam import compat, library, locate, process
from .transaction import ChangePlan, Store, current_settings, execute, recover, restore_file


class NoSteam(RuntimeError):
    def __init__(self):
        super().__init__(
            "Couldn't find Steam. Start Steam once so it creates its folders, or point autoproton at it with "
            "AUTOPROTON_STEAM_ROOT=/path/to/Steam."
        )


@dataclass
class Analysis:
    game: Game
    summary: Summary | None
    recommendation: Recommendation
    current: GameSettings
    fetched_at: float
    stale: bool


class Core:
    def __init__(self, ctx: AppContext | None = None):
        self.ctx = ctx or AppContext.default()
        self.store = Store(self.ctx)
        self.cache = Cache(self.ctx.cache_dir, clock=self.ctx.clock)
        self.protondb = ProtonDB(self.ctx.get_net(), self.cache, clock=self.ctx.clock)
        self._hw: HardwareProfile | None = None

    # ---- Steam --------------------------------------------------------------

    def installs(self) -> list[SteamInstall]:
        return locate.find_installs(self.ctx)

    def default_install(self) -> SteamInstall:
        found = self.installs()
        if not found:
            raise NoSteam()
        return found[0]

    def accounts(self, install: SteamInstall) -> list[Account]:
        return locate.accounts(install)

    def games(self, install: SteamInstall) -> list[Game]:
        return library.installed_games(install)

    def tools(self, install: SteamInstall) -> list[CompatTool]:
        return compat.installed_tools(self.ctx, install)

    def hardware(self) -> HardwareProfile:
        if self._hw is None:
            self._hw = hardware.detect(self.ctx)
        return self._hw

    def settings(self, install: SteamInstall, account: Account, appid: int) -> GameSettings:
        return current_settings(install, account, appid)

    def steam_running(self) -> bool:
        return process.is_running(self.ctx)

    def in_game_mode(self) -> bool:
        return process.in_game_mode(self.ctx.env)

    def close_steam(self, install: SteamInstall, timeout: float = 60.0, cancel: threading.Event | None = None) -> bool:
        if not self.steam_running():
            return True
        process.shutdown(self.ctx, install)
        return process.wait_for_exit(self.ctx, timeout=timeout, cancel=cancel)

    def start_steam(self, install: SteamInstall, appid: int | None = None) -> None:
        process.launch(self.ctx, install, f"steam://rungameid/{appid}" if appid else None)

    def open_url(self, url: str) -> None:
        process.open_url(self.ctx, url)

    # ---- ProtonDB -------------------------------------------------------------

    def summary(self, appid: int) -> Summary | None:
        return self.protondb.summary(appid)

    def analyze(self, install: SteamInstall, account: Account, game: Game, refresh: bool = False) -> Analysis:
        current = self.settings(install, account, game.appid)
        fetched = self.protondb.reports(game.appid, refresh=refresh)
        summary = None
        try:
            summary = self.protondb.summary(game.appid, refresh=refresh)
        except Exception:  # the rating is a nice-to-have next to the reports
            summary = None
        rec = recommend(
            game.appid, parse_reports(fetched.reports), self.hardware(), self.tools(install), current,
            self.ctx.clock(), self.ctx.which,
        )
        return Analysis(game, summary, rec, current, fetched.fetched_at, fetched.stale)

    # ---- GE-Proton ------------------------------------------------------------

    def ge_release(self) -> ge_proton.Release:
        return ge_proton.latest_release(self.ctx.get_net(), self.cache)

    def install_ge(self, install: SteamInstall, release: ge_proton.Release, progress=None, cancel=None) -> CompatTool:
        return ge_proton.install(self.ctx.get_net(), release, install.compat_dir, progress, cancel)

    @staticmethod
    def tool_choice_for(tool: CompatTool) -> ToolChoice:
        return ToolChoice("set", "ge", CompatSetting(tool.internal_name), tool.display_name, "Just installed.")

    # ---- changes --------------------------------------------------------------

    def plan(self, install: SteamInstall, account: Account, game: Game | None, appid: int, after: GameSettings,
             action: str = "apply", ref: str | None = None, note: str = "") -> ChangePlan:
        before = self.settings(install, account, appid)
        name = game.name if game else f"App {appid}"
        return ChangePlan(install, account, appid, name, before, after, action, ref, note)

    def apply(self, plan: ChangePlan) -> HistoryEntry:
        return execute(self.ctx, self.store, plan)

    def history(self, install: SteamInstall, account: Account | None, appid: int | None = None) -> list[HistoryEntry]:
        return self.store.history.entries(str(install.root), account.account_id if account else None, appid)

    def revert_plan(self, install: SteamInstall, account: Account, entry: HistoryEntry, restore: bool = False) -> ChangePlan:
        """Undo ``entry`` (put back its "before"), or with ``restore`` re-apply its "after"."""
        if entry.appid is None:
            raise ValueError("whole-file restores are undone from the backups list")
        target = entry.after if restore else entry.before
        before = self.settings(install, account, entry.appid)
        return ChangePlan(install, account, entry.appid, entry.game, before, target,
                          "restore" if restore else "revert", entry.id)

    def mark(self, entry_id: str, mark: str) -> None:
        self.store.history.mark(entry_id, mark)

    def backups(self):
        return self.store.backups.list()

    def restore_file(self, install: SteamInstall, account: Account | None, set_id: str, label: str) -> HistoryEntry:
        backup = self.store.backups.get(set_id)
        return restore_file(self.ctx, self.store, backup, label, install, account)

    def recover(self) -> str | None:
        return recover(self.ctx, self.store)
