"""Turn a LayerState into a plain-language Verdict.

Pure functions over dataclasses — no Qt, no PyQGIS, no I/O.

The rules, and the limits of what they can claim
------------------------------------------------
`QgsCoordinateTransform.fallbackOperationOccurred()` looks like the ballpark
signal and is not: it reports that a *specified* operation failed and PROJ fell
back, so it stays False when PROJ simply picks a ballpark as its best available
option. Verified on QGIS 4.2.2 / PROJ 9.8.

The available signal is a published accuracy of -1, but it is overloaded —
EPSG:3857 → EPSG:4326 also reports -1 and is healthy, because no datum shift is
involved. So the first cut is on datum equality:

                     | same datum        | different datum
    -----------------+-------------------+---------------------------
    accuracy known   | SHIFT             | SHIFT
    accuracy absent  | NO_SHIFT (fine)   | see below

For the bottom-right cell, "accuracy absent across differing datums" is **not**
a universal guarantee of ballpark behaviour. PROJ documents a negative accuracy
as "unknown or error", and exposes a separate ballpark predicate in its C API
that QGIS does not surface here. So the evidence is split:

  * the operation names itself a ballpark  -> BALLPARK, stated as such
  * it does not                            -> CROSS_DATUM_UNKNOWN, stated as
                                              unknown accuracy across a datum
                                              boundary, not as an omitted shift

Both want attention. Only one asserts a diagnosis. Every verdict carries the
`Evidence` it was derived from plus a rule version, so a finding can be
re-examined, and a verdict that moved because the rules moved is distinguishable
from one that moved because the project did.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    Evidence, GRADING_RULE_VERSION, LayerState, Shift, Verdict,
)

#: PROJ names its fallback operations "Ballpark geographic offset from X to Y".
#: Name evidence, not a guarantee — hence a distinct, weaker state when absent.
_BALLPARK_MARKER = "ballpark"


def _names_itself_ballpark(operation) -> bool:
    return bool(operation) and _BALLPARK_MARKER in (operation.name or "").lower()


def _evidence(state: LayerState) -> Evidence:
    op = state.operation
    return Evidence(
        published_accuracy=(op.published_accuracy_raw if op else None),
        # None when the datums cannot be compared at all — never False, which
        # would read as "the same".
        datums_differ=(None if not state.datums_comparable()
                       else not state.same_datum()),
        operation_name=(op.name if op else ""),
        names_itself_ballpark=_names_itself_ballpark(op),
        source_crs_valid=state.source.is_valid,
        target_crs_valid=state.target.is_valid,
        probe_failed=bool(state.probe_error),
        source_dynamic=state.source.is_dynamic,
        target_dynamic=state.target.is_dynamic,
        source_epoch=state.source.epoch,
        target_epoch=state.target.epoch,
    )


def _temporally_unassessed(evidence: Evidence) -> bool:
    """Matching datums, but declared epochs that are actually in tension.

    Static CRSs make the epoch irrelevant, so they are never flagged.

    Where a dynamic frame is involved, the threshold is deliberately *declared
    tension* rather than mere dynamism. QGIS reports EPSG:4326 and EPSG:3857 as
    dynamic and neither normally carries an epoch — so flagging "any dynamic CRS
    without epochs" marks essentially every Web Mercator project on earth. That
    is the crying-wolf failure this plugin exists to avoid, and a warning nobody
    can act on is worse than silence.

    So: nothing declared, nothing in tension. The flag is raised when at least
    one side declares an epoch and the two do not agree — which is the case a
    user can actually do something about, and the case where a projection-only
    claim would be unsupported.
    """
    if not (evidence.source_dynamic or evidence.target_dynamic):
        return False
    source, target = evidence.source_epoch, evidence.target_epoch
    if source is None and target is None:
        return False
    return source != target


def classify(evidence: Evidence) -> Shift:
    """The classification kernel: Evidence in, a state out. Nothing else.

    Deliberately takes no LayerState and touches no QGIS, so an evidence record
    captured months ago can be re-read under today's rules with zero probe
    calls. A rule change is not a new measurement, and reclassifying must never
    advance an observation's timestamp or make stale evidence look fresh.
    """
    if evidence.probe_failed:
        return Shift.UNKNOWN
    if evidence.source_crs_valid is False:
        return Shift.NO_CRS
    if evidence.target_crs_valid is False:
        return Shift.UNKNOWN
    if evidence.datums_differ is None:
        # Not comparable. Reading this as "different" is how an unresolved
        # project CRS turns into a page of confident cross-datum verdicts.
        return Shift.UNKNOWN
    if evidence.datums_differ is False:
        if _temporally_unassessed(evidence):
            return Shift.TEMPORAL_UNASSESSED
        return Shift.NO_SHIFT
    if evidence.published_accuracy is None or evidence.published_accuracy < 0:
        return (Shift.BALLPARK if evidence.names_itself_ballpark
                else Shift.CROSS_DATUM_UNKNOWN)
    return Shift.SHIFT


def reclassify(evidence: Evidence) -> "Reclassification":
    """Re-read stored evidence under current rules, preserving what was said.

    The original interpretation is kept rather than rewritten: what the plugin
    reported at the time is itself a fact worth not losing.
    """
    return Reclassification(
        evidence=evidence,
        original_rule_version=evidence.rule_version,
        current_rule_version=GRADING_RULE_VERSION,
        current_shift=classify(evidence),
    )


@dataclass(frozen=True)
class Reclassification:
    evidence: Evidence
    original_rule_version: int
    current_rule_version: int
    current_shift: Shift

    @property
    def rules_moved(self) -> bool:
        return self.original_rule_version != self.current_rule_version


def grade(state: LayerState) -> Verdict:
    """Classify one layer's coordinate state against the project CRS."""
    if state.probe_error:
        return Verdict(
            Shift.UNKNOWN,
            "State could not be determined.",
            "The probe did not complete: {}. This is recorded rather than "
            "passed over — silence is the failure this plugin exists to "
            "fix.".format(state.probe_error),
            action="Retry, and report it if it persists.",
            evidence=_evidence(state),
        )

    if not state.source.is_valid:
        return Verdict(
            Shift.NO_CRS,
            "No CRS is assigned to this layer.",
            "QGIS is treating its coordinates as if they were already in the "
            "project CRS. No transformation runs, so nothing here can verify "
            "that assumption. This is the one failure the record cannot see.",
            action="Set the layer's CRS in Layer Properties → Source.",
            evidence=_evidence(state),
        )

    if not state.target.is_valid:
        return Verdict(
            Shift.UNKNOWN,
            "The project CRS did not resolve.",
            "Nothing can be assessed against a destination that is not itself "
            "known. Every layer would otherwise be reported as crossing a datum "
            "boundary, with total confidence and no basis.",
            action="Set a valid project CRS in Project Properties.",
            evidence=_evidence(state),
        )

    if not state.datums_comparable():
        unknown = ("this layer's" if not state.source.datum_key
                   else "the project's")
        return Verdict(
            Shift.UNKNOWN,
            "Datum identity is unavailable.",
            "{} datum could not be identified, so whether a shift was needed "
            "cannot be decided either way. Reported as unchecked rather than "
            "guessed.".format(unknown.capitalize()),
            action="Check the CRS definition; a custom CRS may lack a datum node.",
            evidence=_evidence(state),
        )

    op = state.operation
    accuracy = op.accuracy_m if op else None
    same = state.same_datum()
    evidence = _evidence(state)

    if same and _temporally_unassessed(evidence):
        return Verdict(
            Shift.TEMPORAL_UNASSESSED,
            "Same datum; temporal reference not assessed.",
            "Both sides report {}, but a dynamic reference frame is involved "
            "and the coordinate epochs are not both known and equal ({} vs {}). "
            "Whether a time-dependent shift applies is outside what this check "
            "assesses, so it is reported rather than assumed either way.".format(
                state.source.datum_key or "the same datum",
                "unset" if state.source.epoch is None else state.source.epoch,
                "unset" if state.target.epoch is None else state.target.epoch),
            uncertainty_m=None,
            action="Set a coordinate epoch on both, or verify the epoch "
                   "handling separately.",
            evidence=evidence,
        )

    if same:
        return Verdict(
            Shift.NO_SHIFT,
            "No datum shift.",
            "This layer is already in the project's reference frame ({}). Any "
            "operation applied is a projection change only.".format(
                state.source.datum_key or "same datum"),
            uncertainty_m=None,
            evidence=evidence,
        )

    if accuracy is None:
        if evidence.names_itself_ballpark:
            return Verdict(
                Shift.BALLPARK,
                "No datum shift available.",
                "The operation in force identifies itself as a ballpark offset "
                "from {} to {}: coordinates were reprojected without shifting "
                "the datum. The layer still lines up on screen with everything "
                "else. The error is not 5 m or 50 m — it is unmeasured.".format(
                    state.source.datum_key or state.source.authid,
                    state.target.datum_key or state.target.authid),
                uncertainty_m=None,
                action=_cross_datum_action(state),
                evidence=evidence,
            )
        return Verdict(
            Shift.CROSS_DATUM_UNKNOWN,
            "Accuracy not published across a datum boundary.",
            "This layer crosses from {} to {}, and the operation in force ({}) "
            "publishes no accuracy. That is reported as unknown rather than as "
            "an omitted datum shift — the evidence does not distinguish the "
            "two.".format(
                state.source.datum_key or state.source.authid,
                state.target.datum_key or state.target.authid,
                op.name if op else "unnamed"),
            uncertainty_m=None,
            action=_cross_datum_action(state),
            evidence=evidence,
        )

    missing = op.missing_grids if op else ()
    if missing:
        names = ", ".join(g.short_name for g in missing)
        return Verdict(
            Shift.SHIFT,
            "Shifted, but a better operation is unavailable.",
            "In force: {} at {}. A more accurate operation exists but needs a "
            "grid file that is not installed ({}).".format(
                op.name, format_uncertainty(accuracy), names),
            uncertainty_m=accuracy,
            action="Install {}.".format(missing[0].package or names),
            evidence=evidence,
        )

    return Verdict(
        Shift.SHIFT,
        "Shifted by {}.".format(format_uncertainty(accuracy)),
        "{} → {} using {}.".format(
            state.source.datum_key or state.source.authid,
            state.target.datum_key or state.target.authid,
            op.name if op else "an unnamed operation"),
        uncertainty_m=accuracy,
        evidence=evidence,
    )


