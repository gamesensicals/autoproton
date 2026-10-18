"""Write a file so that it is either fully old or fully new, never half-written."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class ConcurrentModification(RuntimeError):
    """The file changed on disk after we read it."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str | None:
    try:
        return sha256(Path(path).read_bytes())
    except FileNotFoundError:
        return None


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, expected_sha256: str | None = None, check: bool = False) -> None:
    """Replace ``path`` with ``data`` via a temp file and rename.

    Symlinks are followed, so writing to ``~/.steam/steam/config/config.vdf``
    updates the real file rather than replacing the link. With ``check``, the
    write is refused if the current contents no longer hash to
    ``expected_sha256`` (None meaning "the file must not exist").
    """
    real = Path(os.path.realpath(path))
    if check and file_sha256(real) != expected_sha256:
        raise ConcurrentModification(f"{real} changed while autoproton was working")
    try:
        mode = real.stat().st_mode & 0o7777
    except FileNotFoundError:
        mode = 0o644
    real.parent.mkdir(parents=True, exist_ok=True)
    tmp = real.with_name(f".{real.name}.autoproton-{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, real)
        _fsync_dir(real.parent)
    finally:
        if tmp.exists():
            tmp.unlink()
