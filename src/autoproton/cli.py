"""Command-line interface. Works without a display, so it is also the way back
if the window ever won't open:

    autoproton --cli history 1091500
    autoproton --cli revert 1091500
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from .backup import BackupError
from .core import Core, NoSteam
from .ge_proton import GEError
from .models import Account, GameSettings, SteamInstall
from .protondb.client import ProtonDBError, game_url
from .recommend import knowledge
from .transaction import SteamRunning, TransactionError


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


class CliError(RuntimeError):
    pass


class App:
    def __init__(self, args, core: Core | None = None, out=None):
        self.args = args
        self.core = core or Core()
        self.out = out or sys.stdout

    def say(self, *parts):
        print(*parts, file=self.out)

    # ---- selection ---------------------------------------------------------

    def install(self) -> SteamInstall:
        installs = self.core.installs()
        if not installs:
            raise NoSteam()
        return installs[0]

    def account(self, install: SteamInstall) -> Account:
        accounts = self.core.accounts(install)
        if not accounts:
            raise CliError("No Steam account found in this Steam install. Sign in to Steam once first.")
        if self.args.account:
            match = [a for a in accounts if self.args.account in (a.account_id, a.persona, a.account_name)]
            if not match:
                raise CliError(f"No account {self.args.account!r}. Known: {', '.join(a.label for a in accounts)}")
            return match[0]
        return accounts[0]

    def game(self, install, appid: int):
        return next((g for g in self.core.games(install) if g.appid == appid), None)

    def ensure_steam_closed(self, install: SteamInstall) -> None:
        if not self.core.steam_running():
            return
        if self.core.in_game_mode():
            raise CliError("Steam is running in Game Mode. Switch to Desktop Mode, then try again.")
        if not getattr(self.args, "close_steam", False):
            raise SteamRunning()
        self.say("Closing Steam…")
        if not self.core.close_steam(install):
            raise CliError("Steam didn't close within a minute. Close it yourself and try again.")

    def show_settings(self, label: str, s: GameSettings):
        self.say(f"  {label}: tool = {s.describe_tool()}; launch options = {s.describe_options()}")

    # ---- commands -----------------------------------------------------------

    def cmd_status(self):
        inst = self.install()
        acct = self.account(inst)
        self.say(f"Steam:    {inst.label}")
        self.say(f"Account:  {acct.label}")
        self.say(f"Device:   {self.core.hardware().describe()}")
        self.say(f"Running:  {'yes' if self.core.steam_running() else 'no'}")
        if self.args.appid:
            self.show_settings(f"App {self.args.appid}", self.core.settings(inst, acct, self.args.appid))

    def cmd_games(self):
        inst = self.install()
        acct = self.account(inst)
        for g in self.core.games(inst):
            s = self.core.settings(inst, acct, g.appid)
            self.say(f"{g.appid:>8}  {g.name[:40]:40}  {s.describe_tool():22}  {s.launch_options or ''}")

    def _analysis(self):
        inst = self.install()
        acct = self.account(inst)
        game = self.game(inst, self.args.appid)
        if game is None:
            raise CliError(f"App {self.args.appid} isn't installed in this Steam library.")
        return inst, acct, self.core.analyze(inst, acct, game, refresh=getattr(self.args, "refresh", False))

    def cmd_recommend(self):
        _inst, _acct, a = self._analysis()
        rec = a.recommendation
        if self.args.json:
            self.say(json.dumps({
                "appid": rec.appid, "tier": a.summary.tier if a.summary else None, "confidence": rec.confidence,
                "tool": {"action": rec.tool.action, "label": rec.tool.label,
                         "name": rec.tool.setting.name if rec.tool.setting else None,
                         "needs_download": rec.tool.needs_download},
                "launch_options": rec.launch_options(a.current.launch_options),
                "options": [{"text": o.component.text, "category": o.category, "share": round(o.share, 3),
                             "reports": o.count, "default_on": o.default_on, "hidden": o.hidden}
                            for o in rec.options],
                "warnings": rec.warnings,
            }, indent=2))
            return
        self.say(f"{a.game.name} ({a.game.appid}) — ProtonDB: {a.summary.tier if a.summary else 'no rating'}"
                 f" — {game_url(a.game.appid)}")
        self.say(f"{rec.pool_description}. Confidence: {rec.confidence}.")
        if a.stale:
            self.say(f"(ProtonDB couldn't be reached; using reports saved {_when(a.fetched_at)})")
        for w in rec.warnings:
            self.say(f"! {w}")
        self.show_settings("Now", a.current)
        self.say(f"  Proton: {rec.tool.label} ({rec.tool.action}) — {rec.tool.detail}")
        for f in rec.families:
            self.say(f"     {f.family:12} {f.working:>4}/{f.reports:<4} working, score {f.score:.2f}")
        self.say("  Launch options:")
        for o in rec.options:
            if o.hidden and not self.args.all:
                continue
            mark = "[x]" if o.default_on else ("[-]" if not o.available or o.hidden else "[ ]")
            self.say(f"    {mark} {o.component.text:40} {knowledge.CATEGORY_LABELS[o.category]:22} "
                     f"{o.share:4.0%} of reports ({o.count}){'  — ' + o.reason if o.reason else ''}")
        self.say(f"  Result: {rec.launch_options(a.current.launch_options) or '(none)'}")

    def cmd_apply(self):
        inst, acct, a = self._analysis()
        rec = a.recommendation
        tool = rec.tool
        if tool.needs_download:
            if not self.args.download_ge:
                self.say("The best match is GE-Proton, which isn't installed. Re-run with --download-ge to fetch it.")
                tool = next((t for t in rec.alternatives if not t.needs_download), None) or tool
            else:
                release = self.core.ge_release()
                if not _confirm(f"Download {release.tag} ({release.size_mb} MB) from GitHub?", self.args.yes):
                    raise CliError("Cancelled.")
                installed = self.core.install_ge(inst, release, self._progress)
                self.say("")
                tool = self.core.tool_choice_for(installed)
        if self.args.no_tool:
            tool = None
        components = [] if self.args.no_options else None
        after = rec.settings(a.current, tool or _keep(), components, replace=self.args.replace)
        if after == a.current:
            self.say("Nothing to change; Steam already has these settings.")
            return
        plan = self.core.plan(inst, acct, a.game, a.game.appid, after, note=f"ProtonDB {rec.confidence} confidence")
        self._show_plan(plan)
        if not _confirm("Save these settings to Steam?", self.args.yes):
            raise CliError("Cancelled.")
        self.ensure_steam_closed(inst)
        entry = self.core.apply(plan)
        self.say(f"Saved. Undo any time with: autoproton --cli revert {a.game.appid} --entry {entry.id}")

    def _progress(self, stage, done, total):
        if stage == "download" and total:
            print(f"\r  downloading {done * 100 // total:3d}%", end="", file=self.out, flush=True)

    def _show_plan(self, plan):
        self.say(f"{plan.game}:")
        self.show_settings("Before", plan.before)
        self.show_settings("After ", plan.after)

    def cmd_history(self):
        inst = self.install()
        acct = self.account(inst)
        entries = self.core.history(inst, acct, self.args.appid)
        if not entries:
            self.say("No changes recorded yet.")
        for e in entries:
            self.say(f"{e.id}  {_when(e.ts)}  {e.action:12} {e.game}  [{e.mark}]")
            if e.appid is not None:
                self.say(f"    before: {e.before.describe_tool()} | {e.before.describe_options()}")
                self.say(f"    after:  {e.after.describe_tool()} | {e.after.describe_options()}")

    def _entry(self, inst, acct):
        entries = self.core.history(inst, acct, self.args.appid)
        if self.args.entry:
            entry = next((e for e in entries if e.id == self.args.entry), None)
            if entry is None:
                raise CliError(f"No history entry {self.args.entry} for app {self.args.appid}.")
            return entry
        if getattr(self.args, "last_good", False):
            entry = next((e for e in entries if e.mark == "works"), None)
            if entry is None:
                raise CliError("No change for this game is marked as working.")
            return entry
        if not entries:
            raise CliError(f"autoproton hasn't changed app {self.args.appid}.")
        return entries[0]

    def cmd_revert(self):
        inst = self.install()
        acct = self.account(inst)
        entry = self._entry(inst, acct)
        restore = bool(getattr(self.args, "last_good", False))
        plan = self.core.revert_plan(inst, acct, entry, restore=restore)
        if plan.before != entry.after and not restore:
            self.say("Note: this game's settings were changed outside autoproton since then.")
        self._show_plan(plan)
        if plan.before == plan.after:
            self.say("Nothing to change.")
            return
        if not _confirm("Put these settings back?", self.args.yes):
            raise CliError("Cancelled.")
        self.ensure_steam_closed(inst)
        self.core.apply(plan)
        self.say("Done.")

    def cmd_mark(self):
        self.core.mark(self.args.entry, self.args.mark)
        self.say(f"Marked {self.args.entry} as {self.args.mark}.")

    def cmd_backups(self):
        sets = self.core.backups()
        if not sets:
            self.say("No backups yet.")
        for s in sets:
            files = ", ".join(f"{f.label} ({f.size} bytes)" for f in s.files)
            what = f" for {s.game}" if s.game else ""
            self.say(f"{s.id}  {_when(s.created)}  {s.reason}{what}: {files}")

    def cmd_restore_file(self):
        inst = self.install()
        acct = self.account(inst)
        backup = self.core.store.backups.get(self.args.set_id)
        labels = [f.label for f in backup.files] if not self.args.file else [self.args.file]
        self.say("Restoring a whole file undoes every change Steam made to it since the backup "
                 "(playtime, other games' settings). The current file is backed up first.")
        if not _confirm(f"Restore {', '.join(labels)} from {backup.id}?", self.args.yes):
            raise CliError("Cancelled.")
        self.ensure_steam_closed(inst)
        for label in labels:
            self.core.restore_file(inst, acct, backup.id, label)
            self.say(f"Restored {label}.")


def _keep():
    from .recommend.engine import ToolChoice

    return ToolChoice("keep", None, None, "No change")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autoproton --cli", description="Best Proton settings from ProtonDB, from the terminal.")
    p.add_argument("--account", help="Steam account id or name (default: the most recent one)")
    p.add_argument("--version", action="version", version=f"autoproton {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="show Steam, account and device")
    s.add_argument("appid", nargs="?", type=int)
    sub.add_parser("games", help="list installed games and their current settings")

    s = sub.add_parser("recommend", help="show what ProtonDB suggests for a game (changes nothing)")
    s.add_argument("appid", type=int)
    s.add_argument("--json", action="store_true")
    s.add_argument("--all", action="store_true", help="also list hidden launch options")
    s.add_argument("--refresh", action="store_true", help="ignore cached reports")

    s = sub.add_parser("apply", help="apply the recommended settings to a game")
    s.add_argument("appid", type=int)
    s.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    s.add_argument("--close-steam", action="store_true", help="close Steam if it's running")
    s.add_argument("--download-ge", action="store_true", help="download GE-Proton if it's recommended")
    s.add_argument("--replace", action="store_true", help="replace launch options instead of adding to them")
    s.add_argument("--no-tool", action="store_true", help="leave the Proton version alone")
    s.add_argument("--no-options", action="store_true", help="leave launch options alone")
    s.add_argument("--refresh", action="store_true")

    s = sub.add_parser("history", help="list changes autoproton made")
    s.add_argument("appid", nargs="?", type=int)

    for name, helptext in (("revert", "undo a change (the latest one for the game by default)"),):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("appid", type=int)
        s.add_argument("--entry", help="history entry id to undo")
        s.add_argument("--last-good", action="store_true", help="re-apply the latest change marked as working")
        s.add_argument("--yes", "-y", action="store_true")
        s.add_argument("--close-steam", action="store_true")

    s = sub.add_parser("mark", help="mark a change as working or not")
    s.add_argument("entry")
    s.add_argument("mark", choices=["works", "broken", "untested"])

    sub.add_parser("backups", help="list whole-file backups")
    s = sub.add_parser("restore-file", help="put back a whole Steam file from a backup")
    s.add_argument("set_id")
    s.add_argument("--file", choices=["localconfig", "config"])
    s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--close-steam", action="store_true")
    return p


def main(argv=None, core: Core | None = None, out=None) -> int:
    args = build_parser().parse_args(argv)
    app = App(args, core, out)
    try:
        msg = app.core.recover()
        if msg:
            app.say(msg)
        getattr(app, "cmd_" + args.command.replace("-", "_"))()
        return 0
    except KeyboardInterrupt:
        return 130
    except (CliError, NoSteam, TransactionError, ProtonDBError, GEError, BackupError, KeyError, ValueError) as e:
        print(f"autoproton: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
