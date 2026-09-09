"""Generate the acceptance fixture project.

Every case here was established by the M0 spike against a real installation.
None is invented and assumed to behave a certain way elsewhere — which is why
the expected classifications are stated together with the resource condition
they depend on.

    "<OSGeo4W>/bin/python-qgis.bat" tools/make_fixture.py
"""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixture"

from qgis.core import (QgsApplication, QgsProject, QgsVectorLayer, QgsFeature,
                       QgsGeometry, QgsPointXY, QgsFields, QgsField,
                       QgsVectorFileWriter, QgsCoordinateTransformContext,
                       QgsCoordinateReferenceSystem as CRS, QgsDatumTransform,
                       Qgis)
from qgis.PyQt.QtCore import QVariant

app = QgsApplication([], False)
QgsApplication.setPrefixPath(os.path.join(os.environ["OSGEO4W_ROOT"], "apps", "qgis"), True)
app.initQgis()

OUT.mkdir(exist_ok=True)
PROJECT_CRS = "EPSG:6318"          # NAD83(2011)

CASES = [
    ("control_projection_only", PROJECT_CRS, (-96.0, 39.0),
     "same datum as the project: expect NO datum shift. The control."),
    ("case_ballpark_nad27", "EPSG:4267", (-96.0, 39.0),
     "NAD27 -> NAD83(2011): expect BALLPARK *if* the NADCON5 grids are absent."),
    ("case_published_shift", "EPSG:4326", (-96.0, 39.0),
     "WGS 84 -> NAD83(2011): expect a shift with a published accuracy."),
]

def write_gpkg(name, authid, xy):
    path = OUT / (name + ".gpkg")
    if path.exists():
        path.unlink()
    fields = QgsFields()
    fields.append(QgsField("note", QVariant.String))
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = name
    try:
        point_type = Qgis.WkbType.Point
    except AttributeError:                       # Qt5 / QGIS 3.x spelling
        from qgis.core import QgsWkbTypes
        point_type = QgsWkbTypes.Point
    writer = QgsVectorFileWriter.create(
        str(path), fields, point_type, CRS(authid),
        QgsCoordinateTransformContext(), opts)
    feat = QgsFeature(fields)
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*xy)))
    feat.setAttributes([name])
    writer.addFeature(feat)
    del writer
    return path

project = QgsProject.instance()
project.clear()
project.setCrs(CRS(PROJECT_CRS))
lines = []
for name, authid, xy, note in CASES:
    path = write_gpkg(name, authid, xy)
    layer = QgsVectorLayer("{}|layername={}".format(path, name), name, "ogr")
    if not layer.isValid():
        print("FAILED to load", name); continue
    project.addMapLayer(layer)
    lines.append("| `{}` | {} | {} |".format(name, authid, note))

qgz = OUT / "crs_inspector_fixture.qgz"
project.write(str(qgz))
print("wrote", qgz)

# Record the resource conditions this machine actually had.
avail = []
for src, dst in (("EPSG:4267", PROJECT_CRS), ("EPSG:4326", PROJECT_CRS)):
    ops = QgsDatumTransform.operations(CRS(src), CRS(dst))
    for o in ops:
        for g in o.grids:
            avail.append("{} -> {}: grid {} {}".format(
                src, dst, g.shortName, "present" if g.isAvailable else "ABSENT"))
readme = OUT / "README.md"
readme.write_text(
    "# Acceptance fixture\n\n"
    "`crs_inspector_fixture.qgz` plus three GeoPackages. Project CRS **{}**.\n\n"
    "| Layer | CRS | Expected |\n|---|---|---|\n{}\n\n"
    "## Resource conditions when generated\n\n"
    "Classification depends on what PROJ can find, which differs per install.\n"
    "A clean profile does not mean an identical transformation environment, so\n"
    "record these again on the machine under test rather than assuming them.\n\n"
    "```\nQGIS {}\n{}\n```\n".format(
        PROJECT_CRS, "\n".join(lines),
        __import__("qgis.core", fromlist=["Qgis"]).Qgis.QGIS_VERSION,
        "\n".join(sorted(set(avail))) or "no grid-dependent operations listed"),
    encoding="utf-8")
print("wrote", readme)
app.exitQgis()
