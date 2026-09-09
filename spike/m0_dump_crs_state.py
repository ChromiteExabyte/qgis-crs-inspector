"""M0 spike — dump the CRS / transform state PyQGIS actually exposes.

Two modes, one file.

  Console mode.  Paste into the QGIS Python console with a project open:
      exec(open(r"<path to this file>", encoding="utf-8").read())
  Probes every layer against the project CRS.

  Headless mode.  Run outside QGIS:
      "<OSGeo4W>\\bin\\python-qgis.bat" m0_dump_crs_state.py
  No project, so it probes the DESIGN.md section 10 fixture pairs instead. This
  is the mode that answers the API questions, because the fixtures are chosen to
  hit each state deliberately rather than whatever happens to be loaded.

Its job is to ANSWER QUESTIONS, not to be correct. Every probe is wrapped, so a
missing or renamed API prints "unavailable: <reason>" instead of aborting. Read
the output, then update DESIGN.md with what reality said.

What it is trying to settle
---------------------------
1.  Ballpark detection. fallbackOperationOccurred() is an INSTANCE method valid
    only for the MOST RECENT transform — so the probe has to actually push a
    point through, not merely construct the transform. Confirm that, and confirm
    disableFallbackOperationHandler(True) keeps probing out of the message bar.
2.  Whether "no operation exists" is distinguishable from "an operation fell
    back". Ballpark and merely-inaccurate must not collapse into one state.
3.  Datum identity for the colour encoding (DESIGN.md section 7). datumEnsemble()
    only returns something for ensemble CRSs like WGS 84 — OSGB36 and ED50 are
    single datums and need a fallback key. Dump both and find out.
4.  Whether section 6's measured displacement is computable: pin a context to a
    specific operation and difference it against the one in force.
"""

import os
import re
import sys
import traceback
from datetime import datetime

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsDatumTransform,
    QgsDistanceArea,
    QgsPointXY,
    QgsProject,
    QgsUnitTypes,
)

UNSET = "-"
LINE = "-" * 78

# DESIGN.md section 10. Probe points are in the SOURCE CRS and sit in the area of
# use, because ballpark-ness can depend on where you are relative to a grid.
FIXTURES = [
    ("the grid file",  "EPSG:27700", "EPSG:4326", (400000.0, 300000.0)),
    ("reverse of same", "EPSG:4326", "EPSG:27700", (-2.5, 51.8)),
    ("true ballpark?",  "EPSG:4230", "EPSG:27700", (-2.5, 51.8)),
    ("the calm state",  "EPSG:3857", "EPSG:4326", (-278000.0, 6750000.0)),
    ("the inversion",   "EPSG:27700", "EPSG:3857", (400000.0, 300000.0)),
    ("NAD27 grids",     "EPSG:4267", "EPSG:6318", (-96.0, 39.0)),
    ("dynamic CRS",     "EPSG:7844", "EPSG:9000", (145.0, -37.8)),
]


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def safe(fn, default="unavailable"):
    """Call fn(); on any failure return a marker naming the failure.

    A spike that raises tells you nothing about the calls after the raise.
    """
    try:
        v = fn()
    except Exception as exc:
        return "unavailable: {}: {}".format(type(exc).__name__, exc)
    return default if v is None else v


def ensure_qgis():
    """Start a headless QgsApplication if we are not already inside QGIS."""
    if QgsApplication.instance() is not None:
        return None
    root = os.environ.get("OSGEO4W_ROOT", "")
    prefix = os.environ.get("QGIS_PREFIX_PATH") or (
        os.path.join(root, "apps", "qgis") if root else "")
    if not prefix or not os.path.isdir(prefix):
        import qgis
        prefix = os.path.abspath(os.path.join(os.path.dirname(qgis.__file__), "..", ".."))
    app = QgsApplication([], False)
    QgsApplication.setPrefixPath(prefix, True)
    app.initQgis()
    return app


