"""Verify the built ZIP is genuinely self-contained, then exercise it.

The trap this exists to close: a development shell that puts the repository on
`sys.path` can satisfy imports the *installed* package is missing, so the plugin
passes every test here and fails on a user's machine. This extracts the ZIP to a
temp directory, puts only that on the path, asserts the repository is absent, and
runs the plugin from there.

Then it builds the fixture project **in code** and checks the three expected
classifications, so a Qt5 run and a Qt6 run prove the same behaviour rather than
just "it imported". The project is constructed rather than loaded because a .qgz
written by QGIS 4.2 does not round-trip into 3.44 — CI caught the project CRS
coming back empty, which quietly turned every layer into a cross-datum case.

    "<OSGeo4W>/bin/python-qgis.bat" tools/verify_package.py

Exit code 0 on success. Used locally and by both QGIS jobs in CI.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
PROJECT_CRS = "EPSG:6318"          # NAD83(2011)

#: From fixture/README.md. The ballpark expectation holds only while the NADCON5
#: grids are absent — which is the normal state of a clean install and of CI.
EXPECTED = {
    "control_projection_only": "no_shift",
    "case_published_shift": "shift",
    "case_ballpark_nad27": ("ballpark", "cross_datum_unknown"),
}


def build_fixture_project():
    """Construct the fixture project in code rather than reading the .qgz.

    A project file written by QGIS 4.2 does not round-trip into 3.44 — CI showed
    the project CRS coming back empty, with a "saved with a newer version"
    warning. GeoPackages are portable across versions; the project serialisation
    is not. So the layers come from disk and the project CRS is set explicitly,
    which makes this check mean the same thing on every target.

    `fixture/crs_inspector_fixture.qgz` stays for the manual desktop session,
    where one QGIS both writes and reads it.
    """
    from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer

    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem(PROJECT_CRS))

    added = 0
    for name in EXPECTED:
        gpkg = ROOT / "fixture" / "{}.gpkg".format(name)
        if not gpkg.exists():
            print("  missing fixture layer: {}".format(gpkg))
            continue
        layer = QgsVectorLayer("{}|layername={}".format(gpkg, name), name, "ogr")
        if not layer.isValid():
            print("  invalid fixture layer: {}".format(gpkg))
            continue
        project.addMapLayer(layer)
        added += 1
    return project if added == len(EXPECTED) else None


def chosen_zip() -> Path:
    """The artifact under test, named explicitly wherever it matters.

    "Newest by modification time" is a convenience, not an identity: it can
    silently select an older build and report it as verified. CI passes --zip so
    the archive that was uploaded is the archive that gets verified.
    """
    argv = sys.argv[1:]
    if "--zip" in argv:
        return Path(argv[argv.index("--zip") + 1]).resolve()
    zips = sorted((ROOT / "dist").glob("crs_inspector-*.zip"),
                  key=lambda p: p.stat().st_mtime)
    if not zips:
        raise SystemExit("no package found — run tools/package.py first")
    print("note     no --zip given; falling back to newest by mtime")
    return zips[-1]


def check_declared_compatibility(workdir: Path):
    """Ask the running QGIS's own installer logic whether it would accept this.

    Importing classFactory proves the CODE runs here. It says nothing about the
    plugin manager's compatibility decision, which is made from metadata alone —
    and that gap shipped a package QGIS 4.2 marks incompatible, because an
    absent qgisMaximumVersion defaults to <major>.99 and 3.44 became 3.99.
    """
    metadata = workdir / "crs_inspector" / "metadata.txt"
    values = {}
    for line in metadata.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith(" "):
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip())

    minimum = values.get("qgisMinimumVersion", "0")
    maximum = values.get("qgisMaximumVersion", "")
    effective = maximum or (minimum[0] + ".99")     # the reader's own default
    try:
        from pyplugin_installer.version_compare import isCompatible, pyQgisVersion
    except Exception as exc:
        return None, "could not load QGIS compatibility logic: {}".format(exc)

    running = pyQgisVersion()
    ok = isCompatible(running, minimum, effective)
    print("declared {}–{}{}  vs running {}  -> {}".format(
        minimum, effective, "" if maximum else " (defaulted)", running,
        "compatible" if ok else "INCOMPATIBLE"))
    if "supportsQt6" in values:
        return ok, "metadata still declares supportsQt6, which QGIS no longer reads"
    return ok, None


def main() -> int:
    package = chosen_zip()
    print("package  {}".format(package.name))
    print("sha256   {}".format(
        hashlib.sha256(package.read_bytes()).hexdigest()))

    workdir = Path(tempfile.mkdtemp(prefix="crs_inspector_verify_"))
    with zipfile.ZipFile(package) as zf:
        zf.extractall(workdir)

    # Strip every path that could resolve `crs_inspector` from the repository —
    # Python puts this script's own directory on sys.path, so simply asserting
    # the repo is absent would fail on the verifier's own invocation. Removing
    # them and then confirming where the import actually landed is the check
    # that means something.
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
    print("path     {}".format(workdir))

    from qgis.core import QgsApplication, QgsProject

    app = QgsApplication([], True)
    prefix = os.environ.get("QGIS_PREFIX_PATH") or (
        os.path.join(os.environ["OSGEO4W_ROOT"], "apps", "qgis")
        if os.environ.get("OSGEO4W_ROOT") else "/usr")
    QgsApplication.setPrefixPath(prefix, True)
    app.initQgis()

    failures = []
    try:
        from qgis.core import Qgis
        from qgis.PyQt.QtCore import QT_VERSION_STR
        print("qgis     {}  (Qt {})".format(Qgis.QGIS_VERSION, QT_VERSION_STR))

        compatible, note = check_declared_compatibility(workdir)
        if note:
            failures.append(note)
        if compatible is False:
            failures.append(
                "the plugin manager on this QGIS would mark this package "
                "incompatible from its metadata alone")

        licence = workdir / "crs_inspector" / "LICENSE"
        if not licence.exists():
            failures.append("no LICENSE inside the installed package")
        elif licence.read_bytes() != (ROOT / "LICENSE").read_bytes():
            failures.append("packaged LICENSE differs from the authoritative file")
        else:
            print("licence  packaged, matches the repository copy")

        import crs_inspector
        origin = Path(crs_inspector.__file__).resolve()
        if workdir.resolve() not in origin.parents:
            failures.append(
                "imported crs_inspector from {} — not the extracted package".format(origin))
        else:
            print("import   {}".format(origin.relative_to(workdir)))
        if not hasattr(crs_inspector, "classFactory"):
            failures.append("package exposes no classFactory()")

        class _Bar:
            def pushInfo(self, title, message):
                pass

        class _Iface:
            def messageBar(self):
                return _Bar()

            def mainWindow(self):
                return None

        plugin = crs_inspector.classFactory(_Iface())
        print("plugin   {}".format(type(plugin).__name__))

        project = build_fixture_project()
        if project is None:
            failures.append("could not build the fixture project")
        else:
            from crs_inspector.core.grading import grade
            from crs_inspector.core.probe import probe_project

            state = probe_project(project)

            # A blank target CRS silently turns every layer into a cross-datum
            # case, so the grades look confident and are wrong. Fail loudly
            # instead — CI caught exactly this when a 4.2-written .qgz was read
            # by 3.44 and the project CRS came back empty.
            if not state.target.is_valid or not state.target.datum_key:
                failures.append(
                    "project CRS did not resolve (authid={!r}, datum={!r})".format(
                        state.target.authid, state.target.datum_key))
            got = {s.layer_name: grade(s).shift.value for s in state.layers}
            print("project  {}  {}".format(state.target.authid, state.target.datum_key))
            for name, want in EXPECTED.items():
                actual = got.get(name)
                ok = actual in (want if isinstance(want, tuple) else (want,))
                print("  {:<26} {:<22} {}".format(name, actual or "MISSING",
                                                  "ok" if ok else "EXPECTED " + str(want)))
                if not ok:
                    failures.append("{}: got {!r}, expected {!r}".format(
                        name, actual, want))

            # Constructing the widget is the point of running this on both Qt
            # toolkits: the enum-scoping break was invisible until one existed.
            from crs_inspector.ui.panel import CrsInspectorPanel
            panel = CrsInspectorPanel(_Iface())   # standalone, on purpose
            # The panel no longer assesses in its constructor: the owner
            # establishes collaborators, wires notifications, then drives the
            # first probe. A standalone caller is that owner.
            panel.refresh()
            groups = panel.tree.topLevelItemCount()
            print("panel    constructed, {} groups, {} columns".format(
                groups, panel.tree.columnCount()))
            if groups != len(EXPECTED):
                failures.append("panel showed {} groups, expected {}".format(
                    groups, len(EXPECTED)))

            # A temporal layer, exercised through the packaged code on both Qt
            # targets. Two formatter holes have now landed on states added late
            # — the serializer's, then history's, which crashed snapshot() and
            # so every assessment. Reaching this path from the installed package
            # is cheap insurance against the third.
            from dataclasses import replace as _replace
            from crs_inspector.core.diffing import History, snapshot
            from crs_inspector.core.model import (
                CrsRef, LayerState, ProjectState, Shift,
            )
            from crs_inspector.core.serialize import to_text

            dynamic = CrsRef("EPSG:9755", "WGS 84 (G2296)", "WGS 84 ensemble",
                             is_dynamic=True, epoch=2020.0,
                             definition="GEOGCRS[wgs]")
            temporal = LayerState("epoch_layer", "epoch_layer.gpkg",
                                  dynamic, _replace(dynamic, epoch=2025.0))
            temporal_state = ProjectState(target=temporal.target,
                                          layers=(temporal,))
            verdict = grade(temporal)
            if verdict.shift is not Shift.TEMPORAL_UNASSESSED:
                failures.append("temporal fixture graded {}, expected "
                                "TEMPORAL_UNASSESSED".format(verdict.shift))
            try:
                snapshot(temporal_state)
                History().record(temporal_state)
                to_text(temporal_state, "temporal.qgz")
                print("temporal formats through history and the text record")
            except Exception as exc:
                failures.append("temporal layer broke a formatter: {}: {}".format(
                    type(exc).__name__, exc))

            # Component-level: this panel is deliberately standalone, to check
            # the widget in isolation. Ownership and subscriptions are exercised
            # through the plugin's real lifecycle in tools/lifecycle_check.py,
            # which CI runs on both targets alongside this.
            # Drive the real signals, not just the constructor. Qt's clicked
            # carries `checked: bool`; wired straight to refresh() it landed in
            # the `observer` parameter and destroyed it. Constructing the panel
            # would never have caught that — only clicking does.
            from crs_inspector.core.observation import Observer
            before = panel._observer
            panel.refresh_button.click()
            if not isinstance(panel._observer, Observer):
                failures.append(
                    "refresh button replaced the observer with {!r}".format(
                        panel._observer))
            elif panel._observer is not before:
                failures.append("refresh button swapped the observer instance")
            else:
                print("clicked  refresh, observer intact")

            panel.copy_button.click()
            print("clicked  copy record")

            # Visibility filters the view; it must never change what is assessed.
            panel.visible_only.setChecked(True)
            filtered = len(panel._state.layers) if panel._state else 0
            panel.visible_only.setChecked(False)
            restored = len(panel._state.layers) if panel._state else 0
            if filtered != len(EXPECTED) or restored != len(EXPECTED):
                failures.append(
                    "visibility filter changed assessed membership: "
                    "{} then {}, expected {} both times".format(
                        filtered, restored, len(EXPECTED)))
            else:
                print("toggled  visible-only, membership unchanged ({})".format(
                    restored))
    finally:
        app.exitQgis()

    if failures:
        print("\nFAILED")
        for f in failures:
            print("  - {}".format(f))
        return 1
    print("\nOK  package is self-contained and behaves as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
