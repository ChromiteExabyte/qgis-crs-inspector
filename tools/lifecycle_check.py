"""Drive the plugin's real lifecycle: classFactory -> initGui -> action.

Not another standalone panel. Ownership and subscriptions are invisible to a test
that constructs a panel directly, so this enters where QGIS enters, uses real
QGIS objects and real Qt signals, and supplies only the host-interface methods
the plugin actually calls.

Three tests it deliberately avoids, each able to pass while the real path is
broken: calling `_invalidate()` directly, refreshing manually after the edit, and
checking only the verdict once the timer has run.

Assertions carry stable identifiers. `tools/sensitivity_check.py` uses them as
its protocol, so a deliberately broken build must fail a *named* assertion
rather than merely producing a nonzero exit.

    lifecycle_check.py [--zip PATH] [--scenario epoch_edit] [--json OUT]
                       [--normal-exit]

`--normal-exit` reports the SHUTDOWN result by terminating normally. The default
uses os._exit, which skips interpreter cleanup and therefore establishes
behaviour only — never that shutdown succeeds.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
RESULTS = []


def check(ident, condition, message):
    ok = bool(condition)
    print("  {}  {:<38} {}".format("ok  " if ok else "FAIL", ident, message))
    RESULTS.append({"id": ident, "ok": ok, "message": message})
    return ok


def arg(flag, default=None):
    argv = sys.argv[1:]
    return argv[argv.index(flag) + 1] if flag in argv else default


def _isolate(workdir: Path):
    """Only `workdir` resolves `crs_inspector`; the repository never does."""
    kept = []
    for entry in sys.path:
        try:
            resolved = Path(entry or ".").resolve()
        except Exception:
            kept.append(entry)
            continue
        if resolved == ROOT or ROOT in resolved.parents:
            continue
        kept.append(entry)
    sys.path[:] = [str(workdir)] + kept


def bootstrap():
    """Import the package under test.

    `--package-dir` points at an already-extracted tree, which is how the
    sensitivity runner supplies a deliberately modified derivative without
    pretending it is the original archive. `--zip` extracts the artifact itself.
    Neither ever leaves the repository able to resolve the package.
    """
    package_dir = arg("--package-dir")
    if package_dir:
        workdir = Path(package_dir).resolve()
        _isolate(workdir)
        return workdir, "package dir {}".format(workdir.name)
    if "--zip" not in sys.argv[1:]:
        sys.path.insert(0, str(ROOT))
        return ROOT, "repository checkout"
    import tempfile
    import zipfile
    archive = Path(arg("--zip")).resolve()
    workdir = Path(tempfile.mkdtemp(prefix="crs_inspector_lifecycle_"))
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(workdir)
    _isolate(workdir)
    return workdir, archive.name


class _Bar:
    def pushInfo(self, title, message):
        pass


class _Iface:
    """Only what the plugin calls. A wider fake would hide a missing call.

    Note what this does NOT provide: `projectRead` and `newProjectCreated`
    signals. The session-rollover check therefore calls `_begin_session()`
    directly, and establishes correct rebinding once the handler is entered —
    not that a host notification reaches it.
    """

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


class Env:
    pass


def build_env(import_root, source_name):
    """A project, a layer, a window and a loaded plugin. Shared by every run."""
    from qgis.core import (
        QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsPointXY,
        QgsProject, QgsVectorLayer,
    )
    from qgis.PyQt.QtWidgets import QApplication, QMainWindow

    env = Env()
    env.project = QgsProject.instance()
    env.project.clear()
    env.project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))

    env.layer = QgsVectorLayer("Point?crs=EPSG:4326", "epoch_layer", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(-2.5, 51.8)))
    env.layer.dataProvider().addFeature(feature)
    env.layer.updateExtents()
    env.project.addMapLayer(env.layer)

    env.window = QMainWindow()
    env.window.show()
    QApplication.processEvents()
    env.iface = _Iface(env.window)

    import crs_inspector
    import crs_inspector.plugin as plugin_module
    origin = Path(plugin_module.__file__).resolve()
    check("setup.import_origin", str(origin).startswith(str(import_root)),
          "crs_inspector.plugin from {}".format(source_name))

    env.plugin = crs_inspector.classFactory(env.iface)
    env.plugin.initGui()
    check("setup.menu_action", bool(env.iface.menu_actions),
          "initGui registered a menu action")

    env.plugin.action.setChecked(True)
    env.plugin.action.triggered.emit(True)      # the real action, not _show()
    QApplication.processEvents()
    env.panel = env.plugin.panel
    check("setup.panel_opened", env.panel is not None,
          "the action opened the plugin's panel")
    return env


def scenario_epoch_edit(env):
    """A native-setter epoch edit, with reassessment held deterministically.

    The single scenario used by both the ordinary run and every sensitivity
    mutant — identical expectations, never a separate "negative version".
    """
    from qgis.core import QgsCoordinateReferenceSystem
    from qgis.PyQt.QtWidgets import QApplication

    plugin, panel, layer = env.plugin, env.panel, env.layer

    check("setup.first_current", plugin.observer.presentation.is_current,
          "the first assessment is presented as current")
    first_at = plugin.observer.presentation.observed_at
    history_before = len(plugin.history)
    status_before = panel.status.text()

    # A precondition witness: proves the edit committed and the signal fired,
    # so a missing plugin response cannot be confused with a broken fixture.
    fired = []
    layer.crsChanged.connect(lambda *a: fired.append(True))

    # A delegating spy on the REAL publish path. Whether another assessment
    # completed is a question about accepted observations, not about the wall
    # clock — two of them can share a timestamp, and the clock can move
    # backwards.
    accepted = []
    real_publish = plugin.observer.publish

    def spy(observation):
        allowed = real_publish(observation)
        if allowed and observation.ok:
            accepted.append(observation)
        return allowed

    plugin.observer.publish = spy

    plugin._timer.stop()
    changed = QgsCoordinateReferenceSystem(layer.crs())
    changed.setCoordinateEpoch(2025.0)
    layer.setCrs(changed)          # crs() returns by value; assign it back

    # Everything below is asserted BEFORE events are pumped.
    check("epoch_edit.signal_observed", bool(fired),
          "precondition: the layer emitted crsChanged")
    check("epoch_edit.revokes_currency",
          not plugin.observer.presentation.is_current,
          "the observation stopped claiming currency")
    check("epoch_edit.renders_stale",
          panel.status.text() != status_before
          and ("inputs changed" in panel.status.text().lower()
               or "not yet re-checked" in panel.status.text().lower()),
          "the visible status says so: {!r}".format(
              panel.status.text().splitlines()[0]))
    check("epoch_edit.schedules_recheck", plugin._timer.isActive(),
          "a reassessment was scheduled, not run inline")
    check("epoch_edit.preserves_last_success",
          not accepted and plugin.observer.presentation.observed_at == first_at,
          "no further observation was accepted; the last success stands")
    check("epoch_edit.does_not_commit_history",
          len(plugin.history) == history_before,
          "no newly assessed state was committed")

    plugin.observer.publish = real_publish
    plugin._timer.stop()           # hold it from here, deterministically
    QApplication.processEvents()
    return env


def full_lifecycle(env):
    """Everything beyond the epoch scenario. Not run for sensitivity mutants."""
    from qgis.core import QgsCoordinateReferenceSystem
    from qgis.PyQt.QtWidgets import QApplication

    plugin, panel, layer = env.plugin, env.panel, env.layer

    check("ownership.observer", panel._observer is plugin.observer,
          "the panel holds the plugin's authoritative observer")
    check("ownership.history", panel._history is plugin.history,
          "and its authoritative history")

    print("\n-- releasing the reassessment --")
    # A delegating spy around the REAL publish: observes that a new successful
    # observation was accepted, without a timestamp standing in for identity.
    accepted = []
    real_publish = plugin.observer.publish

    def spy(observation):
        ok = real_publish(observation)
        if ok and observation.ok:
            accepted.append(observation)
        return ok

    plugin.observer.publish = spy
    plugin._recompute()
    QApplication.processEvents()
    check("recheck.accepted", len(accepted) == 1,
          "exactly one successful observation was accepted")
    check("recheck.current", plugin.observer.presentation.is_current,
          "and it restored currency")
    if accepted:
        state = accepted[-1].state
        epochs = [s.source.epoch for s in state.layers if s.layer_name == "epoch_layer"]
        check("recheck.saw_the_edit", 2025.0 in epochs,
              "the accepted observation inspected the edited epoch: {}".format(epochs))

    print("\n-- edited while hidden: reopening must not resurrect it --")
    plugin.action.setChecked(False)
    plugin.action.triggered.emit(False)
    QApplication.processEvents()
    again = QgsCoordinateReferenceSystem(layer.crs())
    again.setCoordinateEpoch(2030.0)
    layer.setCrs(again)
    QApplication.processEvents()
    plugin._timer.stop()
    plugin._recompute()            # fires, finds it hidden, does nothing
    QApplication.processEvents()
    check("hidden.stays_stale", not plugin.observer.presentation.is_current,
          "an edit made while hidden leaves the evidence stale")

    accepted.clear()
    plugin.action.setChecked(True)
    plugin.action.triggered.emit(True)
    QApplication.processEvents()
    plugin._recompute()
    QApplication.processEvents()
    check("reopen.reassessed", len(accepted) >= 1,
          "reopening produced a newly accepted observation")
    if accepted:
        epochs = [s.source.epoch for s in accepted[-1].state.layers
                  if s.layer_name == "epoch_layer"]
        check("reopen.saw_the_edit", 2030.0 in epochs,
              "which inspected the edit made while hidden: {}".format(epochs))
    plugin.observer.publish = real_publish

    print("\n-- a new session rebinds before anything can assess --")
    # NOTE: calls the handler directly. _Iface has no projectRead /
    # newProjectCreated signals, so this establishes rebinding once entered,
    # not that a host notification reaches it.
    history_a = plugin.history
    baseline_a = len(history_a)
    plugin._begin_session()
    plugin._timer.stop()
    check("session.new_history", plugin.history is not history_a,
          "the session started a fresh authoritative history")
    check("session.panel_rebound", panel._history is plugin.history,
          "the panel was rebound synchronously, not on the next timer")
    panel.refresh_button.click()
    QApplication.processEvents()
    check("session.no_cross_write", len(history_a) == baseline_a,
          "a manual re-check did not write into the abandoned history")

    print("\n-- a superseded subscription cannot invalidate --")
    entry = plugin._layer_subscriptions.get(layer.id())
    if entry is not None:
        _obj, _handler, stale_token = entry
        plugin._timer.stop()
        before = plugin.observer.begin().generation
        plugin._unsubscribe_layer_ids([layer.id()])
        plugin._subscribe_layers([layer])
        plugin._on_layer_crs_changed(layer.id(), stale_token)
        check("token.stale_ignored",
              plugin.observer.begin().generation == before
              and not plugin._timer.isActive(),
              "a stale token neither superseded the observation nor scheduled work")
        live = plugin._layer_subscriptions[layer.id()][2]
        plugin._on_layer_crs_changed(layer.id(), live)
        check("token.live_honoured",
              plugin.observer.begin().generation != before,
              "while the current subscription still invalidates")

    print("\n-- retirement and unload --")
    plugin._retire_session()
    check("retire.no_evidence", not plugin.observer.presentation.has_evidence,
          "a retired session presents no evidence")
    check("retire.unsubscribed", not plugin._layer_subscriptions,
          "retirement released the per-layer subscriptions")
    plugin.unload()
    check("unload.dock_removed", plugin.panel is None, "unload removed the dock")
    check("unload.entries_removed",
          not env.iface.menu_actions and not env.iface.toolbar_actions,
          "unload removed its menu and toolbar entries")


def teardown(env, observe=False):
    """Ordered destruction, used by EVERY scenario.

    This was previously done only in the full run, so the abbreviated scenario
    reached exitQgis() with a live plugin, dock and project and died with
    0xC0000409 and no output at all — a harness crash the sensitivity runner
    would then have had to interpret. Teardown is not scenario-specific.

    `observe=True` additionally asserts that destruction actually happened,
    which is the shutdown result rather than a functional one.
    """
    from qgis.core import QgsProject
    from qgis.PyQt.QtCore import QCoreApplication, QEvent
    from qgis.PyQt.QtWidgets import QApplication

    if env is None:
        return
    plugin = getattr(env, "plugin", None)
    if plugin is not None and getattr(plugin, "panel", None) is not None:
        try:
            plugin.unload()
        except Exception:
            pass
    destroyed = []
    window = getattr(env, "window", None)
    if window is not None:
        try:
            window.destroyed.connect(lambda *a: destroyed.append("window"))
        except Exception:
            pass
        window.close()
        window.deleteLater()
    try:
        deferred = QEvent.Type.DeferredDelete
    except AttributeError:
        deferred = QEvent.DeferredDelete
    # Requesting deletion is not observing destruction: Qt does not process
    # deferred deletes unless an event loop drives them.
    QCoreApplication.sendPostedEvents(None, deferred)
    QApplication.processEvents()
    if observe:
        check("shutdown.window_destroyed", "window" in destroyed,
              "the harness window was actually destroyed, not merely scheduled")
    env.window = None
    try:
        QgsProject.instance().clear()
    except Exception as exc:
        if observe:
            check("shutdown.project_cleared", False, "clear failed: {}".format(exc))


def main() -> int:
    import_root, source_name = bootstrap()
    from qgis.core import Qgis, QgsApplication
    from qgis.PyQt.QtCore import QT_VERSION_STR

    app = QgsApplication([], True)
    prefix = os.environ.get("QGIS_PREFIX_PATH") or (
        os.path.join(os.environ["OSGEO4W_ROOT"], "apps", "qgis")
        if os.environ.get("OSGEO4W_ROOT") else "/usr")
    QgsApplication.setPrefixPath(prefix, True)
    app.initQgis()
    print("qgis {}  (Qt {})".format(Qgis.QGIS_VERSION, QT_VERSION_STR))

    scenario = arg("--scenario", "full")
    env = None
    try:
        env = build_env(import_root, source_name)
        if env.panel is not None:
            scenario_epoch_edit(env)
            if scenario == "full":
                full_lifecycle(env)
                print("\n-- shutdown (a SEPARATE result) --")
                teardown(env, observe=True)
    finally:
        try:
            teardown(env)          # idempotent; every scenario tears down alike
        except Exception as exc:
            check("shutdown.teardown", False, "teardown failed: {}".format(exc))
        app.exitQgis()

    failures = [r for r in RESULTS if not r["ok"]]
    out = arg("--json")
    if out:
        Path(out).write_text(json.dumps(
            {"scenario": scenario, "results": RESULTS,
             "failed": [r["id"] for r in failures]}, indent=2), encoding="utf-8")

    print()
    if failures:
        print("FAILED: {}".format(", ".join(r["id"] for r in failures)))
        return 1
    print("OK  {} assertions".format(len(RESULTS)))
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    if "--normal-exit" in sys.argv[1:]:
        sys.exit(code)          # the SHUTDOWN result: ordinary termination
    os._exit(code)              # behaviour only; skips interpreter cleanup
