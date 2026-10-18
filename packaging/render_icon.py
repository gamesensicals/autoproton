"""Render the SVG icon to a PNG with Qt (used for .DirIcon and the hicolor theme)."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

src, dst, size = sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 256
app = QGuiApplication(["render", "-platform", "offscreen"])
img = QImage(size, size, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
painter = QPainter(img)
QSvgRenderer(src).render(painter)
painter.end()
if not img.save(dst):
    sys.exit(f"could not write {dst}")
