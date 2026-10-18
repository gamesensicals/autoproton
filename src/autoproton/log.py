"""A log file at ~/.local/state/autoproton/autoproton.log, for bug reports."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

log = logging.getLogger("autoproton")


def setup(state_dir: Path) -> Path | None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        path = state_dir / "autoproton.log"
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return path
