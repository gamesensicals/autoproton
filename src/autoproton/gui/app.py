"""Start the desktop app."""

from __future__ import annotations

import sys

from .. import __version__


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:  # pragma: no cover - depends on the system
        print(
            f"autoproton: the window can't open because Qt failed to load ({e}).\n"
            "On Debian/Ubuntu: sudo apt install libegl1 libxkbcommon-x11-0 libxcb-cursor0\n"
            "On Fedora:        sudo dnf install libglvnd-egl libxkbcommon-x11 xcb-util-cursor\n"
            "The command-line version still works: autoproton --cli --help",
            file=sys.stderr,
        )
        return 1

    from ..core import Core
    from ..log import setup
    from .common import app_icon
    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("autoproton")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("autoproton")
    app.setWindowIcon(app_icon())
    core = Core()
    setup(core.ctx.state_dir)
    window = MainWindow(core)
    window.show()
    window.start()
    code = app.exec()
    window.runner.wait(5000)
    return code
