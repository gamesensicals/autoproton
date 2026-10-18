"""Download, verify and install GE-Proton from its official GitHub releases."""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .cache import Cache
from .models import CompatTool
from .net import Cancelled, HttpError, Net, NetError
from .steam.compat import ge_version, read_compatibilitytool_vdf

REPO = "GloriousEggroll/proton-ge-custom"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"

Progress = Callable[[str, int, int], None]  # (stage, done, total)


class GEError(RuntimeError):
    """Message is meant for the user."""


@dataclass(frozen=True)
class Release:
    tag: str
    tarball_name: str
    tarball_url: str
    size: int
    checksum_url: str

    @property
    def size_mb(self) -> int:
        return round(self.size / 1e6)


def latest_release(net: Net, cache: Cache | None = None) -> Release:
    data = cache.get("ge-latest", 3600) if cache else None
    if data is None:
        try:
            data = net.get_json(LATEST_URL, {"Accept": "application/vnd.github+json"})
        except HttpError as e:
            if e.status in (403, 429):
                raise GEError("GitHub is limiting requests right now; try again in an hour.") from e
            raise GEError(f"Couldn't look up the latest GE-Proton: {e}") from e
        except NetError as e:
            raise GEError(f"Couldn't reach GitHub: {e}") from e
        if cache:
            cache.put("ge-latest", data)
    try:
        tag = str(data["tag_name"])
        assets = {a["name"]: a for a in data["assets"]}
    except (KeyError, TypeError) as e:
        raise GEError("GitHub's answer about GE-Proton didn't look as expected.") from e
    tarball = next((a for n, a in assets.items() if n.endswith(".tar.gz")), None)
    checksum = next((a for n, a in assets.items() if n.endswith(".sha512sum")), None)
    if tarball is None or checksum is None:
        raise GEError(f"GE-Proton {tag} has no download with a checksum; not installing it.")
    return Release(tag, tarball["name"], tarball["browser_download_url"], int(tarball.get("size") or 0),
                   checksum["browser_download_url"])


def _expected_sha512(text: str, filename: str) -> str:
    lines = [ln.split() for ln in text.splitlines() if ln.strip()]
    for parts in lines:
        if len(parts) >= 2 and parts[-1].lstrip("*").endswith(filename):
            return parts[0].lower()
    if len(lines) == 1 and lines[0]:
        return lines[0][0].lower()
    raise GEError("The GE-Proton checksum file couldn't be read.")


def _check_members(tar: tarfile.TarFile) -> str:
    """Refuse anything that would land outside the target folder. Returns the single top-level folder."""
    tops = set()
    for m in tar.getmembers():
        p = PurePosixPath(m.name)
        if p.is_absolute() or ".." in p.parts:
            raise GEError(f"The GE-Proton archive contains an unsafe path ({m.name}); not installing it.")
        if m.isdev() or m.isfifo():
            raise GEError("The GE-Proton archive contains device files; not installing it.")
        if m.issym() or m.islnk():
            target = PurePosixPath(m.linkname)
            base = p.parent if m.issym() else PurePosixPath()
            resolved = []
            for part in (base / target).parts if not target.is_absolute() else ("/",):
                if part == "/":
                    raise GEError(f"The GE-Proton archive links outside itself ({m.name}); not installing it.")
                if part == "..":
                    if not resolved:
                        raise GEError(f"The GE-Proton archive links outside itself ({m.name}); not installing it.")
                    resolved.pop()
                elif part != ".":
                    resolved.append(part)
        if p.parts:
            tops.add(p.parts[0])
    if len(tops) != 1:
        raise GEError("The GE-Proton archive isn't laid out as expected; not installing it.")
    return tops.pop()


class _Hashing:
    def __init__(self, f):
        self.f = f
        self.h = hashlib.sha512()

    def write(self, b):
        self.h.update(b)
        return self.f.write(b)


def install(
    net: Net,
    release: Release,
    compat_dir: Path,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> CompatTool:
    """Install ``release`` into ``compat_dir`` (Steam's compatibilitytools.d)."""
    compat_dir = Path(compat_dir)
    report = progress or (lambda stage, done, total: None)
    existing = _find_installed(compat_dir, release.tag)
    if existing:
        return existing
    compat_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(compat_dir).free
    if release.size and free < release.size * 3.5:
        raise GEError(f"Not enough free disk space for GE-Proton (needs about {release.size * 3.5 / 1e9:.1f} GB).")

    part = compat_dir / f".autoproton-{release.tag}.tar.gz.part"
    staging = compat_dir / f".autoproton-extract-{release.tag}"
    try:
        report("checksum", 0, 0)
        try:
            expected = _expected_sha512(net.get(release.checksum_url).body.decode("utf-8", "replace"), release.tarball_name)
            with open(part, "wb") as f:
                hashing = _Hashing(f)
                net.download(release.tarball_url, hashing, lambda d, t: report("download", d, t or release.size), cancel)
        except NetError as e:
            raise GEError(f"Downloading GE-Proton failed: {e}") from e
        report("verify", 0, 0)
        if hashing.h.hexdigest() != expected:
            raise GEError("The GE-Proton download didn't match its published checksum, so it was thrown away.")

        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        with tarfile.open(part, "r:gz") as tar:
            top = _check_members(tar)
            members = tar.getmembers()
            for i, m in enumerate(members):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if hasattr(tarfile, "data_filter"):
                    tar.extract(m, staging, filter="data")
                else:  # older Pythons: members were already checked above
                    tar.extract(m, staging)
                if i % 200 == 0:
                    report("extract", i, len(members))
        unpacked = staging / top
        if not (unpacked / "compatibilitytool.vdf").is_file() or not (unpacked / "proton").exists():
            raise GEError("The GE-Proton archive is missing files Steam needs; not installing it.")
        final = compat_dir / top
        if final.exists():
            shutil.rmtree(unpacked, ignore_errors=True)
        else:
            os.rename(unpacked, final)
        report("done", 1, 1)
        tool = _tool_from(final)
        if tool is None:
            raise GEError("GE-Proton was unpacked but its compatibilitytool.vdf couldn't be read.")
        return tool
    finally:
        part.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def _tool_from(folder: Path) -> CompatTool | None:
    names = read_compatibilitytool_vdf(folder / "compatibilitytool.vdf")
    if not names:
        return None
    internal, shown = names[0]
    return CompatTool(internal, shown, "custom", folder, ge_version(internal) or ge_version(shown))


def _find_installed(compat_dir: Path, tag: str) -> CompatTool | None:
    folder = compat_dir / tag
    if (folder / "compatibilitytool.vdf").is_file():
        return _tool_from(folder)
    return None
