"""Entry point: the window by default, ``--cli`` for the terminal, ``--self-test`` for diagnostics."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--cli":
        from .cli import main as cli_main

        return cli_main(args[1:])
    if args and args[0] == "--self-test":
        from .selftest import main as selftest_main

        return selftest_main(args[1:])
    if args and args[0] in ("--version", "-V"):
        from . import __version__

        print(f"autoproton {__version__}")
        return 0
    if args and args[0] in ("--help", "-h"):
        print("usage: autoproton            open the window\n"
              "       autoproton --cli …    use it from the terminal (autoproton --cli --help)\n"
              "       autoproton --self-test [--online]   check that everything loads")
        return 0
    from .gui.app import main as gui_main

    return gui_main([sys.argv[0], *args])


if __name__ == "__main__":
    sys.exit(main())
