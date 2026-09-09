"""Capture panel screenshots for the README — from a real desktop QGIS.

This cannot run headless. Offscreen Qt reports **zero font families**, so every
grab taken without a display renders as tofu boxes. Real screenshots are an
output of the first-load acceptance session, not something the build can make.

Run it from the QGIS Python console (Plugins -> Python Console) with the
acceptance fixture open and CRS Inspector enabled:

    exec(open(r"<repo>/tools/capture_screenshots.py", encoding="utf-8").read())

Writes docs/panel-<width>.png at three dock widths, because the layout has to
survive a narrow dock — an earlier version produced a horizontal scrollbar at
420px and pushed two columns off-screen entirely.
"""

from __future__ import annotations

import os

WIDTHS = [380, 480, 620]
HEIGHT = 360


def _repo_root():
    # The console has no __file__; fall back to the project's directory.
    here = globals().get("__file__")
    if here:
        return os.path.dirname(os.path.dirname(os.path.abspath(here)))
    from qgis.core import QgsProject
    return os.path.dirname(QgsProject.instance().fileName()) or os.getcwd()


def _find_panel():
    from qgis.PyQt.QtWidgets import QDockWidget
    from qgis.utils import iface
    for dock in iface.mainWindow().findChildren(QDockWidget):
        if dock.objectName() == "CrsInspectorPanel":
            return dock
    return None


def capture():
    from qgis.PyQt.QtWidgets import QApplication

    panel = _find_panel()
    if panel is None:
        print("CRS Inspector dock not found — enable the plugin and open it first.")
        return

    out_dir = os.path.join(_repo_root(), "docs")
    os.makedirs(out_dir, exist_ok=True)

    original = panel.size()
    panel.setFloating(True)
    try:
        for width in WIDTHS:
            panel.resize(width, HEIGHT)
            QApplication.processEvents()
            path = os.path.join(out_dir, "panel-{}.png".format(width))
            if panel.grab().save(path):
                print("wrote {}".format(path))
            else:
                print("failed to write {}".format(path))
    finally:
        panel.resize(original)
        panel.setFloating(False)
        QApplication.processEvents()

    print("\nCheck each one for a horizontal scrollbar. There should be none at "
          "any width — that was the defect these widths exist to catch.")


capture()
