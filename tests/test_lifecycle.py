"""Observer lifecycle: discovery, re-entry, and session boundaries.

These cover the parts of the first-GUI-session acceptance list that do not need
a visible window. The remaining items — the dock actually appearing, and the two
Qt targets — are in `spike/acceptance_session.md`.

No QGIS, no Qt.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crs_inspector.core.diffing import PROJECT_ID, History
from crs_inspector.core.model import CrsRef, LayerState, OperationRef, ProjectState
from crs_inspector.core.observation import Observation, Observer

OSGB = CrsRef("EPSG:27700", "OSGB36 / British National Grid",
              "Ordnance Survey of Great Britain 1936")
WGS = CrsRef("EPSG:3857", "WGS 84 / Pseudo-Mercator",
             "World Geodetic System 1984 ensemble")


def layer(lid, name, source, target, accuracy=2.0):
    return LayerState(lid, name, source, target,
                      operation=OperationRef("op", accuracy_m=accuracy))


def project(target, *layers):
    return ProjectState(target=target, layers=tuple(layers))


# --------------------------------------------------------------------------
def test_enabling_on_an_existing_project_discovers_rather_than_creates():
    """Those layers were there before the plugin was.

    Recording them as "added" would put an event in the history that never
    happened — the plugin's own arrival is not a project edit.
    """
    history = History()
    cs = history.record(project(OSGB,
                                layer("l1", "uk.gpkg", OSGB, OSGB, None),
                                layer("l2", "gps.gpkg", WGS, OSGB)))
    assert cs.is_baseline
    assert cs.note == "initial assessment of 2 layers"
    for change in cs.changes:
        assert change.first_observation
        assert change.line.startswith("first observed — ")
        assert "added" not in change.line


def test_a_later_addition_is_still_an_addition():
    """The baseline distinction must not swallow real additions."""
    history = History()
    history.record(project(OSGB, layer("l1", "uk.gpkg", OSGB, OSGB, None)))
    cs = history.record(project(OSGB,
                                layer("l1", "uk.gpkg", OSGB, OSGB, None),
                                layer("l2", "new.gpkg", WGS, OSGB)))
    assert not cs.is_baseline
    change = next(c for c in cs.changes if c.object_id == "l2")
    assert change.line.startswith("added — ")


def test_repeated_assessment_adds_no_revisions_but_moves_freshness():
    """The acceptance requirement: 0 revisions, updated last-successful-check."""
    history, observer = History(), Observer()
    state = project(OSGB, layer("l1", "uk.gpkg", OSGB, OSGB, None))

    for stamp in (datetime(2026, 9, 9, 10, 0), datetime(2026, 9, 9, 10, 5),
                  datetime(2026, 9, 9, 10, 9)):
        observer.publish(Observation(observer.begin(), stamp, True, state=state))
        history.record(state)

    assert len(history) == 1, "only the baseline is a semantic revision"
    assert observer.presentation.observed_at == datetime(2026, 9, 9, 10, 9)
    assert observer.presentation.is_current


def test_re_entry_starts_a_clean_observer():
    """Disable and re-enable: no evidence survives from the abandoned instance."""
    first = Observer()
    first.publish(Observation(first.begin(), datetime.now(), True,
                              state=project(OSGB)))
    assert first.presentation.has_evidence

    second = Observer()          # what initGui() would build after unload()
    assert not second.presentation.has_evidence
    assert second.presentation.note == "Not checked."


def test_work_from_an_abandoned_instance_cannot_publish_into_a_live_one():
    """An in-flight batch from a torn-down instance must not surface later."""
    abandoned = Observer()
    pending = abandoned.begin()
    live = Observer(session="session-2")
    assert live.publish(Observation(pending, datetime.now(), True,
                                    state=project(OSGB))) is False


def test_switching_projects_does_not_write_a_mass_removal():
    """cleared() fires on switch as well as on clear.

    Ending the observation session is correct; diffing the new project against
    the old one's snapshot would record every layer as deleted.
    """
    observer = Observer()
    history = History()
    history.record(project(OSGB, layer("l1", "uk.gpkg", OSGB, OSGB, None),
                           layer("l2", "gps.gpkg", WGS, OSGB)))
    observer.start_session("other-project")

    # A new session means a new store, not a diff across the boundary.
    fresh = History()
    cs = fresh.record(project(WGS, layer("z1", "other.gpkg", WGS, WGS, None)))
    assert cs.is_baseline
    assert not any(c.after is None for c in cs.changes), \
        "nothing may be recorded as removed by opening a different project"
    assert {c.object_id for c in cs.changes} == {PROJECT_ID, "z1"}
