# PyInstaller recipe for the folder that goes inside the AppImage.
# Run through packaging/build_appimage.sh rather than directly.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    datas=[(os.path.join(ROOT, "src", "autoproton", "resources"), os.path.join("autoproton", "resources"))],
    hiddenimports=["PySide6.QtSvg"],
    excludes=["tkinter", "unittest", "pydoc_data", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtNetwork",
              "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtConcurrent", "PySide6.QtDBus"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="autoproton",
    console=True,
    strip=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="autoproton")
