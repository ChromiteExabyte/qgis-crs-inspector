"""Plugin entry point: wiring only. No logic lives here."""

from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QAction

from .core.diffing import History
from .core.observation import Observer
from .core.trace import Trace
from .ui.panel import CrsInspectorPanel
from .ui.qt_compat import RIGHT_DOCK_AREA

MENU = "&CRS Inspector"

# Project loading fires the invalidation signals in a burst, so coalesce them.
# Records are written once the recompute settles, never inline as things happen.
DEBOUNCE_MS = 150


class CrsInspectorPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.panel = None
        self.action = None
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._recompute)
        self._connected = False
        self.observer = Observer(session=self._session_token())
        self.history = History()
        self.trace = Trace(enabled=False)   # opt-in; counters run regardless

    @staticmethod
    def _session_token() -> str:
        """Identifies the project currently under observation.

        Results captured against one project must never be publishable into
        another, so the token changes whenever the project does.
        """
        project = QgsProject.instance()
        return "{}|{}".format(project.fileName() or "<unsaved>", id(project))

    # ---------------------------------------------------------------- QGIS API
    def initGui(self):
        self.action = QAction("CRS Inspector", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip(
            "Show which datum shift was applied to every layer — including the "
            "ones where none was available.")
        self.action.triggered.connect(self.toggle)
        self.iface.addPluginToMenu(MENU, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self._disconnect()
        self._timer.stop()
        if self.panel is not None:
            self.iface.removeDockWidget(self.panel)
            self.panel.deleteLater()
            self.panel = None
        if self.action is not None:
            self.iface.removePluginMenu(MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    # ------------------------------------------------------------------ panel
    def toggle(self, checked):
        if checked:
            self._show()
        elif self.panel is not None:
            self.panel.hide()

    def _show(self):
        if self.panel is None:
            self.panel = CrsInspectorPanel(self.iface, self.iface.mainWindow())
            self.panel.visibilityChanged.connect(self._on_visibility)
            self.iface.addDockWidget(RIGHT_DOCK_AREA, self.panel)
            self._connect()
        self.panel.show()
        self.panel.raise_()

    def _on_visibility(self, visible):
        if self.action is not None:
            self.action.setChecked(visible)

    # ----------------------------------------------------- invalidation wiring
    def _connect(self):
        if self._connected:
            return
        project = QgsProject.instance()
        for signal in (project.crsChanged,
                       project.transformContextChanged,
                       project.layersAdded,
                       project.layersRemoved):
            try:
                signal.connect(self._invalidate)
            except Exception:
                pass
        # Retire on aboutToBeCleared, not cleared. Verified ordering:
        #   aboutToBeCleared -> layersWillBeRemoved -> layersRemoved -> cleared
        # so retiring at the end would leave teardown's removal notifications
        # arriving against a live session, where they read as project edits.
        try:
            project.aboutToBeCleared.connect(self._retire_session)
        except Exception:
            pass
        # Both routes to "a project is ready": a file was read, and a brand new
        # empty project was created. Listening only for the read leaves
        # New Project -> Add Layer with no observer running.
        for signal_name in ("projectRead", "newProjectCreated"):
            try:
                getattr(self.iface, signal_name).connect(self._begin_session)
            except Exception:
                pass
        try:
            project.layerTreeRoot().visibilityChanged.connect(self._invalidate)
        except Exception:
            pass
        self._connected = True

    def _disconnect(self):
        if not self._connected:
            return
        project = QgsProject.instance()
        for signal in (project.crsChanged,
                       project.transformContextChanged,
                       project.layersAdded,
                       project.layersRemoved):
            try:
                signal.disconnect(self._invalidate)
            except Exception:
                pass
        try:
            project.aboutToBeCleared.disconnect(self._retire_session)
        except Exception:
            pass
        for signal_name in ("projectRead", "newProjectCreated"):
            try:
                getattr(self.iface, signal_name).disconnect(self._begin_session)
            except Exception:
                pass
        try:
            project.layerTreeRoot().visibilityChanged.disconnect(self._invalidate)
        except Exception:
            pass
        self._connected = False

    def _invalidate(self, *args):
        """A relevant input moved. Anything in flight is superseded."""
        self.observer.invalidate()
        self._timer.start()

    def _retire_session(self, *args):
        """Teardown has begun. Nothing in flight may publish from here."""
        self.observer.retire()
        self._timer.stop()

    def _begin_session(self, *args):
        """A project is ready — read from a file, or newly created and empty.

        A populated project gets a first-observed baseline; a new empty one gets
        an empty baseline, so a layer added afterwards is genuinely an addition
        rather than a first sighting.
        """
        self.observer.start_session(self._session_token())
        self.history = History()
        self.trace.reset_counters()
        self._timer.start()

    def _recompute(self):
        if self.panel is not None and self.panel.isVisible():
            self.panel.refresh(self.observer, self.history, self.trace)
