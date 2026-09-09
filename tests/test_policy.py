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

from crs_inspector.core.diffing import PROJECT_ID, History, diff, snapshot
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


# --------------------------------------------------------------------------
# Coordinate epoch: identity, and the classifier boundary that goes with it.
# An epoch-blind key does not lose a change's explanation — it prevents the
# change being recorded at all, which is the worse failure.
# --------------------------------------------------------------------------
from crs_inspector.core.fingerprint import crs_identity
from crs_inspector.core.grading import classify, grade
from crs_inspector.core.model import Shift

WGS_2020 = CrsRef("EPSG:9755", "WGS 84 (G2296)", "World Geodetic System 1984 ensemble",
                  is_dynamic=True, epoch=2020.0, definition='GEOGCRS["WGS 84 (G2296)"]')
WGS_2010 = CrsRef("EPSG:9755", "WGS 84 (G2296)", "World Geodetic System 1984 ensemble",
                  is_dynamic=True, epoch=2010.0, definition='GEOGCRS["WGS 84 (G2296)"]')
WGS_NONE = CrsRef("EPSG:9755", "WGS 84 (G2296)", "World Geodetic System 1984 ensemble",
                  is_dynamic=True, epoch=None, definition='GEOGCRS["WGS 84 (G2296)"]')


def test_a_layer_epoch_only_change_is_recorded():
    """Operation and accuracy held constant; only the epoch moves."""
    before = project(OSGB, [], layer("l1", "a.gpkg", WGS_2010, OSGB, "op", 2.0))
    after = project(OSGB, [], layer("l1", "a.gpkg", WGS_2020, OSGB, "op", 2.0))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    assert cs is not None, "an epoch-only edit must be observable"
    assert [c.object_id for c in cs.changes] == ["l1"]
    assert cs.locally_changed_inputs[0].object_id == "l1", \
        "the layer's own input moved, so it is a local change"


def test_a_project_epoch_only_change_is_recorded_on_the_project():
    before = project(WGS_2010, [], layer("l1", "a.gpkg", OSGB, WGS_2010, "op", 2.0))
    after = project(WGS_2020, [], layer("l1", "a.gpkg", OSGB, WGS_2020, "op", 2.0))
    cs = diff(snapshot(before), snapshot(after), "CS-2")
    assert cs is not None
    assert PROJECT_ID in {c.object_id for c in cs.changes}


def test_setting_or_clearing_an_epoch_is_a_transition_not_a_collapse():
    unset_to_set = diff(snapshot(project(OSGB, [], layer("l1", "a", WGS_NONE, OSGB))),
                        snapshot(project(OSGB, [], layer("l1", "a", WGS_2020, OSGB))),
                        "CS-2")
    set_to_unset = diff(snapshot(project(OSGB, [], layer("l1", "a", WGS_2020, OSGB))),
                        snapshot(project(OSGB, [], layer("l1", "a", WGS_NONE, OSGB))),
                        "CS-3")
    assert unset_to_set is not None and set_to_unset is not None


def test_epoch_is_identity_but_never_datum():
    """A different epoch is not a different datum; conflating them would make a
    temporal change look like a reference-frame change."""
    assert WGS_2010.datum_key == WGS_2020.datum_key
    assert crs_identity(WGS_2010.definition, WGS_2010.epoch) != \
        crs_identity(WGS_2020.definition, WGS_2020.epoch)


def test_custom_crss_sharing_labels_have_different_identities():
    a = crs_identity('PROJCRS["site",PARAMETER["False easting",100]]', None, "USER:100001")
    b = crs_identity('PROJCRS["site",PARAMETER["False easting",500]]', None, "USER:100001")
    assert a != b, "identity is the definition, not the label or the USER id"


def test_an_unreadable_definition_is_not_an_absent_one():
    assert crs_identity("", None, "EPSG:4326", definition_read=False) != \
        crs_identity("", None, "EPSG:4326", definition_read=True)


