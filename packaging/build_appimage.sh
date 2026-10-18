#!/usr/bin/env bash
# Build dist/autoproton-<version>-x86_64.AppImage.
#
#   python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
#   PYTHON=.venv/bin/python packaging/build_appimage.sh
#
# Build on the oldest distribution you want to support: the AppImage needs at
# least the glibc it was built with. CI uses Ubuntu 22.04 (glibc 2.35).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
ARCH="${ARCH:-x86_64}"
CACHE="$ROOT/packaging/.cache"
BUILD="$ROOT/build"
APPDIR="$BUILD/AppDir"
VERSION="$("$PYTHON" -c 'import autoproton; print(autoproton.__version__)')"
OUT="$ROOT/dist/autoproton-$VERSION-$ARCH.AppImage"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-$ARCH"

echo "==> PyInstaller"
rm -rf "$BUILD/dist" "$BUILD/work" "$APPDIR"
"$PYTHON" -m PyInstaller "$ROOT/packaging/autoproton.spec" --noconfirm --clean \
    --distpath "$BUILD/dist" --workpath "$BUILD/work" --log-level WARN

echo "==> Trimming the bundle"
"$PYTHON" "$ROOT/packaging/prune_bundle.py" "$BUILD/dist/autoproton"

echo "==> AppDir"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/scalable/apps" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$BUILD/dist/autoproton" "$APPDIR/usr/lib/autoproton"
ln -s ../lib/autoproton/autoproton "$APPDIR/usr/bin/autoproton"
install -m 755 "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
install -m 644 "$ROOT/packaging/autoproton.desktop" "$APPDIR/autoproton.desktop"
install -m 644 "$ROOT/packaging/autoproton.desktop" "$APPDIR/usr/share/applications/autoproton.desktop"
SVG="$ROOT/src/autoproton/resources/autoproton.svg"
install -m 644 "$SVG" "$APPDIR/usr/share/icons/hicolor/scalable/apps/autoproton.svg"
QT_QPA_PLATFORM=offscreen "$PYTHON" "$ROOT/packaging/render_icon.py" "$SVG" "$APPDIR/autoproton.png" 256
cp "$APPDIR/autoproton.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/autoproton.png"
ln -s autoproton.png "$APPDIR/.DirIcon"

echo "==> appimagetool"
mkdir -p "$CACHE" "$ROOT/dist"
[ -s "$CACHE/appimagetool" ] || curl -fsSL -o "$CACHE/appimagetool" "$TOOL_URL"
[ -s "$CACHE/runtime" ] || curl -fsSL -o "$CACHE/runtime" "$RUNTIME_URL"
chmod +x "$CACHE/appimagetool"
rm -f "$OUT"
# APPIMAGE_EXTRACT_AND_RUN lets appimagetool run without FUSE (containers, CI)
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$CACHE/appimagetool" --no-appstream \
    --runtime-file "$CACHE/runtime" "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "==> $OUT ($(du -h "$OUT" | cut -f1))"
