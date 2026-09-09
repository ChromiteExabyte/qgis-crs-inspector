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


#: Set this before running from the console if the default is wrong.
#: The console has no __file__, and deriving the root from the open project put
#: output under <repo>/fixture/docs/ — the fixture lives in a subdirectory, so
#: the "repo root" guess was one level too deep. An explicit path is one line
#: and cannot be silently wrong.
OUTPUT_DIR = ""


def _output_dir():
    if OUTPUT_DIR:
        return OUTPUT_DIR
    here = globals().get("__file__")
    if here:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(here))), "docs")
    raise SystemExit(
        "Set OUTPUT_DIR at the top of this script before running it from the "
        "QGIS console - __file__ is unavailable there, and guessing the "
        "repository root from the open project writes to the wrong place.")


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

    out_dir = _output_dir()
    os.makedirs(out_dir, exist_ok=True)

    original_size = panel.size()
    was_floating = panel.isFloating()          # restore what was there, not a guess
    panel.setFloating(True)
    try:
        for width in WIDTHS:
            panel.resize(width, HEIGHT)
            QApplication.processEvents()
            shot = panel.grab()
            path = os.path.join(out_dir, "panel-{}.png".format(width))
            if shot.save(path):
                # Report what was actually captured: a widget can refuse to
                # shrink below its layout minimum, so the requested width is not
                # evidence of the width on disk.
                print("wrote {}  ({}x{} actual)".format(
                    path, shot.width(), shot.height()))
            else:
                print("failed to write {}".format(path))
    finally:
        panel.resize(original_size)
        panel.setFloating(was_floating)
        QApplication.processEvents()

    print("\nCheck each one for a horizontal scrollbar. There should be none at "
          "any width — that was the defect these widths exist to catch.")


capture()
