"""Every Shift state must survive every consumer that formats it.

This file exists because the same defect landed twice. `CROSS_DATUM_UNKNOWN`
was missing from the serializer's wording map and raised `KeyError` on export;
the fix added an exhaustiveness test *for the serializer*. `TEMPORAL_UNASSESSED`
then landed and was missing from history's map, raising `KeyError` inside
`snapshot()` — which is on the path of every assessment, so one temporal layer
killed the whole refresh rather than one view.

A wording map per consumer is a place to forget per consumer. So the enum drives
the test, not a hand-written list, and every consumer is walked: adding a state
without wiring it up fails here.

No QGIS, no Qt.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from crs_inspector.core.diffing import History, _layer_summary, snapshot
from crs_inspector.core.grading import classify, grade
from crs_inspector.core.model import (
    CrsRef, LayerState, OperationRef, ProjectState, Shift,
)
from crs_inspector.core.serialize import to_text

OSGB = CrsRef("EPSG:27700", "OSGB36 / British National Grid",
              "Ordnance Survey of Great Britain 1936", definition="PROJCRS[osgb]")
NAD27 = CrsRef("EPSG:4267", "NAD27", "North American Datum 1927",
               is_geographic=True, definition="GEOGCRS[nad27]")
NAD83 = CrsRef("EPSG:6318", "NAD83(2011)", "NAD83 (NSRS2011)",
               is_geographic=True, definition="GEOGCRS[nad83]")
DYN_2020 = CrsRef("EPSG:9755", "WGS 84 (G2296)", "WGS 84 ensemble",
                  is_dynamic=True, epoch=2020.0, definition="GEOGCRS[wgs]")
DYN_2025 = CrsRef("EPSG:9755", "WGS 84 (G2296)", "WGS 84 ensemble",
                  is_dynamic=True, epoch=2025.0, definition="GEOGCRS[wgs]")
NO_CRS = CrsRef("", "no CRS set", "", is_valid=False)
NO_TARGET = CrsRef("", "invalid", "", is_valid=False)


def _layer_reaching(shift: Shift) -> LayerState:
    """A LayerState that genuinely grades to `shift` — no forced verdicts."""
    cases = {
        Shift.NO_SHIFT: LayerState(
            "a", "same-frame.gpkg", OSGB, OSGB,
            operation=OperationRef("noop", accuracy_m=None)),
        Shift.SHIFT: LayerState(
            "b", "shifted.gpkg", NAD27, NAD83,
            operation=OperationRef("NAD27 to NAD83", accuracy_m=2.0,
                                   published_accuracy_raw=2.0)),
        Shift.BALLPARK: LayerState(
            "c", "ballpark.gpkg", NAD27, NAD83,
            operation=OperationRef("Ballpark geographic offset",
                                   accuracy_m=None, published_accuracy_raw=-1.0)),
        Shift.CROSS_DATUM_UNKNOWN: LayerState(
            "d", "unpublished.gpkg", NAD27, NAD83,
            operation=OperationRef("Some unnamed operation",
                                   accuracy_m=None, published_accuracy_raw=-1.0)),
        Shift.TEMPORAL_UNASSESSED: LayerState(
            "e", "epoch.gpkg", DYN_2020, DYN_2025,
            operation=OperationRef("noop", accuracy_m=None)),
        Shift.NO_CRS: LayerState("f", "nocrs.gpkg", NO_CRS, OSGB),
        Shift.UNKNOWN: LayerState("g", "broken.gpkg", OSGB, NO_TARGET,
                                  operation=OperationRef("op", accuracy_m=2.0)),
    }
    return cases[shift]


@pytest.mark.parametrize("shift", list(Shift), ids=lambda s: s.value)
def test_every_state_is_reachable_from_a_real_layer(shift):
    """If a state cannot be produced, the table below proves nothing."""
    assert grade(_layer_reaching(shift)).shift is shift


@pytest.mark.parametrize("shift", list(Shift), ids=lambda s: s.value)
def test_every_state_survives_history(shift):
    """snapshot() is on the path of every assessment, so a gap here is fatal."""
    layer = _layer_reaching(shift)
    state = ProjectState(target=layer.target, layers=(layer,))
    summary = _layer_summary(layer)
    assert summary and "unmapped" not in summary
    assert snapshot(state)
    assert History().record(state) is not None


@pytest.mark.parametrize("shift", list(Shift), ids=lambda s: s.value)
def test_every_state_survives_the_text_record(shift):
    layer = _layer_reaching(shift)
    text = to_text(ProjectState(target=layer.target, layers=(layer,)), "p.qgz")
    assert "UNMAPPED STATE" not in text
    assert layer.layer_name in text


@pytest.mark.parametrize("shift", list(Shift), ids=lambda s: s.value)
def test_every_state_round_trips_through_the_kernel(shift):
    """grade() and classify() must not drift apart for any state."""
    verdict = grade(_layer_reaching(shift))
    assert classify(verdict.evidence) is verdict.shift


def test_a_project_mixing_every_state_survives_end_to_end():
    """The integration the per-consumer tests do not cover on their own."""
    layers = tuple(_layer_reaching(s) for s in Shift)
    state = ProjectState(target=OSGB, layers=layers)
    assert History().record(state) is not None
    text = to_text(state, "mixed.qgz")
    for layer in layers:
        assert layer.layer_name in text
