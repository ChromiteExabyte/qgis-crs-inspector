"""Tests for the grading rules.

These import `crs_inspector.core.*` and nothing else — no QGIS, no Qt, no running
application. That is the whole point of keeping `core/` free of both.

The cases that matter most are the two halves of the M0 finding: an absent
accuracy is healthy when the datums match and alarming when they do not. Getting
that backwards would either cry wolf on every Web Mercator project or stay silent
on every genuine ballpark, so both directions are pinned here.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crs_inspector.core.grading import format_uncertainty, grade, summarise
from crs_inspector.core.model import (
    CrsRef, GridRef, LayerState, OperationRef, Shift, clean_float,
)

OSGB = CrsRef("EPSG:27700", "OSGB36 / British National Grid",
              "Ordnance Survey of Great Britain 1936")
WGS_GEO = CrsRef("EPSG:4326", "WGS 84", "World Geodetic System 1984 ensemble",
                 is_geographic=True, is_dynamic=True)
WGS_MERC = CrsRef("EPSG:3857", "WGS 84 / Pseudo-Mercator",
                  "World Geodetic System 1984 ensemble", is_dynamic=True)
NAD27 = CrsRef("EPSG:4267", "NAD27", "North American Datum 1927", is_geographic=True)
NAD83 = CrsRef("EPSG:6318", "NAD83(2011)",
               "NAD83 (National Spatial Reference System 2011)", is_geographic=True)
NO_CRS = CrsRef("", "no CRS set", "", is_valid=False)


def state(source, target, operation=None, error=None):
    return LayerState("layer_abc123", "test.gpkg", source, target,
                      operation=operation, probe_error=error)


# --------------------------------------------------------------------------
# The M0 finding, both directions.
# --------------------------------------------------------------------------
def test_absent_accuracy_with_matching_datum_is_healthy():
    """EPSG:3857 -> EPSG:4326 reports accuracy -1 and is completely fine.

    Same datum ensemble, so the -1 means "no datum shift involved", not
    "ballpark". Treating -1 alone as the ballpark signal would flag every Web
    Mercator project in existence.
    """
    verdict = grade(state(WGS_MERC, WGS_GEO,
                          OperationRef("Inverse of Popular Visualisation "
                                       "Pseudo-Mercator", accuracy_m=None)))
    assert verdict.shift is Shift.NO_SHIFT
    assert not verdict.is_alarming
    assert verdict.uncertainty_m is None


def test_absent_accuracy_with_differing_datum_is_ballpark():
    """EPSG:4267 -> EPSG:6318 with no NADCON grids: a real ballpark.

    fallbackOperationOccurred() returns False here, which is exactly why it is
    not the signal we use.
    """
    verdict = grade(state(NAD27, NAD83,
                          OperationRef("Ballpark geographic offset from NAD27 "
                                       "to NAD83(2011)", accuracy_m=None)))
    assert verdict.shift is Shift.BALLPARK
    assert verdict.is_alarming
    assert verdict.uncertainty_m is None
    assert "unmeasured" in verdict.detail
    assert verdict.action, "an alarming verdict must carry exactly one action"


def test_datum_decides_not_the_number():
    """Same operation accuracy, opposite verdicts — the datum is what differs."""
    same = grade(state(WGS_MERC, WGS_GEO, OperationRef("x", accuracy_m=None)))
    diff = grade(state(NAD27, NAD83, OperationRef("x", accuracy_m=None)))
    assert same.shift is not diff.shift


# --------------------------------------------------------------------------
# Ordinary states
# --------------------------------------------------------------------------
def test_published_accuracy_is_a_plain_shift():
    verdict = grade(state(OSGB, WGS_GEO,
                          OperationRef("OSGB36 to WGS 84 (6)", accuracy_m=2.0)))
    assert verdict.shift is Shift.SHIFT
    assert verdict.uncertainty_m == 2.0
    assert not verdict.is_alarming


def test_missing_grid_names_the_file_to_install():
    op = OperationRef(
        "OSGB36 to WGS 84 (6)", accuracy_m=2.0,
        grids=(GridRef("uk_os_OSTN15_NTv2_OSGBtoETRS.tif", package="uk_os",
                       is_available=False),))
    verdict = grade(state(OSGB, WGS_GEO, op))
    assert verdict.shift is Shift.SHIFT
    assert "uk_os" in verdict.action


def test_no_crs_is_reported_as_its_own_state():
    verdict = grade(state(NO_CRS, OSGB))
    assert verdict.shift is Shift.NO_CRS
    assert verdict.is_alarming
    assert "cannot see" in verdict.detail


def test_probe_error_is_recorded_not_swallowed():
    verdict = grade(state(OSGB, WGS_GEO, error="PROJ exploded"))
    assert verdict.shift is Shift.UNKNOWN
    assert "PROJ exploded" in verdict.detail


# --------------------------------------------------------------------------
# Sentinels and formatting
# --------------------------------------------------------------------------
def test_clean_float_absorbs_both_sentinels():
    assert clean_float(-1.0) is None      # PROJ: no accuracy published
    assert clean_float(float("nan")) is None   # QGIS: no coordinate epoch
    assert clean_float(2.0) == 2.0
    assert clean_float(0.0) == 0.0
    assert clean_float(None) is None


def test_uncertainty_wording():
    assert format_uncertainty(None) == "unbounded"
    assert format_uncertainty(0.001) == "under 1 cm"
    assert format_uncertainty(0.1) == "10 cm"
    assert format_uncertainty(2.0) == "2 m"
    assert format_uncertainty(104.84) == "105 m"


def test_named_ballpark_and_merely_unknown_are_different_states():
    """The evidence for each is different, so the claim made must differ too.

    PROJ documents a negative accuracy as "unknown or error", which is weaker
    than "the datum shift was omitted". Only the operation naming itself a
    ballpark supports the stronger reading.
    """
    named = grade(state(NAD27, NAD83,
                        OperationRef("Ballpark geographic offset from NAD27 "
                                     "to NAD83(2011)", accuracy_m=None)))
    unnamed = grade(state(NAD27, NAD83,
                          OperationRef("Some unnamed operation", accuracy_m=None)))
    assert named.shift is Shift.BALLPARK
    assert unnamed.shift is Shift.CROSS_DATUM_UNKNOWN
    assert named.is_alarming and unnamed.is_alarming
    # The weaker case must not assert an omitted shift.
    assert "unmeasured" in named.detail
    assert "unmeasured" not in unnamed.detail
    assert "not published" in unnamed.headline.lower()


def test_verdicts_carry_the_evidence_and_a_rule_version():
    verdict = grade(state(NAD27, NAD83,
                          OperationRef("Ballpark geographic offset",
                                       accuracy_m=None,
                                       published_accuracy_raw=-1.0)))
    ev = verdict.evidence
    assert ev.published_accuracy == -1.0, "the literal value is preserved"
    assert ev.datums_differ is True
    assert ev.names_itself_ballpark is True
    assert ev.rule_version >= 2


def test_summary_counts_only_what_is_wrong():
    verdicts = [
        grade(state(WGS_MERC, WGS_GEO, OperationRef("x", accuracy_m=None))),
        grade(state(OSGB, WGS_GEO, OperationRef("y", accuracy_m=2.0))),
        grade(state(NAD27, NAD83,
                    OperationRef("Ballpark geographic offset", accuracy_m=None))),
        grade(state(NO_CRS, OSGB)),
    ]
    text = summarise(verdicts)
    assert "4 layers" in text
    assert "1 with no datum shift available" in text
    assert "1 with no CRS set" in text


def test_unchecked_never_collapses_into_no_flags():
    """Hiding a failure must not be able to look like fixing one."""
    from crs_inspector.core.grading import indicator
    clean = [grade(state(OSGB, WGS_GEO, OperationRef("y", accuracy_m=2.0)))]
    assert indicator(clean) == "no flagged transforms"

    with_unknown = clean + [grade(state(OSGB, WGS_GEO, error="probe failed"))]
    text = indicator(with_unknown)
    assert "unchecked" in text
    assert "no flagged transforms" not in text
    # And never a claim of correctness.
    for word in ("verified", "safe", "accurate", "✓"):
        assert word not in text.lower()


def test_summary_is_calm_when_nothing_is_wrong():
    verdicts = [grade(state(OSGB, WGS_GEO, OperationRef("y", accuracy_m=2.0)))]
    assert "all assessed, no flags" in summarise(verdicts)
