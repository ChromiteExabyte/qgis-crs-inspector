"""The copied record: a timeline first, then every layer, however small.

Written against first real use, where the ask was "Bing Satellite, loaded at this
time, CRS such-and-such — so it can all be pasted into someone's own LLM". Each
test pins one thing that request needs, and one thing the record must never
claim.

No QGIS, no Qt.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from crs_inspector.core.diffing import History
from crs_inspector.core.grading import crs_text, grade, plain_detail
from crs_inspector.core.model import (
    CrsRef, LayerState, OperationRef, ProjectState, Shift,
)
from crs_inspector.core.serialize import full_record, timeline_lines

WEB = CrsRef("EPSG:3857", "WGS 84 / Pseudo-Mercator", "World Geodetic System 1984",
             definition="PROJCRS[web]")
WGS = CrsRef("EPSG:4326", "WGS 84", "World Geodetic System 1984",
             is_geographic=True, definition="GEOGCRS[wgs]")
UTM = CrsRef("EPSG:32610", "WGS 84 / UTM zone 10N", "World Geodetic System 1984",
             definition="PROJCRS[utm]")
NAD27 = CrsRef("EPSG:4267", "NAD27", "North American Datum 1927",
               is_geographic=True, definition="GEOGCRS[nad27]")
NO_CRS = CrsRef("", "no CRS set", "", is_valid=False)

T0 = datetime(2026, 10, 13, 9, 0, 0)
T1 = datetime(2026, 10, 13, 9, 5, 30)
T2 = datetime(2026, 10, 13, 9, 7, 12)


def _layer(layer_id, name, source, target, op="noop", accuracy=None, raw=None):
    operation = None if op is None else OperationRef(
        op, accuracy_m=accuracy, published_accuracy_raw=raw)
    return LayerState(layer_id, name, source, target, operation=operation)


def _satellite(target):
    return _layer("sat", "Bing Satellite", WEB, target)


def _roads(target):
    return _layer("roads", "roads.gpkg", WGS, target)


def _history_of_a_session():
    history = History()
    history.record(ProjectState(target=WEB, layers=(_satellite(WEB), _roads(WEB))),
                   at=T0)
    history.record(ProjectState(target=UTM, layers=(_satellite(UTM), _roads(UTM))),
                   at=T1)
    ballpark = _layer("old", "old_survey.shp", NAD27, UTM,
                      op="Ballpark geographic offset", raw=-1.0)
    history.record(ProjectState(target=UTM, layers=(
        _satellite(UTM), _roads(UTM), ballpark)), at=T2)
    return history


# -- the timeline --------------------------------------------------------
def test_every_layer_already_present_is_listed_with_its_crs_and_time():
    text = "\n".join(timeline_lines(_history_of_a_session()))
    assert "09:00:00" in text
    assert "Started watching: 2 layers already in the project." in text
    assert "Bing Satellite — EPSG:3857 WGS 84 / Pseudo-Mercator" in text
    assert "roads.gpkg — EPSG:4326 WGS 84" in text


def test_a_layer_present_at_start_is_never_called_added():
    """The plugin cannot know when a layer it found was loaded."""
    baseline = timeline_lines(_history_of_a_session())[:6]
    assert not any("added" in line.lower() for line in baseline)


def test_a_project_crs_change_reads_as_one_plain_sentence():
    lines = timeline_lines(_history_of_a_session())
    at = lines.index("09:05:30  Project CRS changed")
    assert lines[at + 1].strip() == "from: EPSG:3857 WGS 84 / Pseudo-Mercator"
    assert lines[at + 2].strip() == "to:   EPSG:32610 WGS 84 / UTM zone 10N"


def test_the_timeline_lists_layers_by_name_not_by_internal_id():
    lines = timeline_lines(_history_of_a_session())
    names = [line.strip().split(" — ")[0] for line in lines[1:6]
             if " — " in line and not line.startswith(" " * 14)]
    assert names == ["Bing Satellite", "roads.gpkg"]


def test_even_a_projection_only_change_is_recorded_for_each_layer():
    """Cognitive accessibility "no matter how small": same datum still shows."""
    text = "\n".join(timeline_lines(_history_of_a_session()))
    assert "was: same CRS as the project — no conversion" in text
    assert "now: same datum as the project — map projection change only" in text


def test_a_layer_added_later_says_added_with_what_happens_to_it():
    text = "\n".join(timeline_lines(_history_of_a_session()))
    assert "09:07:12  Layer added: old_survey.shp — EPSG:4267 NAD27" in text
    assert "no datum shift was available — converted without one" in text


def test_the_timeline_runs_oldest_first():
    lines = timeline_lines(_history_of_a_session())
    stamps = [line[:8] for line in lines if line[:2].isdigit() and line[2] == ":"]
    assert stamps == ["09:00:00", "09:05:30", "09:07:12"]


def test_a_removal_names_what_was_there():
    history = _history_of_a_session()
    history.record(ProjectState(target=UTM, layers=(_satellite(UTM),
                                                    _roads(UTM))),
                   at=datetime(2026, 10, 13, 9, 9, 0))
    text = "\n".join(timeline_lines(history))
    assert "Layer removed: old_survey.shp (was EPSG:4267 NAD27)" in text


def test_a_new_day_starts_a_new_dated_block():
    history = _history_of_a_session()
    history.record(ProjectState(target=WEB, layers=(_satellite(WEB),
                                                    _roads(WEB))),
                   at=datetime(2026, 10, 14, 8, 0, 0))
    lines = timeline_lines(history)
    assert "2026-10-13" in lines and "2026-10-14" in lines


def test_an_empty_history_says_so_in_words():
    assert timeline_lines(History()) == ["(nothing recorded in this session yet)"]
    assert timeline_lines(None) == ["(nothing recorded in this session yet)"]


# -- the full record -----------------------------------------------------
def _current_state():
    ballpark = _layer("old", "old_survey.shp", NAD27, UTM,
                      op="Ballpark geographic offset", raw=-1.0)
    return ProjectState(target=UTM, layers=(_satellite(UTM), _roads(UTM), ballpark))


def test_the_record_shows_the_project_crs_and_when_it_was_checked():
    text = full_record(_current_state(), _history_of_a_session(), "site.qgz",
                       observed_at=T2, made_at=T2)
    assert "CRS INSPECTOR — PROJECT RECORD" in text
    assert "project CRS:    EPSG:32610 WGS 84 / UTM zone 10N" in text
    assert "last checked:   2026-10-13 09:07:12 (current)" in text
    assert "TIMELINE" in text and "CURRENT STATE, LAYER BY LAYER" in text
    assert text.index("TIMELINE") < text.index("CURRENT STATE")


def test_the_record_says_its_times_are_noticing_times():
    text = full_record(_current_state(), _history_of_a_session(), observed_at=T2)
    assert "when CRS Inspector noticed" in text


def test_a_stale_observation_says_so_in_the_record():
    text = full_record(_current_state(), _history_of_a_session(),
                       observed_at=T2, is_current=False,
                       freshness_note="Inputs have changed since this check.")
    assert "NOT CURRENT: Inputs have changed since this check." in text
    assert "(current)" not in text


def test_the_record_never_uses_the_word_assessment():
    """It read as more authority than operation metadata can carry."""
    text = full_record(_current_state(), _history_of_a_session(), observed_at=T2)
    assert "assessment" not in text.lower()


@pytest.mark.parametrize("shift", list(Shift), ids=lambda s: s.value)
def test_every_state_survives_the_full_record(shift):
    from tests.test_exhaustive import _layer_reaching
    layer = _layer_reaching(shift)
    state = ProjectState(target=layer.target, layers=(layer,))
    history = History()
    history.record(state, at=T0)
    text = full_record(state, history, "p.qgz", observed_at=T0)
    assert "UNMAPPED STATE" not in text
    assert text.count(layer.layer_name) >= 2      # timeline and layer block


# -- the plain line ------------------------------------------------------
def test_identical_crs_and_same_datum_are_told_apart():
    same = _layer("a", "a", UTM, UTM)
    reprojected = _layer("b", "b", WGS, UTM)
    assert plain_detail(same, grade(same)) == "same CRS as the project — no conversion"
    assert plain_detail(reprojected, grade(reprojected)) == (
        "same datum as the project — map projection change only")


def test_a_layer_with_no_crs_says_what_qgis_does_with_it():
    layer = _layer("n", "n", NO_CRS, UTM, op=None)
    assert "used as if already in the project CRS" in plain_detail(layer, grade(layer))


def test_crs_text_names_the_epoch_when_one_is_set():
    from dataclasses import replace
    assert crs_text(replace(WGS, epoch=2024.5)) == "EPSG:4326 WGS 84 (epoch 2024.5)"
    assert crs_text(NO_CRS) == "no CRS"
