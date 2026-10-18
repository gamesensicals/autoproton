"""Trim the PyInstaller folder before it goes into the AppImage.

Two rules:
1. Libraries that must match the host's drivers and core system (OpenGL,
   X11/Wayland, fonts, glib, libstdc++ ...) are removed, so the host's own
   copies are used. Bundling old copies of these is the classic way an
   AppImage breaks on newer distributions.
2. Everything else is kept only if something the app actually loads needs it.

    python prune_bundle.py build/dist/autoproton
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

HOST = re.compile(
    r"^(ld-linux.*|libc|libm|libdl|libpthread|librt|libutil|libresolv|libnsl|libstdc\+\+|libgcc_s"
    r"|libGL|libEGL|libGLX|libGLdispatch|libOpenGL|libGLES\w*|libdrm|libgbm|libvulkan|libwayland-\w+"
    r"|libX11|libX11-xcb|libxcb|libXau|libXdmcp|libXext|libXrender|libXi|libXfixes|libXcursor|libXrandr"
    r"|libXinerama|libXcomposite|libXdamage|libxshmfence|libfontconfig|libfreetype|libharfbuzz|libpng16"
    r"|libexpat|libz|libglib-2\.0|libgobject-2\.0|libgio-2\.0|libgmodule-2\.0|libgthread-2\.0|libpcre2-8"
    r"|libdbus-1|libsystemd|libudev|libselinux|libmount|libblkid|libcap|libgcrypt|libgpg-error|liblz4"
    r"|libcom_err|libgssapi_krb5|libk5crypto|libkrb5|libkrb5support|libkeyutils|libasound|libbsd|libmd"
    r"|libuuid|libbrotli\w*)\.so(\.[0-9.]+)?$"
)

# Qt plugins the app doesn't need. The GTK theme plugin alone drags in GTK 3, cairo and pango.
DROP = [
    "PySide6/Qt/plugins/platformthemes/libqgtk3.so",
    "PySide6/Qt/plugins/platforms/libqeglfs.so",
    "PySide6/Qt/plugins/platforms/libqlinuxfb.so",
    "PySide6/Qt/plugins/platforms/libqminimal.so",
    "PySide6/Qt/plugins/platforms/libqminimalegl.so",
    "PySide6/Qt/plugins/platforms/libqvkkhrdisplay.so",
    "PySide6/Qt/plugins/platforms/libqvnc.so",
    "PySide6/Qt/plugins/egldeviceintegrations",
    "PySide6/Qt/plugins/generic",
    "PySide6/Qt/plugins/wayland-shell-integration/libivi-shell.so",
    "PySide6/Qt/plugins/wayland-shell-integration/libfullscreen-shell-v1.so",
    "PySide6/Qt/plugins/wayland-shell-integration/libqt-shell.so",
    "PySide6/Qt/plugins/wayland-shell-integration/libwl-shell-plugin.so",
    "PySide6/Qt/plugins/wayland-graphics-integration-client/libvulkan-server.so",
    "PySide6/Qt/translations",
]
KEEP_IMAGEFORMATS = {"libqsvg.so", "libqico.so"}


def needed(path: Path) -> list[str]:
    out = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", out)


def is_elf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def main(bundle: Path) -> None:
    internal = bundle / "_internal"
    for rel in DROP:
        p = internal / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()
    imgdir = internal / "PySide6/Qt/plugins/imageformats"
    for p in imgdir.glob("*.so"):
        if p.name not in KEEP_IMAGEFORMATS:
            p.unlink()

    files = [p for p in internal.rglob("*") if (p.is_file() or p.is_symlink()) and ".so" in p.name]
    by_name: dict[str, list[Path]] = {}
    for p in files:
        by_name.setdefault(p.name, []).append(p)

    def is_module(p: Path) -> bool:
        return p.name.endswith(".abi3.so") or ".cpython-" in p.name or "/plugins/" in str(p)

    roots = [bundle / "autoproton"] + [p for p in files if is_module(p) and not p.is_symlink()]
    roots += [p for p in files if p.name.startswith(("libpython", "libpyside6", "libshiboken6"))]
    keep: set[Path] = set()  # paths (files and symlinks) to keep
    seen: set[Path] = set()  # real files whose dependencies were already read
    host, missing = set(), set()
    queue = list(roots)
    while queue:
        cur = queue.pop()
        keep.add(cur)
        real = cur.resolve()
        keep.add(real)
        if real in seen or not is_elf(real):
            continue
        seen.add(real)
        for name in needed(real):
            if HOST.match(name):
                host.add(name)
            elif name in by_name:
                queue.extend(by_name[name])
            else:
                missing.add(name)

    removed, saved = [], 0
    for p in files:
        drop = HOST.match(p.name) or p not in keep
        if drop and (p.exists() or p.is_symlink()):
            saved += 0 if p.is_symlink() else p.stat().st_size
            removed.append(str(p.relative_to(internal)))
            p.unlink()
    print(f"pruned {len(removed)} files ({saved / 1e6:.1f} MB)")
    print("from the host:", " ".join(sorted(host)))
    if missing:
        print("not bundled and not on the host list (must exist on the host):", " ".join(sorted(missing)))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