def datum_key(crs):
    """Best available stable identity for the CRS's datum.

    Section 7 colours rows by datum, so this must return the same key for two
    CRSs sharing a datum (EPSG:27700 and EPSG:4277 are both OSGB36). Ensemble
    first, then the WKT DATUM / ENSEMBLE node.
    """
    out = {}
    try:
        ens = crs.datumEnsemble()
        if ens is not None and ens.isValid():
            out["ensemble_name"] = ens.name()
            out["ensemble_code"] = "{}:{}".format(ens.authority(), ens.code())
            out["ensemble_accuracy_m"] = ens.accuracy()
        else:
            out["ensemble_name"] = UNSET + " (not an ensemble CRS)"
    except Exception as exc:
        out["ensemble_name"] = "unavailable: {}".format(exc)

    wkt = ""
    for attempt in (lambda: crs.toWkt(Qgis.CrsWktVariant.Wkt2_2019), lambda: crs.toWkt()):
        got = safe(attempt, "")
        if isinstance(got, str) and got.startswith(("GEOG", "PROJ", "BOUND", "COMPOUND")):
            wkt = got
            break
    if wkt:
        m = re.search(r'\b(?:DATUM|ENSEMBLE|GEODETICDATUM)\s*\[\s*"([^"]+)"', wkt)
        out["wkt_datum_node"] = m.group(1) if m else UNSET
    else:
        out["wkt_datum_node"] = "unavailable"
    return out


def crs_facts(crs):
    if crs is None or not crs.isValid():
        return {"valid": False, "authid": UNSET, "description": "invalid or unset CRS"}
    f = {
        "valid": True,
        "authid": safe(crs.authid, UNSET),
        "description": safe(crs.description, UNSET),
        "is_geographic": safe(crs.isGeographic),
        "is_dynamic": safe(crs.isDynamic),
        "coordinate_epoch": safe(crs.coordinateEpoch, UNSET),
        "celestial_body": safe(crs.celestialBodyName, UNSET),
    }
    f["map_units"] = safe(lambda: QgsUnitTypes.toString(crs.mapUnits()), UNSET)
    f.update(datum_key(crs))
    return f


def grid_facts(details):
    rows = []
    for g in safe(lambda: list(details.grids), []) or []:
        rows.append({
            "short_name": safe(lambda g=g: g.shortName, UNSET),
            "package": safe(lambda g=g: g.packageName, UNSET),
            "is_available": safe(lambda g=g: g.isAvailable),
            "direct_download": safe(lambda g=g: g.directDownload),
            "url": safe(lambda g=g: g.url, UNSET),
        })
    return rows


def op_facts(details):
    """TransformDetails -> dict.

    Field names verified against the PyQGIS docs: `authority` and `areaOfUse` are
    SINGULAR, and there is an `operationDetails` list that is barely documented.
    """
    if details is None:
        return {"present": False}
    return {
        "present": True,
        "name": safe(lambda: details.name, UNSET),
        "authority": safe(lambda: details.authority, UNSET),
        "code": safe(lambda: details.code, UNSET),
        "accuracy_m": safe(lambda: details.accuracy, UNSET),
        "is_available": safe(lambda: details.isAvailable),
        "scope": safe(lambda: details.scope, UNSET),
        "area_of_use": safe(lambda: details.areaOfUse, UNSET),
        "remarks": safe(lambda: details.remarks, UNSET),
        "n_single_operations": safe(lambda: len(details.operationDetails), UNSET),
        "proj": safe(lambda: details.proj, UNSET),
        "grids": grid_facts(details),
    }