def _cross_datum_action(state: LayerState) -> str:
    """One action, not a menu. Removing the 'now what?' is the point."""
    return (
        "Choose an operation in Project Properties → Transformations, or pick a "
        "project CRS with a published path from {}.".format(
            state.source.datum_key or state.source.authid))


def format_uncertainty(metres) -> str:
    """Published uncertainty, worded so it cannot be read as measured error."""
    if metres is None:
        return "unbounded"
    if metres == 0:
        return "0 m"
    if metres < 0.01:
        return "under 1 cm"
    if metres < 1:
        return "{:.0f} cm".format(metres * 100)
    if metres < 10:
        return "{:.1f} m".format(metres).replace(".0 m", " m")
    return "{:.0f} m".format(metres)


def summarise(verdicts) -> str:
    """A count for a header. Unknown must never collapse into 'no flags'."""
    vs = list(verdicts)
    if not vs:
        return "No layers."
    counts = {
        Shift.BALLPARK: 0,
        Shift.CROSS_DATUM_UNKNOWN: 0,
        Shift.NO_CRS: 0,
        Shift.UNKNOWN: 0,
        Shift.TEMPORAL_UNASSESSED: 0,
    }
    for v in vs:
        if v.shift in counts:
            counts[v.shift] += 1

    flagged = counts[Shift.BALLPARK] + counts[Shift.CROSS_DATUM_UNKNOWN] + counts[Shift.NO_CRS]
    unchecked = counts[Shift.UNKNOWN] + counts[Shift.TEMPORAL_UNASSESSED]
    if not flagged and not unchecked:
        return "{} layers, all assessed, no flags.".format(len(vs))

    bits = []
    if counts[Shift.BALLPARK]:
        bits.append("{} with no datum shift available".format(counts[Shift.BALLPARK]))
    if counts[Shift.CROSS_DATUM_UNKNOWN]:
        bits.append("{} with unpublished accuracy across a datum boundary".format(
            counts[Shift.CROSS_DATUM_UNKNOWN]))
    if counts[Shift.NO_CRS]:
        bits.append("{} with no CRS set".format(counts[Shift.NO_CRS]))
    if counts[Shift.TEMPORAL_UNASSESSED]:
        bits.append("{} with an unassessed temporal reference".format(
            counts[Shift.TEMPORAL_UNASSESSED]))
    if counts[Shift.UNKNOWN]:
        bits.append("{} unchecked".format(counts[Shift.UNKNOWN]))
    return "{} layers — {}.".format(len(vs), ", ".join(bits))


def indicator(verdicts) -> str:
    """The compact, always-visible readout.

    An existential claim only: something needs attention, or nothing observed
    does. Deliberately never says "accurate", "safe", "verified", or shows a
    score — none of which the evidence could support. "Unchecked" is kept
    separate from "no flags" so hiding a failure cannot look like fixing it.
    """
    vs = list(verdicts)
    flagged = sum(1 for v in vs if v.is_alarming)
    unchecked = sum(1 for v in vs
                    if v.shift in (Shift.UNKNOWN, Shift.TEMPORAL_UNASSESSED))
    if not vs:
        return "not checked"
    bits = []
    if flagged:
        bits.append("{} flagged".format(flagged))
    if unchecked:
        bits.append("{} unchecked".format(unchecked))
    # "no flagged transforms" — a statement about what was looked for and not
    # found. Never "correct", "safe", "verified", or a score: the evidence is
    # operation metadata, which cannot support a claim about absolute position.
    # No prefix: whatever surface shows this already names itself.
    return " · ".join(bits) if bits else "no flagged transforms"
