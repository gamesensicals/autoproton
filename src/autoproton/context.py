"""Everything the core touches on the outside world, in one place so tests can swap it."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


def _xdg(env: Mapping[str, str], var: str, home: Path, default: str) -> Path:
    value = env.get(var, "")
    base = Path(value) if value and os.path.isabs(value) else home / default
    return base / "autoproton"


def default_spawn(argv: Sequence[str], env: Mapping[str, str]) -> None:
    """Start a program detached from us (Steam, xdg-open)."""
    subprocess.Popen(
        list(argv),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


@dataclass
class AppContext:
    home: Path
    data_dir: Path
    cache_dir: Path
    state_dir: Path
    env: Mapping[str, str]
    sys_root: Path = Path("/")
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    which: Callable[[str], str | None] = shutil.which
    spawn: Callable[[Sequence[str], Mapping[str, str]], None] = default_spawn
    steam_root_override: Path | None = None
    net: object | None = field(default=None, repr=False)
    own_pid: int = field(default_factory=os.getpid)

    @classmethod
    def default(cls, env: Mapping[str, str] | None = None) -> AppContext:
        env = dict(os.environ if env is None else env)
        home = Path(env.get("HOME") or os.path.expanduser("~"))
        override = env.get("AUTOPROTON_STEAM_ROOT")
        return cls(
            home=home,
            data_dir=_xdg(env, "XDG_DATA_HOME", home, ".local/share"),
            cache_dir=_xdg(env, "XDG_CACHE_HOME", home, ".cache"),
            state_dir=_xdg(env, "XDG_STATE_HOME", home, ".local/state"),
            env=env,
            steam_root_override=Path(override).expanduser() if override else None,
        )

    def get_net(self):
        if self.net is None:
            from .net import Net

            self.net = Net(env=self.env)
        return self.net