# --------------------------------------------------------------------------
# the probe
# --------------------------------------------------------------------------
def probe_pair(src, dst, ctx, pt, label):
    out = {"label": label, "source": crs_facts(src), "target": crs_facts(dst)}
    if not src.isValid():
        out["verdict"] = "NO CRS ON SOURCE - nothing transforms, nothing is verified"
        return out

    out["context"] = {
        "explicit_operation": safe(
            lambda: ctx.calculateCoordinateOperation(src, dst) or UNSET, UNSET),
        "must_reverse": safe(lambda: ctx.mustReverseCoordinateOperation(src, dst)),
        "has_transform": safe(lambda: ctx.hasTransform(src, dst)),
        "allow_fallback": safe(lambda: ctx.allowFallbackTransform(src, dst)),
    }

    cands = []
    try:
        for d in QgsDatumTransform.operations(src, dst):
            cands.append({
                "name": safe(lambda d=d: d.name, UNSET),
                "accuracy_m": safe(lambda d=d: d.accuracy, UNSET),
                "is_available": safe(lambda d=d: d.isAvailable),
                "missing_grids": [g["short_name"] for g in grid_facts(d)
                                  if g["is_available"] is False],
                "proj": safe(lambda d=d: d.proj, UNSET),
            })
    except Exception as exc:
        cands = [{"error": "QgsDatumTransform.operations failed: {}".format(exc)}]
    out["n_candidate_operations"] = len(cands)
    out["candidate_operations"] = cands

    try:
        xf = QgsCoordinateTransform(src, dst, ctx)
    except Exception as exc:
        out["verdict"] = "could not construct transform: {}".format(exc)
        return out

    # Keep probing out of the user's message bar AND make
    # fallbackOperationOccurred() meaningful for this instance.
    out["handler_disabled"] = safe(
        lambda: (xf.disableFallbackOperationHandler(True), "yes")[1], "no")
    out["transform"] = {
        "is_valid": safe(xf.isValid),
        "is_short_circuited": safe(xf.isShortCircuited),
        "coordinate_operation": safe(lambda: xf.coordinateOperation() or UNSET, UNSET),
    }
    out["instantiated"] = safe(
        lambda: op_facts(xf.instantiatedCoordinateOperationDetails()), {"present": False})

    p = QgsPointXY(pt[0], pt[1])
    out["probe_point"] = {"in_source_crs": "{}, {}".format(pt[0], pt[1])}
    try:
        moved = xf.transform(p)
        out["probe_point"]["transformed"] = "{:.4f}, {:.4f}".format(moved.x(), moved.y())
        out["BALLPARK_USED"] = safe(xf.fallbackOperationOccurred, "unavailable")
    except Exception as exc:
        out["probe_point"]["transformed"] = "transform raised: {}".format(exc)
        out["BALLPARK_USED"] = "unknown - the transform itself failed"

    out["displacement"] = measure_displacement(src, dst, p, cands, xf)
    return out


def measure_displacement(src, dst, pt, cands, current_xf):
    """Can we compute a real metre difference between two operations?

    Section 6 wants measured displacement rather than published nominal accuracy.
    Cheapest proof: pin a context to the most accurate AVAILABLE candidate,
    transform the same point, and difference it against the one in force.
    """
    usable = [c for c in cands
              if c.get("is_available") is True
              and isinstance(c.get("proj"), str) and c["proj"]]
    if not usable:
        return {"computed": False, "why": "no available candidate with a proj string"}
    try:
        here = current_xf.transform(pt)
    except Exception as exc:
        return {"computed": False, "why": "in-force transform failed: {}".format(exc)}

    rows, failed = [], []
    for c in usable:
        try:
            alt_ctx = QgsCoordinateTransformContext()
            alt_ctx.addCoordinateOperation(src, dst, c["proj"])
            alt_xf = QgsCoordinateTransform(src, dst, alt_ctx)
            alt_xf.disableFallbackOperationHandler(True)
            there = alt_xf.transform(pt)
        except Exception as exc:
            failed.append("{}: {}".format(c["name"][:40], exc))
            continue
        m = safe(lambda: _metres(dst, here, there), None)
        rows.append({
            "operation": c["name"],
            "published_accuracy_m": c["accuracy_m"],
            "is_ballpark": c["accuracy_m"] == -1.0,
            "metres_from_in_force": round(m, 4) if isinstance(m, float) else m,
        })
    if not rows:
        return {"computed": False, "why": "no candidate could be pinned", "errors": failed}

    spread = [r["metres_from_in_force"] for r in rows
              if isinstance(r["metres_from_in_force"], float)]
    return {
        "computed": True,
        "n_operations_compared": len(rows),
        "widest_disagreement_m": round(max(spread), 4) if spread else UNSET,
        "note": "distance at ONE point between the operation in force and each "
                "alternative - a difference between MODELS, not a true error",
        "per_operation": rows,
        "pin_failures": failed or UNSET,
    }


