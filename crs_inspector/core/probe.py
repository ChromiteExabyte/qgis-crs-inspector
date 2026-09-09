"""The only module that touches PyQGIS.

Everything here returns plain dataclasses from `model`, so the rest of the
plugin — and every test — can run without a QGIS instance. The API calls below
were validated by the M0 spike against QGIS 4.2.2 / PROJ 9.8; see
`spike/m0_crs_state.txt` for the raw output that justifies each one.
"""

from __future__ import annotations

import re
from typing import Optional

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)

from .fingerprint import ContextEntry
from .model import CrsRef, GridRef, LayerState, OperationRef, ProjectState, clean_float

_DATUM_NODE = re.compile(r'\b(?:DATUM|ENSEMBLE|GEODETICDATUM)\s*\[\s*"([^"]+)"')

INVALID_CRS = CrsRef(authid="", description="no CRS set", datum_key="", is_valid=False)


def datum_key(crs) -> str:
    """Stable datum identity for a CRS.

    Two tiers, because M0 showed `datumEnsemble()` is empty for every
    single-datum CRS (OSGB36, ED50, NAD27, GDA2020 all came back blank) while
    the WKT DATUM node is populated for all of them.
    """
    try:
        ensemble = crs.datumEnsemble()
        if ensemble is not None and ensemble.isValid():
            return ensemble.name()
    except Exception:
        pass

    for attempt in (
        lambda: crs.toWkt(Qgis.CrsWktVariant.Wkt2_2019),
        lambda: crs.toWkt(),
    ):
        try:
            wkt = attempt()
        except Exception:
            continue
        if wkt:
            found = _DATUM_NODE.search(wkt)
            if found:
                return found.group(1)
    return ""


def read_crs(crs) -> CrsRef:
    if crs is None or not crs.isValid():
        return INVALID_CRS
    return CrsRef(
        authid=crs.authid() or "",
        description=crs.description() or "",
        datum_key=datum_key(crs),
        is_geographic=bool(crs.isGeographic()),
        is_dynamic=bool(crs.isDynamic()),
        epoch=clean_float(crs.coordinateEpoch()),
        is_valid=True,
    )


def _read_grids(details) -> tuple:
    out = []
    try:
        grids = list(details.grids)
    except Exception:
        return ()
    for g in grids:
        try:
            out.append(GridRef(
                short_name=g.shortName or "",
                package=g.packageName or "",
                url=g.url or "",
                is_available=bool(g.isAvailable),
            ))
        except Exception:
            continue
    return tuple(out)


def _representative_point(layer):
    """A point in the layer's own CRS, used to force the transform to run.

    Some PROJ state is only settled once a coordinate has actually been pushed
    through, so the probe transforms one point rather than merely constructing
    the transform.
    """
    try:
        extent = layer.extent()
        if extent is not None and not extent.isNull() and not extent.isEmpty():
            return extent.center()
    except Exception:
        pass
    return None


def probe_layer(layer, target_crs, context) -> LayerState:
    """Read one layer's coordinate state. Never raises."""
    layer_id = layer.id()
    layer_name = layer.name()
    target = read_crs(target_crs)

    try:
        source = read_crs(layer.crs())
    except Exception as exc:
        return LayerState(layer_id, layer_name, INVALID_CRS, target,
                          probe_error="could not read layer CRS: {}".format(exc))

    if not source.is_valid:
        return LayerState(layer_id, layer_name, source, target)

    try:
        transform = QgsCoordinateTransform(layer.crs(), target_crs, context)
        # Keep our probing out of the user's message bar. Without this, reading
        # the state of a broken project would itself spam warnings at them.
        transform.disableFallbackOperationHandler(True)
    except Exception as exc:
        return LayerState(layer_id, layer_name, source, target,
                          probe_error="could not build transform: {}".format(exc))

    point = _representative_point(layer)
    if point is not None:
        try:
            transform.transform(point)
        except Exception:
            # A failed transform is informative but not fatal; the operation
            # details below are still worth reading.
            pass

    operation = None
    try:
        details = transform.instantiatedCoordinateOperationDetails()
        if details is not None:
            operation = OperationRef(
                name=(details.name or "").replace(
                    " (with axis order normalized for visualization)", ""),
                # -1 becomes None. On its own this does not mean ballpark;
                # grading.grade() disambiguates it against the datum.
                accuracy_m=clean_float(details.accuracy),
                is_available=bool(details.isAvailable),
                grids=_read_grids(details),
                published_accuracy_raw=details.accuracy,
            )
    except Exception as exc:
        return LayerState(layer_id, layer_name, source, target,
                          probe_error="could not read operation: {}".format(exc))

    return LayerState(layer_id, layer_name, source, target, operation=operation)


