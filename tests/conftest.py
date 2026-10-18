import os
import socket
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fakesteam import make_default_steam  # noqa: E402

from autoproton.context import AppContext  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never reach the internet; HTTP goes through FakeNet."""

    real_connect = socket.socket.connect

    def guard(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError("tests must not open network connections")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def ctx(home, tmp_path):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    sys_root = tmp_path / "sysroot"
    (sys_root / "proc").mkdir(parents=True)
    c = AppContext.default(env)
    c.sys_root = sys_root
    c.which = lambda name: None
    c.spawned = []

    def spawn(argv, env):
        c.spawned.append((list(argv), dict(env)))

    c.spawn = spawn
    c.sleep = lambda s: None
    return c


@pytest.fixture
def steam(home):
    return make_default_steam(home)
