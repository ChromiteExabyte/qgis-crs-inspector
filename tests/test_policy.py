"""Transformation-policy fingerprinting, and the invariants it has to preserve.

The project's authored policy is part of the project's own inputs, so editing it
must show up as a project change — including when it changes no output today.
But it must NOT ripple into every layer, or the diff rule collapses back into
activity logging.

Those two requirements pull against each other, which is why each one gets a
test rather than a comment.

No QGIS, no Qt.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crs_inspector.core.diffing import PROJECT_ID, diff, snapshot
from crs_inspector.core.fingerprint import (
    ContextEntry, canonical_encoding, context_fingerprint, relevant_entries,
)
from crs_inspector.core.model import CrsRef, LayerState, OperationRef, ProjectState

OSGB = CrsRef("EPSG:27700", "OSGB36 / British National Grid",
              "Ordnance Survey of Great Britain 1936")
WGS_MERC = CrsRef("EPSG:3857", "WGS 84 / Pseudo-Mercator",
                  "World Geodetic System 1984 ensemble")

OSGB_WKT = 'PROJCRS["OSGB36 / British National Grid",...ID["EPSG",27700]]'
WGS_WKT = 'GEOGCRS["WGS 84",ENSEMBLE[...],ID["EPSG",4326]]'
NAD_WKT = 'GEOGCRS["NAD27",DATUM["North American Datum 1927"],ID["EPSG",4267]]'


def entry(src=OSGB_WKT, dst=WGS_WKT, op="+proj=noop", fallback=True,
          src_id="EPSG:27700", dst_id="EPSG:4326"):
    return ContextEntry(src, dst, op, fallback, src_id, dst_id)


def layer(lid, name, source, target, op_name="op", accuracy=2.0):
    return LayerState(lid, name, source, target,
                      operation=OperationRef(op_name, accuracy_m=accuracy))


def project(target, entries=(), *layers):
    return ProjectState(target=target, layers=tuple(layers),
                        context_entries=tuple(entries))


# --------------------------------------------------------------------------
# The fingerprint itself
# --------------------------------------------------------------------------
def test_fingerprint_is_stable_and_order_independent():
    a, b = entry(), entry(src=NAD_WKT, src_id="EPSG:4267")
    assert context_fingerprint([a, b]) == context_fingerprint([b, a])
    assert context_fingerprint([a]) != context_fingerprint([a, b])


def test_fingerprint_preserves_direction():
    """A→B is not the same authored policy as B→A."""
    forward = entry(src=OSGB_WKT, dst=WGS_WKT)
    reverse = entry(src=WGS_WKT, dst=OSGB_WKT)
    assert context_fingerprint([forward]) != context_fingerprint([reverse])


def test_stored_empty_operation_differs_from_no_entry():
    """"" is an authored value — use default behaviour — not an absent entry."""
    assert context_fingerprint([entry(op="")]) != context_fingerprint([])
    assert context_fingerprint([entry(op="")]) != context_fingerprint([entry()])


def test_fallback_flag_alone_changes_the_fingerprint():
    """A fallback-policy-only edit is a real authored change.

    It is also invisible in `coordinateOperations()`, which is why the fallback
    flag is extracted from the XML rather than that convenience map.
    """
    assert context_fingerprint([entry(fallback=True)]) != \
        context_fingerprint([entry(fallback=False)])


def test_unnamed_custom_crss_are_not_conflated():
    """Two custom CRSs can share an empty or non-portable authid.

    Identity comes from the full definition; the authid is metadata only,
    because QGIS warns USER: ids differ between machines and profiles.
    """
    one = ContextEntry('PROJCRS["custom A",PARAMETER["False easting",100]]',
                       WGS_WKT, "+proj=noop", True, "", "EPSG:4326")
    two = ContextEntry('PROJCRS["custom B",PARAMETER["False easting",500]]',
                       WGS_WKT, "+proj=noop", True, "", "EPSG:4326")
    assert context_fingerprint([one]) != context_fingerprint([two])


def test_operation_string_is_treated_as_opaque():
    """Never reordered or 'simplified' — we cannot prove two spellings agree."""
    a = entry(op="+proj=pipeline +step +proj=a +step +proj=b")
    b = entry(op="+proj=pipeline +step +proj=b +step +proj=a")
    assert context_fingerprint([a]) != context_fingerprint([b])


def test_fingerprint_is_schema_versioned():
    assert context_fingerprint([entry()]).startswith("1:")
    assert "schema" in canonical_encoding([entry()])


def test_unobserved_context_is_distinct_from_empty_one():
    assert context_fingerprint(None) != context_fingerprint([])


def test_relevant_entries_matches_either_direction():
    e = entry()
    assert relevant_entries([e], "EPSG:27700", "EPSG:4326") == (e,)
    assert relevant_entries([e], "EPSG:4326", "EPSG:27700") == (e,)
    assert relevant_entries([e], "EPSG:4267", "EPSG:4326") == ()


# --------------------------------------------------------------------------
# The two requirements that pull against each other
# --------------------------------------------------------------------------
def test_editing_an_unused_override_changes_the_project_and_no_layer():
    """The headline regression test.

    An authored edit to a CRS pair no layer uses is a real project change — it
    must be recorded. It must also not touch a single layer row, or the global
    policy hash has leaked into layer state and re-created the fan-out that the
    diff rule exists to prevent.
    """
    lyr = layer("l1", "uk.gpkg", OSGB, WGS_MERC, "OSGB36 to WGS 84 (6)", 2.0)
    before = project(WGS_MERC, [entry()], lyr)
    after = project(WGS_MERC,
                    [entry(), entry(src=NAD_WKT, src_id="EPSG:4267",
                                    op="+proj=somethingelse")],
                    lyr)

    cs = diff(snapshot(before), snapshot(after), "CS-2")
    assert cs is not None, "an authored policy edit must be recorded"
    assert [c.object_id for c in cs.changes] == [PROJECT_ID]
    assert cs.note == "no other object changed"


def test_a_policy_edit_is_recorded_even_when_no_output_moved():
    """Configuration is state. 'It changed nothing today' is not a reason to
    forget that it was edited — that is what makes it findable later."""
    lyr = layer("l1", "uk.gpkg", OSGB, WGS_MERC, "OSGB36 to WGS 84 (6)", 2.0)
    before = project(WGS_MERC, [entry(fallback=True)], lyr)
    after = project(WGS_MERC, [entry(fallback=False)], lyr)
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    assert cs is not None
    assert cs.locally_changed_inputs[0].object_id == PROJECT_ID


def test_operation_change_is_recorded_even_when_the_grade_is_unchanged():
    """Severity is not the unit of record. Provenance is."""
    before = project(WGS_MERC, [],
                     layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                           "OSGB36 to WGS 84 (6)", 2.0))
    after = project(WGS_MERC, [],
                    layer("l1", "uk.gpkg", OSGB, WGS_MERC,
                          "OSGB36 to WGS 84 (2)", 2.0))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    assert cs is not None, "a different operation at the same grade still changed"
    assert [c.object_id for c in cs.changes] == ["l1"]


# --------------------------------------------------------------------------
# The invariant
# --------------------------------------------------------------------------
def test_own_input_change_always_implies_a_state_change():
    """self_key moving must imply state_key moving, for every tracked object.

    If it did not, an object could change its own inputs without producing a
    row, and an edited-but-unused setting would silently vanish from history.
    Every field of self_key must therefore be represented in state_key.
    """
    cases = [
        # (before, after) pairs, each moving exactly one own-input field
        (project(OSGB, [entry()]), project(WGS_MERC, [entry()])),          # project CRS
        (project(OSGB, [entry()]), project(OSGB, [entry(op="+proj=x")])),  # policy
        (project(OSGB, [], layer("l1", "a", OSGB, OSGB)),
         project(OSGB, [], layer("l1", "a", WGS_MERC, OSGB))),             # layer CRS
    ]
    for before, after in cases:
        first, second = snapshot(before), snapshot(after)
        for object_id, was in first.items():
            now = second.get(object_id)
            if now is None:
                continue
            if was.self_key != now.self_key:
                assert was.state_key != now.state_key, (
                    "{} changed its own inputs without changing state".format(
                        object_id))