def test_identical_reassessment_adds_no_revision():
    state = project(OSGB, [], layer("l1", "a.gpkg", WGS_2020, OSGB, "op", 2.0))
    assert diff(snapshot(state), snapshot(state), "CS-2") is None


# --- the classifier boundary --------------------------------------------
def test_matching_datums_with_disagreeing_epochs_are_not_projection_only():
    """A matching datum name is not proof the operation is projection-only."""
    verdict = grade(LayerState("l1", "a.gpkg", WGS_2010, WGS_2020,
                               operation=OperationRef("noop", accuracy_m=None)))
    assert verdict.shift is Shift.TEMPORAL_UNASSESSED
    assert "not assessed" in verdict.headline
    assert classify(verdict.evidence) is Shift.TEMPORAL_UNASSESSED, \
        "replay must reach the same conclusion from stored evidence"


def test_no_declared_epochs_is_not_a_temporal_warning():
    """EPSG:4326 and EPSG:3857 are both dynamic and normally carry no epoch.

    Flagging that would mark essentially every Web Mercator project — a warning
    nobody can act on, which is worse than silence.
    """
    verdict = grade(LayerState("l1", "a.gpkg", WGS_NONE, WGS_NONE,
                               operation=OperationRef("noop", accuracy_m=None)))
    assert verdict.shift is Shift.NO_SHIFT
    assert not verdict.is_alarming


def test_a_temporal_gap_is_unchecked_not_flagged():
    from crs_inspector.core.grading import indicator
    verdicts = [grade(LayerState("l1", "a", WGS_2010, WGS_2020,
                                 operation=OperationRef("noop", accuracy_m=None)))]
    assert "1 unchecked" in indicator(verdicts)
    assert "flagged" not in indicator(verdicts)


# --------------------------------------------------------------------------
# A lost read is not an observed change. The acceptance sequence is
# observe A -> fail -> observe A again: the failure and the recovery may be
# reported, but the authored policy must never be said to have changed.
# --------------------------------------------------------------------------
def test_observe_fail_observe_reports_no_policy_change():
    lyr = layer("l1", "uk.gpkg", OSGB, WGS_MERC, "OSGB36 to WGS 84 (6)", 2.0)
    policy_a = [entry()]

    history = History()
    assert history.record(project(WGS_MERC, policy_a, lyr)) is not None   # baseline

    # The read fails: context_entries is None, not ().
    unreadable = ProjectState(target=WGS_MERC, layers=(lyr,), context_entries=None)
    assert history.record(unreadable) is None, (
        "a failed policy read must not be recorded as a policy change")

    # And the same policy observed again is still not a change.
    assert history.record(project(WGS_MERC, policy_a, lyr)) is None
    assert len(history) == 1, "only the baseline was ever a real change"


def test_an_unreadable_policy_is_not_an_empty_one():
    """Observed-and-empty is an authored state. Unreadable is an absence of
    evidence, and the two must not digest alike."""
    assert context_fingerprint(None) != context_fingerprint([])


def test_a_real_policy_change_across_a_failed_read_is_still_caught():
    """Carrying the last known value forward must not swallow a genuine edit."""
    lyr = layer("l1", "uk.gpkg", OSGB, WGS_MERC, "OSGB36 to WGS 84 (6)", 2.0)
    history = History()
    history.record(project(WGS_MERC, [entry()], lyr))
    history.record(ProjectState(target=WGS_MERC, layers=(lyr,), context_entries=None))
    cs = history.record(project(WGS_MERC, [entry(op="+proj=somethingelse")], lyr))
    assert cs is not None, "the edit is still visible once the read recovers"
    assert cs.locally_changed_inputs[0].object_id == PROJECT_ID


def test_carry_forward_does_not_apply_without_a_previous_value():
    """Nothing to carry forward from: the first observation may be unreadable."""
    lyr = layer("l1", "uk.gpkg", OSGB, WGS_MERC, "OSGB36 to WGS 84 (6)", 2.0)
    history = History()
    cs = history.record(ProjectState(target=WGS_MERC, layers=(lyr,),
                                     context_entries=None))
    assert cs is not None and cs.is_baseline
