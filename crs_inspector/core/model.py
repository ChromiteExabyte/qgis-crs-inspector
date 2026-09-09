"""Value types for the datum record.

Deliberately free of Qt and PyQGIS imports: everything here is plain Python, so
`grading` can be tested headless and the same objects can back a Qt5 (QGIS 3.44
LTR) or Qt6 (QGIS 4.x) surface without change.

`probe` is the only module allowed to import from `qgis.core`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


#: Bump when the grading rules change, so a verdict that moved because the rules
#: moved is distinguishable from one that moved because the project did.
GRADING_RULE_VERSION = 3


class Shift(Enum):
    """What happened to a layer's coordinates on the way to the project CRS."""

    NO_CRS = "no_crs"        # nothing was applied and nothing can be verified
    NO_SHIFT = "no_shift"    # same datum; only a projection change, if that
    SHIFT = "shift"          # a datum shift with a published uncertainty
    BALLPARK = "ballpark"    # named ballpark operation across differing datums
    CROSS_DATUM_UNKNOWN = "cross_datum_unknown"
    """Datums differ and no accuracy was published, but the operation does not
    identify itself as a ballpark.

    PROJ documents a negative accuracy as "unknown or error" — which is not the
    same claim as "the datum shift was omitted". Asserting the stronger reading
    from the weaker evidence is exactly the crying-wolf this plugin must not do,
    so it gets its own state and its own wording.
    """

    TEMPORAL_UNASSESSED = "temporal_unassessed"
    """Datums match, but a dynamic CRS is involved and the epochs are not both
    known and equal.

    A matching datum name does not prove the operation is projection-only: QGIS
    treats the coordinate epoch as material to time-dependent transformations,
    and this classifier does not assess them. Reporting the narrower supported
    scope is the honest answer — neither an invented cross-datum diagnosis nor
    an unconditional projection-only assurance.
    """

    UNKNOWN = "unknown"      # the probe could not complete


#: States that should draw attention. Everything else is quiet by construction.
ALARMING = frozenset({Shift.BALLPARK, Shift.CROSS_DATUM_UNKNOWN, Shift.NO_CRS})


def clean_float(value) -> Optional[float]:
    """PROJ reports "no value" as -1 and QGIS reports "no epoch" as NaN.

    Both become None here so that absence is one thing in the model rather than
    two sentinels that each need remembering. (M0 finding: `coordinateEpoch()`
    returns NaN, not None, and `accuracy` returns -1.0, not None.)
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or f < 0:
        return None
    return f


@dataclass(frozen=True)
class GridRef:
    """A grid shift file an operation depends on."""

    short_name: str
    package: str = ""
    url: str = ""
    is_available: bool = False


@dataclass(frozen=True)
class CrsRef:
    authid: str
    description: str
    datum_key: str
    """Stable identity for the datum.

    Two CRSs sharing a datum must produce the same key — EPSG:27700 and
    EPSG:4277 are both OSGB36. M0 finding: `datumEnsemble()` is empty for
    single-datum CRSs, so this falls back to the WKT DATUM node.
    """

    is_geographic: bool = False
    is_dynamic: bool = False
    epoch: Optional[float] = None
    is_valid: bool = True
    definition: str = ""
    """The CRS definition as captured (WKT). Identity, unlike authid."""

    definition_read: bool = True
    """False when the definition could not be read at all.

    Unreadable is not empty. Letting them share a value would turn a lost read
    into an observed change to a blank definition.
    """

    @property
    def label(self) -> str:
        if not self.is_valid:
            return "no CRS set"
        return "{}  {}".format(self.authid or "custom", self.description).strip()


@dataclass(frozen=True)
class OperationRef:
    """The coordinate operation actually in force."""

    name: str
    accuracy_m: Optional[float] = None
    """None means PROJ published no accuracy.

    On its own this does NOT mean ballpark — a same-datum projection change also
    reports no accuracy. See `grading.grade`.
    """

    is_available: bool = True
    grids: Tuple[GridRef, ...] = ()
    published_accuracy_raw: Optional[float] = None
    """The literal value PROJ reported, before -1 was folded into None.

    Retained so a finding can be re-examined without re-probing, and so the
    interpretation is separable from the observation.
    """

    @property
    def missing_grids(self) -> Tuple[GridRef, ...]:
        return tuple(g for g in self.grids if not g.is_available)


@dataclass(frozen=True)
class LayerState:
    """One layer's coordinate state at one moment.

    This is the unit that gets diffed. Two states comparing equal means nothing
    is written to the log — the diff is the primitive, not the event.
    """

    layer_id: str
    layer_name: str
    source: CrsRef
    target: CrsRef
    operation: Optional[OperationRef] = None
    probe_error: Optional[str] = None

    def datums_comparable(self) -> bool:
        """Both datum identities are known, so same/different is meaningful.

        Without this, an unknown datum on either side collapses into "different"
        — and a project whose CRS failed to resolve grades every layer as a
        cross-datum case with total confidence. That is the exact failure this
        plugin exists to catch, and it was living inside the plugin.
        """
        return bool(self.source.datum_key) and bool(self.target.datum_key)

    def same_datum(self) -> bool:
        return (self.datums_comparable()
                and self.source.datum_key == self.target.datum_key)


@dataclass(frozen=True)
class Evidence:
    """What the grading was actually derived from.

    Retained alongside the verdict so a finding can be re-examined without
    re-running the probe, and so a change in grading rules is distinguishable
    from a change in the world.
    """

    published_accuracy: Optional[float]   # literal value, before interpretation
    datums_differ: Optional[bool]
    """True, False, or None when the two datums are not comparable at all.

    None is not "no difference" — it is "no answer", and it must never be read
    as either of the other two.
    """

    operation_name: str = ""
    names_itself_ballpark: bool = False
    source_crs_valid: bool = True
    target_crs_valid: bool = True
    probe_failed: bool = False
    source_dynamic: bool = False
    target_dynamic: bool = False
    source_epoch: Optional[float] = None
    target_epoch: Optional[float] = None
    """Epochs travel with the evidence so a stored verdict can be re-read.

    Deliberately NOT folded into `datum_key`: a different coordinate epoch is
    not a different datum, and merging them would let a temporal change appear
    as a reference-frame change.
    """
    rule_version: int = GRADING_RULE_VERSION


@dataclass(frozen=True)
class Verdict:
    shift: Shift
    headline: str
    detail: str = ""
    uncertainty_m: Optional[float] = None
    action: str = ""
    evidence: Optional[Evidence] = None

    @property
    def is_alarming(self) -> bool:
        return self.shift in ALARMING


@dataclass(frozen=True)
class ProjectState:
    target: CrsRef
    layers: Tuple[LayerState, ...] = field(default_factory=tuple)
    context_entries: Optional[Tuple] = field(default_factory=tuple)
    """The project's authored transformation policy, for fingerprinting.

    Part of the PROJECT's own inputs — never folded into a layer's state key.
    """
