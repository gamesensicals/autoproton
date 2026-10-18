import hashlib
import io
import tarfile
import threading

import pytest
from fakenet import FakeNet
from fakesteam import GE_VDF

from autoproton import ge_proton
from autoproton.net import Cancelled

TAG = "GE-Proton10-99"


def build_tar(top=TAG, extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data=b"", mode=0o644, kind=tarfile.REGTYPE, link=""):
            info = tarfile.TarInfo(name)
            info.type, info.mode, info.linkname = kind, mode, link
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)

        add(f"{top}", kind=tarfile.DIRTYPE, mode=0o755)
        add(f"{top}/compatibilitytool.vdf", GE_VDF.replace("{name}", top).encode())
        add(f"{top}/proton", b"#!/usr/bin/env python3\n", mode=0o755)
        add(f"{top}/files", kind=tarfile.DIRTYPE, mode=0o755)
        add(f"{top}/files/lib.so.1", b"ELF")
        add(f"{top}/files/lib.so", kind=tarfile.SYMTYPE, link="lib.so.1")
        for e in extra or []:
            add(**e)
    return buf.getvalue()


def release_json(size):
    return {"tag_name": TAG, "assets": [
        {"name": f"{TAG}.tar.gz", "browser_download_url": "https://dl/ge.tar.gz", "size": size},
        {"name": f"{TAG}.sha512sum", "browser_download_url": "https://dl/ge.sha512sum", "size": 150},
    ]}


def net_for(tar_bytes, checksum=None):
    digest = checksum or hashlib.sha512(tar_bytes).hexdigest()
    return FakeNet({
        ge_proton.LATEST_URL: release_json(len(tar_bytes)),
        "https://dl/ge.sha512sum": f"{digest}  {TAG}.tar.gz\n".encode(),
        "https://dl/ge.tar.gz": tar_bytes,
    })


def test_install_verifies_and_unpacks(tmp_path):
    data = build_tar()
    net = net_for(data)
    rel = ge_proton.latest_release(net)
    assert rel.tag == TAG and rel.size == len(data)
    stages = []
    tool = ge_proton.install(net, rel, tmp_path / "compatibilitytools.d", lambda s, d, t: stages.append(s))
    assert tool.internal_name == TAG and tool.ge_version == (10, 99, 0)
    folder = tmp_path / "compatibilitytools.d" / TAG
    assert (folder / "proton").stat().st_mode & 0o111
    assert (folder / "files/lib.so").is_symlink()
    assert {"download", "verify", "extract", "done"} <= set(stages)
    leftovers = [p.name for p in (tmp_path / "compatibilitytools.d").iterdir() if p.name.startswith(".")]
    assert leftovers == []
    # second install is a no-op
    net.calls.clear()
    assert ge_proton.install(net, rel, tmp_path / "compatibilitytools.d").internal_name == TAG
    assert net.calls == []


def test_bad_checksum_installs_nothing(tmp_path):
    data = build_tar()
    net = net_for(data, checksum="0" * 128)
    with pytest.raises(ge_proton.GEError, match="checksum"):
        ge_proton.install(net, ge_proton.latest_release(net), tmp_path / "c")
    assert list((tmp_path / "c").iterdir()) == []


@pytest.mark.parametrize("extra", [
    [{"name": "../evil", "data": b"x"}],
    [{"name": f"{TAG}/files/up", "kind": tarfile.SYMTYPE, "link": "../../../../etc/passwd"}],
    [{"name": f"{TAG}/files/abs", "kind": tarfile.SYMTYPE, "link": "/etc/passwd"}],
    [{"name": f"{TAG}/dev", "kind": tarfile.CHRTYPE}],
    [{"name": "second-top/x", "data": b"x"}],
])
def test_unsafe_archives_rejected(tmp_path, extra):
    data = build_tar(extra=extra)
    net = net_for(data)
    with pytest.raises(ge_proton.GEError):
        ge_proton.install(net, ge_proton.latest_release(net), tmp_path / "c")
    assert not (tmp_path / "c" / TAG).exists()
    assert not (tmp_path / "evil").exists()


def test_cancel_cleans_up(tmp_path):
    data = build_tar()
    net = net_for(data)
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelled):
        ge_proton.install(net, ge_proton.latest_release(net), tmp_path / "c", cancel=ev)
    assert list((tmp_path / "c").iterdir()) == []


def test_release_without_checksum_refused():
    net = FakeNet({ge_proton.LATEST_URL: {"tag_name": TAG, "assets": [
        {"name": f"{TAG}.tar.gz", "browser_download_url": "u", "size": 1}]}})
    with pytest.raises(ge_proton.GEError, match="checksum"):
        ge_proton.latest_release(net)


def test_rate_limit_message():
    net = FakeNet({ge_proton.LATEST_URL: (403, {})})
    with pytest.raises(ge_proton.GEError, match="limiting"):
        ge_proton.latest_release(net)
