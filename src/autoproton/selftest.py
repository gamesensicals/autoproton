"""``autoproton --self-test``: check that the bundled pieces work on this system.

Used by CI on the built AppImage and handy for bug reports. Prints one line per
check and exits non-zero if any required check fails.
"""

from __future__ import annotations

import argparse
import os
import platform
import ssl
import sys

from . import __version__


class _OfflineNet:
    def get_json(self, url, headers=None):
        from .net import NetError

        raise NetError("offline self-test")

    get = get_json

    def download(self, *a, **k):
        from .net import NetError

        raise NetError("offline self-test")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autoproton --self-test")
    p.add_argument("--online", action="store_true", help="also reach ProtonDB")
    p.add_argument("--screenshot", help="save a picture of the main window to this PNG file")
    args = p.parse_args(argv)

    failures = 0

    def check(name, ok, detail="", required=True):
        nonlocal failures
        status = "ok  " if ok else ("FAIL" if required else "warn")
        if not ok and required:
            failures += 1
        print(f"[{status}] {name}{': ' + detail if detail else ''}", flush=True)

    frozen = bool(getattr(sys, "frozen", False))
    check("autoproton", True, f"{__version__} on Python {platform.python_version()}"
          f"{' (bundled)' if frozen else ''}, {platform.system()} {platform.machine()}")

    from .net import ssl_context

    try:
        ctx = ssl_context(os.environ)
        check("TLS certificates", ctx.cert_store_stats().get("x509_ca", 0) > 0 or bool(ssl.get_default_verify_paths().cafile),
              f"{ctx.cert_store_stats().get('x509_ca', 0)} CA certificates loaded")
    except Exception as e:  # noqa: BLE001
        check("TLS certificates", False, str(e))

    from .context import AppContext
    from .core import Core
    from .steam.process import clean_env

    env = clean_env(os.environ)
    bundle_dirs = [d for d in (getattr(sys, "_MEIPASS", None), os.environ.get("APPDIR")) if d]
    leaked = [d for d in bundle_dirs if d in env.get("LD_LIBRARY_PATH", "")]
    check("clean environment for Steam and the browser", not leaked,
          "LD_LIBRARY_PATH would point into the bundle" if leaked else "no bundled library paths are passed on")

    actx = AppContext.default()
    if not args.online:
        actx.net = _OfflineNet()
    core = Core(actx)
    installs = core.installs()
    check("Steam", True, f"found {len(installs)} install(s)" + (f": {installs[0].label}" if installs else ""),
          required=False)
    check("device", True, core.hardware().describe(), required=False)

    if args.online:
        try:
            counts = core.protondb.counts(refresh=True)
            check("ProtonDB", True, f"{counts.get('reports')} reports available")
        except Exception as e:  # noqa: BLE001
            check("ProtonDB", False, str(e))

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from .gui.main_window import MainWindow
        from .gui.workers import SyncRunner

        app = QApplication.instance() or QApplication([sys.argv[0]])
        window = MainWindow(core, SyncRunner())
        window.show()
        window.load()
        QTimer.singleShot(300, app.quit)
        app.exec()
        if args.screenshot:
            window.grab().save(args.screenshot)
        window.close()
        check("Qt window", True, f"{app.platformName()} platform")
    except Exception as e:  # noqa: BLE001
        check("Qt window", False, f"{type(e).__name__}: {e}")

    print("all checks passed" if not failures else f"{failures} check(s) failed")
    return 1 if failures else 0
