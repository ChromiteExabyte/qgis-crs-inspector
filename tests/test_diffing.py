"""Tests for the change-set engine.

The four rules from `diffing`'s docstring are each pinned by a test, because
each one is a thing that quietly stops being true if nobody checks:

  1. the project is an object like any other
  2. unchanged state writes nothing
  3. the originator is derived, never asserted
  4. one store, two traversals

No QGIS, no Qt — these run on plain Python.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crs_inspector.core.diffing import PROJECT_ID, History, diff, snapshot
from crs_inspector.core.model import (
    CrsRef, GridRef, LayerState, OperationRef, ProjectState,
)

OSGB = CrsRef("EPSG:27700", "OSGB36 / British National Grid",
              "Ordnance Survey of Great Britain 1936")
WGS_GEO = CrsRef("EPSG:4326", "WGS 84", "World Geodetic System 1984 ensemble",
                 is_geographic=True)
WGS_MERC = CrsRef("EPSG:3857", "WGS 84 / Pseudo-Mercator",
                  "World Geodetic System 1984 ensemble")
NO_CRS = CrsRef("", "no CRS set", "", is_valid=False)


def layer(lid, name, source, target, op_name="op", accuracy=2.0, grids=()):
    op = None if op_name is None else OperationRef(op_name, accuracy_m=accuracy,
                                                   grids=grids)
    return LayerState(lid, name, source, target, operation=op)


def project(target, *layers):
    return ProjectState(target=target, layers=tuple(layers))


# --------------------------------------------------------------------------
# Rule 1 — the project is an object with state
# --------------------------------------------------------------------------
def test_project_is_a_tracked_object():
    snap = snapshot(project(OSGB, layer("l1", "a.gpkg", OSGB, OSGB, accuracy=None)))
    assert PROJECT_ID in snap
    assert snap[PROJECT_ID].kind == "project"
    assert "EPSG:27700" in snap[PROJECT_ID].summary


def test_changing_the_project_crs_writes_the_project_as_a_row():
    before = project(OSGB, layer("l1", "uk.gpkg", OSGB, OSGB, accuracy=None))
    after = project(WGS_MERC, layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                                    "OSGB36 to WGS 84 (6)", 2.0))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    ids = {c.object_id for c in cs.changes}
    assert PROJECT_ID in ids, "the project's own change must be recorded, not implied"
    assert "l1" in ids


# --------------------------------------------------------------------------
# Rule 2 — the diff is the primitive
# --------------------------------------------------------------------------
def test_identical_state_writes_nothing():
    state = project(OSGB, layer("l1", "a.gpkg", OSGB, OSGB, accuracy=None))
    assert diff(snapshot(state), snapshot(state), "CS-2") is None


def test_a_layer_that_did_not_move_is_not_recorded():
    """The count has to be computed, or it is just activity logging one level down.

    `unaffected.gpkg` has no CRS, so a project CRS change cannot act on it and
    its state does not move. It must not appear in the change set.
    """
    before = project(
        OSGB,
        layer("moved", "uk.gpkg", OSGB, OSGB, accuracy=None),
        layer("unaffected", "notes.gpkg", NO_CRS, OSGB, op_name=None),
    )
    after = project(
        WGS_MERC,
        layer("moved", "uk.gpkg", OSGB, WGS_MERC, "OSGB36 to WGS 84 (6)", 2.0),
        layer("unaffected", "notes.gpkg", NO_CRS, WGS_MERC, op_name=None),
    )
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    ids = {c.object_id for c in cs.changes}
    assert "moved" in ids
    assert "unaffected" not in ids
    assert cs.note == "1 of 2 layers also changed"


# --------------------------------------------------------------------------
# Rule 3 — the originator is derived
# --------------------------------------------------------------------------
def test_project_policy_change_shows_on_the_project():
    before = project(OSGB, layer("l1", "uk.gpkg", OSGB, OSGB, accuracy=None))
    after = project(WGS_MERC, layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                                    "OSGB36 to WGS 84 (6)", 2.0))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    originators = [c.object_id for c in cs.locally_changed_inputs]
    assert originators == [PROJECT_ID]
    # The layer moved, but its own CRS did not — so it is downstream, and
    # nothing had to record that fact.
    assert [c.object_id for c in cs.without_local_change] == ["l1"]


def test_a_layer_whose_own_crs_changed_shows_a_local_input_change():
    before = project(OSGB, layer("l1", "a.gpkg", OSGB, OSGB, accuracy=None))
    after = project(OSGB, layer("l1", "a.gpkg", WGS_GEO, OSGB,
                                "WGS 84 to OSGB36", 2.0))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    assert [c.object_id for c in cs.locally_changed_inputs] == ["l1"]
    assert cs.note == "no other object changed"


def test_adding_a_layer_is_a_local_input_change():
    before = project(OSGB)
    after = project(OSGB, layer("new", "added.gpkg", WGS_GEO, OSGB))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    change = next(c for c in cs.changes if c.object_id == "new")
    assert change.locally_changed
    assert change.before is None
    assert change.line.startswith("added — ")


def test_removing_a_layer_is_recorded():
    before = project(OSGB, layer("gone", "old.gpkg", WGS_GEO, OSGB))
    after = project(OSGB)
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    change = next(c for c in cs.changes if c.object_id == "gone")
    assert change.after is None
    assert "removed" in change.line


def test_headline_is_an_objects_own_diff():
    before = project(OSGB, layer("l1", "uk.gpkg", OSGB, OSGB, accuracy=None))
    after = project(WGS_MERC, layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                                    "OSGB36 to WGS 84 (6)", 2.0))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    assert cs.headline.startswith("Project CRS")
    assert "→" in cs.headline


# --------------------------------------------------------------------------
# Rule 4 — one store, two traversals
# --------------------------------------------------------------------------
def test_history_records_only_when_something_changed():
    history = History()
    state = project(OSGB, layer("l1", "a.gpkg", OSGB, OSGB, accuracy=None))
    assert history.record(state) is not None      # first read: everything new
    assert history.record(state) is None          # nothing moved
    assert history.record(state) is None
    assert len(history) == 1


def test_the_two_traversals_read_the_same_edges():
    history = History()
    history.record(project(OSGB,
                           layer("l1", "uk.gpkg", OSGB, OSGB, accuracy=None),
                           layer("l2", "notes.gpkg", NO_CRS, OSGB, op_name=None)))
    history.record(project(WGS_MERC,
                           layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                                 "OSGB36 to WGS 84 (6)", 2.0),
                           layer("l2", "notes.gpkg", NO_CRS, WGS_MERC,
                                 op_name=None)))

    assert len(history.all()) == 2                       # git log
    assert len(history.for_object("l1")) == 2            # git log -- uk.gpkg
    # l2 never moved after it was first seen, so its history is one entry.
    assert len(history.for_object("l2")) == 1
    assert len(history.for_object(PROJECT_ID)) == 2


def test_change_set_ids_are_shared_within_one_action():
    history = History()
    history.record(project(OSGB, layer("l1", "uk.gpkg", OSGB, OSGB, accuracy=None)))
    cs = history.record(project(WGS_MERC, layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                                                "OSGB36 to WGS 84 (6)", 2.0)))
    assert cs.ident == "CS-2"
    assert all(c is not None for c in cs.changes)
    for _, change in history.for_object("l1"):
        assert change.object_id == "l1"


def test_a_change_can_have_no_locally_changed_object():
    """A grid file appearing on disk changes no tracked object's own inputs.

    Neither the project nor the layer moved — the environment did. The model
    should say so rather than inventing a culprit, so the change is recorded
    with an empty originator set and the headline falls back to the object that
    actually moved.
    """
    missing = (GridRef("uk_os_OSTN15_NTv2_OSGBtoETRS.tif", is_available=False),)
    present = (GridRef("uk_os_OSTN15_NTv2_OSGBtoETRS.tif", is_available=True),)
    before = project(WGS_MERC, layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                                     "OSGB36 to WGS 84 (6)", 2.0, missing))
    after = project(WGS_MERC, layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                                    "OSGB36 to WGS 84 (6)", 2.0, present))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    assert cs is not None
    assert [c.object_id for c in cs.changes] == ["l1"]
    assert cs.locally_changed_inputs == (), "no captured own input explains this"
    assert cs.headline.startswith("uk.gpkg")
