"""Freshness and replay — the observer being honest about when evidence applies.

The classifier tests establish what an observation *means*. These establish when
it may still be shown. Both are needed: every grading test can pass while the
panel presents yesterday's correct answer against today's inputs.

No QGIS, no Qt.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crs_inspector.core.grading import classify, grade, indicator, reclassify
from crs_inspector.core.model import (
    CrsRef, Evidence, LayerState, OperationRef, ProjectState, Shift,
)
from crs_inspector.core.observation import (
    Capture, Observation, Observer, Presentation, freshness_line,
)

OSGB = CrsRef("EPSG:27700", "OSGB36 / British National Grid",
              "Ordnance Survey of Great Britain 1936")
WGS = CrsRef("EPSG:4326", "WGS 84", "World Geodetic System 1984 ensemble")

CLEAN = ProjectState(target=OSGB, layers=(
    LayerState("l1", "uk.gpkg", OSGB, OSGB,
               operation=OperationRef("noop", accuracy_m=None)),))
AT = datetime(2026, 9, 9, 10, 4)


def ok(observer, state=CLEAN, at=AT):
    return Observation(observer.begin(), at, True, state=state)


def failed(observer, error="PROJ unavailable", at=AT):
    return Observation(observer.begin(), at, False, error=error)


# --------------------------------------------------------------------------
# The behavioural test the wording test could not catch
# --------------------------------------------------------------------------
def test_a_failed_recheck_after_an_input_change_revokes_currency():
    """The dangerous case: a good result still displayed against new inputs.

    The evidence stays readable. It stops being presented as current.
    """
    observer = Observer()
    assert observer.publish(ok(observer))
    assert observer.presentation.is_current

    observer.invalidate()                       # a relevant input changed
    assert observer.publish(failed(observer))   # and re-checking failed

    shown = observer.presentation
    assert shown.has_evidence, "the last good evidence must remain readable"
    assert not shown.is_current, "but it must not be presented as current"
    assert "last successful" in shown.note
    assert "inputs changed since" in freshness_line(shown)


def test_a_failed_capture_is_not_nothing_changed():
    """Failure changes what the plugin can claim to know."""
    observer = Observer()
    observer.publish(ok(observer))
    observer.invalidate()
    observer.publish(failed(observer, "grid vanished"))
    assert "grid vanished" in observer.presentation.note


def test_a_successful_no_op_recheck_updates_freshness_only():
    """Zero revisions, new timestamp. `observed_at` is never semantic state."""
    observer = Observer()
    observer.publish(ok(observer, at=datetime(2026, 9, 9, 10, 0)))
    later = datetime(2026, 9, 9, 11, 30)
    observer.publish(ok(observer, at=later))
    shown = observer.presentation
    assert shown.is_current
    assert shown.observed_at == later
    assert freshness_line(shown) == "checked 11:30"


def test_nothing_observed_yet_is_its_own_state():
    assert freshness_line(Observer().presentation) == "Not checked."


# --------------------------------------------------------------------------
# Generation and session tokens
# --------------------------------------------------------------------------
def test_a_superseded_batch_is_rejected_not_shown():
    observer = Observer()
    pending = observer.begin()                  # batch starts
    observer.invalidate()                       # inputs move while it runs
    stale = Observation(pending, AT, True, state=CLEAN)
    assert observer.publish(stale) is False
    assert not observer.presentation.has_evidence


def test_a_generation_counter_beats_an_input_digest():
    """Inputs can change away and back while work is pending.

    A digest would call that "unchanged" and let a result computed against the
    intermediate state be published as if it described the current one.
    """
    observer = Observer()
    pending = observer.begin()
    observer.invalidate()                       # A -> B
    observer.invalidate()                       # B -> A again, digest identical
    assert observer.publish(Observation(pending, AT, True, state=CLEAN)) is False


def test_results_cannot_cross_a_project_switch():
    observer = Observer()
    pending = observer.begin()
    observer.start_session("session-2")         # another project opened
    assert observer.publish(Observation(pending, AT, True, state=CLEAN)) is False
    assert not observer.presentation.has_evidence, \
        "the previous project's evidence must not describe the new one"


def test_a_project_switch_is_not_a_mass_removal():
    """QGIS fires cleared() both on clear and before reading another project.

    Reading it as "the user deleted every layer" would fabricate history. The
    observation session ends instead.
    """
    observer = Observer()
    observer.publish(ok(observer))
    observer.start_session("session-2")
    shown = observer.presentation
    assert shown.state is None
    assert shown.note == "Not checked."


# --------------------------------------------------------------------------
# Replay: reclassification without reobservation
# --------------------------------------------------------------------------
def test_stored_evidence_reclassifies_with_zero_probe_calls():
    """Same inputs, same raw evidence, newer rules -> a different verdict.

    A rule update is not a new measurement.
    """
    captured = Evidence(
        published_accuracy=-1.0,
        datums_differ=True,
        operation_name="Some unnamed operation",
        names_itself_ballpark=False,
        rule_version=1,          # graded when -1 + differing datums meant ballpark
    )
    again = reclassify(captured)
    assert again.rules_moved
    assert again.original_rule_version == 1
    assert again.current_shift is Shift.CROSS_DATUM_UNKNOWN
    # The evidence object is unchanged: no timestamp advanced, nothing reobserved.
    assert again.evidence is captured
    assert again.evidence.published_accuracy == -1.0


def test_reclassification_cannot_reach_qgis():
    """Structural, not textual: nothing observable is in scope to reach for.

    Scanning the source would only prove the docstring's wording. This proves
    that replaying stored evidence cannot re-observe anything, because the
    module has no QGIS in it and the kernel runs with qgis unimportable.
    """
    import sys
    import crs_inspector.core.grading as grading

    for name, value in vars(grading).items():
        assert not name.startswith("Qgs"), "grading pulled in {}".format(name)
        module = getattr(value, "__module__", "") or ""
        assert not module.startswith("qgis"), (
            "grading reached qgis via {}".format(name))

    saved = {k: v for k, v in sys.modules.items() if k.startswith("qgis")}
    for key in saved:
        sys.modules[key] = None
    try:
        assert classify(
            Evidence(published_accuracy=2.0, datums_differ=True)) is Shift.SHIFT
    finally:
        sys.modules.update(saved)


def test_the_kernel_and_the_live_path_agree():
    """grade() must not drift from classify() — one rule set, two entry points."""
    cases = [
        LayerState("a", "a", OSGB, OSGB,
                   operation=OperationRef("noop", accuracy_m=None,
                                          published_accuracy_raw=-1.0)),
        LayerState("b", "b", WGS, OSGB,
                   operation=OperationRef("Ballpark geographic offset",
                                          accuracy_m=None,
                                          published_accuracy_raw=-1.0)),
        LayerState("c", "c", WGS, OSGB,
                   operation=OperationRef("OSGB36 to WGS 84 (6)", accuracy_m=2.0,
                                          published_accuracy_raw=2.0)),
        LayerState("d", "d", CrsRef("", "none", "", is_valid=False), OSGB),
        LayerState("e", "e", OSGB, WGS, probe_error="boom"),
    ]
    for state in cases:
        verdict = grade(state)
        assert classify(verdict.evidence) is verdict.shift, state.layer_id


def test_change_signature_separates_the_three_kinds_of_movement():
    """Own inputs, raw evidence, and interpretation move independently."""
    base = Evidence(published_accuracy=-1.0, datums_differ=True,
                    operation_name="op A", names_itself_ballpark=False,
                    rule_version=2)
    evidence_moved = Evidence(published_accuracy=-1.0, datums_differ=True,
                              operation_name="op B", names_itself_ballpark=False,
                              rule_version=2)
    rules_moved = Evidence(published_accuracy=-1.0, datums_differ=True,
                           operation_name="op A", names_itself_ballpark=False,
                           rule_version=1)

    def raw(e):
        return (e.published_accuracy, e.datums_differ, e.operation_name,
                e.names_itself_ballpark)

    # Raw-evidence comparison is independent of rule_version even though both
    # live inside the same Evidence structure.
    assert raw(base) != raw(evidence_moved)
    assert raw(base) == raw(rules_moved)
    assert base.rule_version != rules_moved.rule_version
    assert classify(base) is classify(rules_moved), \
        "same evidence classifies the same way regardless of what it was graded under"