def _metres(dst, a, b):
    da = QgsDistanceArea()
    da.setSourceCrs(dst, QgsCoordinateTransformContext())
    da.setEllipsoid(dst.ellipsoidAcronym() or "WGS84")
    return da.measureLine(QgsPointXY(a), QgsPointXY(b))


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def fmt(obj, indent=0):
    pad = "  " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append("{}{}:".format(pad, k))
                lines.append(fmt(v, indent + 1))
            else:
                lines.append("{}{:<24} {}".format(
                    pad, k + ":", v if v not in ({}, [], None) else UNSET))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            lines.append("{}[{}]".format(pad, i))
            lines.append(fmt(v, indent + 1))
    else:
        lines.append("{}{}".format(pad, obj))
    return "\n".join(lines)


def run():
    app = ensure_qgis()
    project = QgsProject.instance()
    layers = list(project.mapLayers().values())
    mode = "console (project layers)" if layers else "headless (section 10 fixtures)"

    parts = [
        "M0 SPIKE - CRS / TRANSFORM STATE", LINE,
        "generated      {}".format(datetime.now().isoformat(timespec="seconds")),
        "mode           {}".format(mode),
        "QGIS           {}".format(safe(lambda: Qgis.QGIS_VERSION, UNSET)),
        "PROJ           {}".format(safe(_proj_version, UNSET)),
        "python         {}".format(sys.version.split()[0]),
        "project        {}".format(project.fileName() or "<none>"),
        "",
        "Ballpark is read from fallbackOperationOccurred() AFTER a point has been",
        "transformed. A source with no CRS never reaches that call.",
        "",
    ]

    if layers:
        ctx = project.transformContext()
        dst = project.crs()
        for layer in layers:
            parts += [LINE, "LAYER  {}".format(layer.name()), LINE]
            pt = safe(lambda l=layer: (l.extent().center().x(), l.extent().center().y()),
                      (0.0, 0.0))
            if not isinstance(pt, tuple):
                pt = (0.0, 0.0)
            try:
                d = probe_pair(layer.crs(), dst, ctx, pt, layer.name())
                d["layer_id"] = layer.id()      # the durable identity candidate
                parts.append(fmt(d))
            except Exception:
                parts += ["PROBE CRASHED - this is a finding, not a bug to hide:",
                          traceback.format_exc()]
            parts.append("")
    else:
        ctx = QgsCoordinateTransformContext()
        for label, s, d, pt in FIXTURES:
            parts += [LINE, "FIXTURE  {}   {} -> {}".format(label, s, d), LINE]
            try:
                parts.append(fmt(probe_pair(QgsCoordinateReferenceSystem(s),
                                            QgsCoordinateReferenceSystem(d),
                                            ctx, pt, label)))
            except Exception:
                parts += ["PROBE CRASHED - this is a finding, not a bug to hide:",
                          traceback.format_exc()]
            parts.append("")

    text = "\n".join(parts)
    print(text)
    base = os.path.dirname(project.fileName()) or os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(base, "m0_crs_state.txt")
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("\nwritten to {}".format(out_path))
    except Exception as exc:
        print("\ncould not write report: {}".format(exc))
    if app is not None:
        app.exitQgis()
    return text


def _proj_version():
    from qgis.core import QgsProjUtils
    bits = [safe(QgsProjUtils.projVersionMajor, "?"), safe(QgsProjUtils.projVersionMinor, "?")]
    return ".".join(str(b) for b in bits)


run()
