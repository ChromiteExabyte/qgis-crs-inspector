"""Drive the plugin's real lifecycle: classFactory -> initGui -> action.

Not another standalone panel. The remaining §4 work lives in ownership and
subscriptions, and both are invisible to a test that constructs a panel
directly. So this enters where QGIS enters, uses real QGIS objects and real Qt
signals, and supplies only the host-interface methods the plugin actually calls.

Three tests it deliberately avoids, each able to pass while the real path is
broken:

  * calling `_invalidate()` directly     — proves the machinery, not that an
                                           edit reaches it
  * refreshing manually after the edit   — proves reassessment, not notification
  * checking only the verdict after the  — proves eventual correctness, not that
    timer runs                             the stale result was ever disowned

    "<OSGeo4W>/bin/python-qgis.bat" tools/lifecycle_check.py

Exit code 0 on success.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES = []


def check(condition, message):
    print("  {}  {}".format("ok  " if condition else "FAIL", message))
    if not condition:
        FAILURES.append(message)


class _Bar:
    def pushInfo(self, title, message):
        pass


class _Iface:
    """Only what the plugin calls. A wider fake would hide a missing call."""

    def __init__(self, window):
        self._window = window
        self.docks = []
        self.menu_actions = []
        self.toolbar_actions = []

    def mainWindow(self):
        return self._window

    def messageBar(self):
        return _Bar()

    def addPluginToMenu(self, menu, action):
        self.menu_actions.append((menu, action))

    def removePluginMenu(self, menu, action):
        self.menu_actions = [x for x in self.menu_actions if x[1] is not action]

    def addToolBarIcon(self, action):
        self.toolbar_actions.append(action)

    def removeToolBarIcon(self, action):
        self.toolbar_actions = [a for a in self.toolbar_actions if a is not action]

    def addDockWidget(self, area, dock):
        self.docks.append(dock)
        self._window.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        self.docks = [d for d in self.docks if d is not dock]
        self._window.removeDockWidget(dock)


def main() -> int:
    from qgis.core import (
        QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry,
        QgsPointXY, QgsProject, QgsVectorLayer, Qgis,
    )
    from qgis.PyQt.QtCore import QT_VERSION_STR
    from qgis.PyQt.QtWidgets import QApplication, QMainWindow

    app = QgsApplication([], True)
    prefix = os.environ.get("QGIS_PREFIX_PATH") or (
        os.path.join(os.environ["OSGEO4W_ROOT"], "apps", "qgis")
        if os.environ.get("OSGEO4W_ROOT") else "/usr")
    QgsApplication.setPrefixPath(prefix, True)
    app.initQgis()
    print("qgis     {}  (Qt {})".format(Qgis.QGIS_VERSION, QT_VERSION_STR))

    try:
        project = QgsProject.instance()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))

        layer = QgsVectorLayer("Point?crs=EPSG:4326", "epoch_layer", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(-2.5, 51.8)))
        layer.dataProvider().addFeature(feature)
        layer.updateExtents()
        project.addMapLayer(layer)

        window = QMainWindow()
        window.show()          # the dock must really be visible; _recompute
        QApplication.processEvents()   # deliberately skips work while hidden
        iface = _Iface(window)

        import crs_inspector
        plugin = crs_inspector.classFactory(iface)
        plugin.initGui()

        print("\n-- entering through the real action --")
        check(bool(iface.menu_actions), "initGui registered a menu action")
        plugin.action.setChecked(True)
        plugin.action.triggered.emit(True)      # the action, not _show()
        QApplication.processEvents()
        panel = plugin.panel
        check(panel is not None, "the action opened the plugin's panel")
        if panel is None:
            return 1

        print("\n-- ownership at the FIRST probe --")
        check(panel._observer is plugin.observer,
              "the panel's observer is the plugin's authoritative observer")
        check(panel._history is plugin.history,
              "the first assessment went to the authoritative history")
        check(len(plugin.history) == 1,
              "exactly one baseline exists ({} found)".format(len(plugin.history)))
        first = plugin.observer.presentation
        check(first.is_current, "the first assessment is presented as current")
        first_observed_at = first.observed_at

        print("\n-- a real epoch edit, with reassessment held back --")
        plugin._timer.stop()                   # hold the debounced work
        history_before = len(plugin.history)

        changed = QgsCoordinateReferenceSystem(layer.crs())
        changed.setCoordinateEpoch(2025.0)
        # Assign it back. layer.crs() returns by value, so mutating that copy
        # would change nothing the layer knows about.
        layer.setCrs(changed)
        QApplication.processEvents()

        after = plugin.observer.presentation
        check(not after.is_current,
              "the displayed assessment stopped claiming currency")
        check(after.observed_at == first_observed_at,
              "the last successful observation time is unchanged")
        check(len(plugin.history) == history_before,
              "no newly assessed state was committed ({} -> {})".format(
                  history_before, len(plugin.history)))
        check("inputs changed" in panel.status.text().lower()
              or "not yet re-checked" in panel.status.text().lower(),
              "the panel says so on screen: {!r}".format(
                  panel.status.text().splitlines()[0]))
        check(plugin._timer.isActive(),
              "reassessment is scheduled, not run inline")

        print("\n-- releasing the reassessment --")
        plugin._recompute()
        QApplication.processEvents()
        released = plugin.observer.presentation
        check(released.is_current, "a successful recheck restores currency")
        check(released.observed_at != first_observed_at,
              "and carries a new observation time")

        print("\n-- edited while hidden: reopening must not resurrect it --")
        plugin.action.setChecked(False)
        plugin.action.triggered.emit(False)          # hide via the real action
        QApplication.processEvents()
        before_hidden_edit = plugin.observer.presentation.observed_at

        again = QgsCoordinateReferenceSystem(layer.crs())
        again.setCoordinateEpoch(2030.0)
        layer.setCrs(again)
        QApplication.processEvents()
        plugin._recompute()          # the timer fires, finds it hidden, does nothing
        QApplication.processEvents()
        check(not plugin.observer.presentation.is_current,
              "an edit made while hidden leaves the evidence stale")

        plugin.action.setChecked(True)
        plugin.action.triggered.emit(True)           # reopen
        QApplication.processEvents()
        reopened = plugin.observer.presentation
        check(reopened.is_current is False or reopened.observed_at != before_hidden_edit,
              "reopening never shows the pre-edit result as current")
        check(not plugin.panel.needs_assessment() or plugin._timer.isActive(),
              "reopening re-checked, or scheduled a re-check")

        print("\n-- retirement --")
        plugin._retire_session()
        check(not plugin.observer.presentation.has_evidence,
              "a retired session presents no evidence")
        check(not plugin._layer_subscriptions,
              "retirement released the per-layer subscriptions")

        print("\n-- unload --")
        plugin.unload()
        check(plugin.panel is None, "unload removed the dock")
        check(not plugin._layer_subscriptions, "unload released subscriptions")
        check(not iface.menu_actions and not iface.toolbar_actions,
              "unload removed its menu and toolbar entries")
    finally:
        app.exitQgis()

    print()
    if FAILURES:
        print("FAILED")
        for failure in FAILURES:
            print("  - {}".format(failure))
        return 1
    print("OK  the plugin's own lifecycle behaves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