def visible_layer_ids(project: Optional[QgsProject] = None):
    """Ids of layers currently ticked in the layer tree, or None if unknown.

    Deliberately separate from probing. Visibility is a *presentation* concern:
    filtering the assessment itself made hidden layers absent from the snapshot,
    and an absent subject reads as removed, so toggling a checkbox wrote
    removal and re-addition records into history.
    """
    project = project or QgsProject.instance()
    try:
        return {n.layerId() for n in project.layerTreeRoot().findLayers()
                if n.isVisible()}
    except Exception:
        return None


def probe_project(project: Optional[QgsProject] = None) -> ProjectState:
    """Read every layer's state against the project CRS. Always full membership."""
    project = project or QgsProject.instance()
    target_crs = project.crs()
    context = project.transformContext()

    states = []
    for layer in project.mapLayers().values():
        try:
            states.append(probe_layer(layer, target_crs, context))
        except Exception as exc:  # a probe must never take the panel down
            states.append(LayerState(
                layer.id(), layer.name(), INVALID_CRS, read_crs(target_crs),
                probe_error="unexpected: {}".format(exc)))

    states.sort(key=lambda s: s.layer_name.lower())
    return ProjectState(target=read_crs(target_crs), layers=tuple(states),
                        context_entries=read_context_entries(context))


def read_context_entries(context) -> tuple:
    """Extract the project's *authored* transformation policy.

    `coordinateOperations()` is not enough for this: it yields only authid
    strings — and QGIS warns that USER: ids are not portable between machines or
    profiles — and it omits the per-pair fallback flag. `allowFallbackTransform()`
    does not read that flag back faithfully either (verified: a pair stored with
    allowFallback=False reads back True).

    So the XML serialisation is the route. The fields are extracted here and
    canonicalised in `fingerprint`, which stays pure — the raw XML is never
    hashed, because a serialiser change would then look like a user edit.
    """
    try:
        from qgis.core import QgsReadWriteContext
        from qgis.PyQt.QtXml import QDomDocument
    except Exception:
        return ()

    try:
        doc = QDomDocument("dl")
        root = doc.createElement("root")
        doc.appendChild(root)
        context.writeXml(root, QgsReadWriteContext())
    except Exception:
        return ()

    entries = []
    pairs = root.elementsByTagName("srcDest")
    for i in range(pairs.count()):
        node = pairs.at(i).toElement()
        if node.isNull():
            continue
        try:
            entries.append(ContextEntry(
                source=_wkt_of(node, "src"),
                destination=_wkt_of(node, "dest"),
                # "" is a real authored value: use default operation behaviour.
                operation=node.attribute("coordinateOp", ""),
                allow_fallback=node.attribute("allowFallback", "1") == "1",
                source_authid=_text_of(node, "src", "authid"),
                destination_authid=_text_of(node, "dest", "authid"),
            ))
        except Exception:
            continue
    return tuple(entries)


def _side(node, side):
    kids = node.elementsByTagName(side)
    return kids.at(0).toElement() if kids.count() else None


def _wkt_of(node, side) -> str:
    return _text_of(node, side, "wkt")


def _text_of(node, side, tag) -> str:
    element = _side(node, side)
    if element is None or element.isNull():
        return ""
    found = element.elementsByTagName(tag)
    if not found.count():
        return ""
    return found.at(0).toElement().text() or ""
