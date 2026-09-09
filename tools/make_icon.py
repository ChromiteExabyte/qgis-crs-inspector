"""Rasterise crs_inspector/icon.svg to the PNG the plugin manager wants.

`metadata.txt` requires a web-friendly image (PNG or JPEG), not an SVG, so the
source of truth stays the SVG and this regenerates the PNG from it.

Run with the QGIS python, which supplies Qt:

    "<OSGeo4W>/bin/python-qgis.bat" tools/make_icon.py

Fonts are unavailable offscreen (verified: zero font families), which is why the
icon is pure geometry — no text to fall back to tofu.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "crs_inspector" / "icon.svg"
SIZES = [(64, "icon.png"), (128, "icon@2x.png")]


def main() -> int:
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QImage, QPainter
    from qgis.PyQt.QtSvg import QSvgRenderer
    from qgis.PyQt.QtWidgets import QApplication

    if not SVG.exists():
        print("missing {}".format(SVG))
        return 1

    app = QApplication.instance() or QApplication([])
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        print("QSvgRenderer rejected the SVG")
        return 1

    for size, name in SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied
                       if hasattr(QImage, "Format") else QImage.Format_ARGB32_Premultiplied)
        image.fill(0)                       # transparent: works on any theme
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing
                                  if hasattr(QPainter, "RenderHint")
                                  else QPainter.Antialiasing, True)
        except Exception:
            pass
        renderer.render(painter)
        painter.end()

        out = ROOT / "crs_inspector" / name
        if not image.save(str(out), "PNG"):
            print("failed to write {}".format(out))
            return 1
        print("wrote {}  {}x{}  {:,} bytes".format(
            out.relative_to(ROOT), size, size, out.stat().st_size))

    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
