"""Change sets — the record itself.

Four rules, and they are the whole design:

1.  **One kind of record.** The project is a tracked object with state, exactly
    like a layer. Changing the project CRS is not an event with N effects; it is
    one more state change that the others point at. There is no event primitive
    here, and adding one would grow a second vocabulary that slowly acquires its
    own semantics.

2.  **The diff is the primitive.** A row is written when state *changes*, never
    when something merely happens. A layer already in the target frame does not
    move when the project CRS does, so nothing is written for it.

3.  **Local input change is derived, not recorded — and it is not a cause.**
    `self_key` (this object's own captured inputs) and `state_key` (everything
    observable about it) are tracked separately, so "its own inputs moved too"
    falls out rather than being asserted by anyone.

    What that set means is deliberately narrow. An empty one means *no change in
    the captured own inputs explains this observation* — not "nothing caused
    this", and not "the environment must have". With two simultaneous input
    changes, an external resource change, or a gap between observations,
    co-membership in a change set does not establish causation. The field is
    named for what it measures.

4.  **A change set holds the state differences committed together from one
    assessment batch.** Not "one user action" — that was an overclaim. QGIS's
    signals are invalidation notices, not transaction boundaries, and Qt leaves
    the ordering of a zero-duration timer against other event sources
    unspecified, so a debounce firing is not evidence that a user action
    finished. Sometimes a batch matches one action; sometimes it captures
    several together; sometimes one action is observed in pieces.

    Storage is per-object diffs; display is by change set. `all()` and
    `for_object()` are `git log` and `git log -- <path>` over one store.

The boundary that follows: **signals invalidate, snapshots supply evidence,
diffs create records.** Events can drive the implementation without becoming a
domain primitive.

Consequence worth knowing: a batch cannot be written until its recompute has
settled, so records are emitted at the end of a batch, never inline. And this
history is a history of *observed* changes — inputs that went A -> B -> A
between two snapshots leave no record, which is an unobserved intermediate
state rather than a defective diff.

Pure functions over dataclasses. No Qt, no PyQGIS.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from .fingerprint import context_fingerprint, crs_identity
from .grading import format_uncertainty, grade
from .model import ProjectState, Shift

PROJECT_ID = "__project__"

_SHORT = {
    Shift.NO_CRS: "no CRS",
    Shift.NO_SHIFT: "no shift",
    Shift.SHIFT: None,           # replaced by the uncertainty
    Shift.BALLPARK: "no shift available",
    Shift.CROSS_DATUM_UNKNOWN: "accuracy not published",
    Shift.UNKNOWN: "state unknown",
}


@dataclass(frozen=True)
class ObjectSnapshot:
    object_id: str
    label: str
    kind: str                    # "project" or "layer"
    self_key: Tuple              # this object's OWN inputs
    state_key: Tuple             # everything observable about it
    summary: str                 # one human-readable line
    policy: str = ""             # the authored-policy digest, held separately
    unreadable: Tuple[str, ...] = ()
    """Inputs that could not be established on this pass.

    "I could not establish whether this input changed" is not "I observed this
    input change". Substituting a sentinel into the key would assert the second
    while only having grounds for the first, so `diff()` carries the previous
    value forward for anything named here.
    """


@dataclass(frozen=True)
class Change:
    object_id: str
    label: str
    kind: str
    before: Optional[str]        # None when the object did not exist
    after: Optional[str]         # None when the object was removed
    locally_changed: bool
    """This object's own captured inputs moved as well as its observable state.

    Evidence about where to look, not a claim about what caused what.
    """

    first_observation: bool = False
    """This is the batch that first saw the object, not the batch it appeared in.

    Enabling the plugin on an existing project discovers layers that have been
    there all along. Calling that "added" would put an event in the record that
    never happened.
    """

    @property
    def line(self) -> str:
        if self.before is None:
            if self.first_observation:
                return "first observed — {}".format(self.after)
            return "added — {}".format(self.after)
        if self.after is None:
            return "removed (was {})".format(self.before)
        return "{} → {}".format(self.before, self.after)


@dataclass(frozen=True)
class ChangeSet:
    ident: str
    at: datetime
    changes: Tuple[Change, ...]
    n_tracked_layers: int
    is_baseline: bool = False
    """The first batch of an observation session.

    Its rows record what was found, not what happened.
    """

    @property
    def locally_changed_inputs(self) -> Tuple[Change, ...]:
        """Objects whose own captured inputs did change in this batch.

        Empty means exactly: *no tracked own-input changes were observed in
        this batch.* Not "nothing explains this" — that still gestures at causal
        analysis this deliberately does not perform.
        """
        return tuple(c for c in self.changes if c.locally_changed)

    @property
    def without_local_change(self) -> Tuple[Change, ...]:
        """Objects whose observable state moved with no own-input change observed."""
        return tuple(c for c in self.changes if not c.locally_changed)

    @property
    def headline(self) -> str:
        """Named by an object's own diff, never by an event label."""
        local = self.locally_changed_inputs
        lead = local[0] if local else self.changes[0]
        return "{}  {}".format(lead.label, lead.line)

    @property
    def note(self) -> str:
        """Computed, not asserted — and worded without implying causation."""
        if self.is_baseline:
            return "initial assessment of {} layers".format(self.n_tracked_layers)
        n = len(self.without_local_change)
        if not n:
            return "no other object changed"
        return "{} of {} layers also changed".format(n, self.n_tracked_layers)


# --------------------------------------------------------------------------
def _layer_summary(state) -> str:
    verdict = grade(state)
    short = _SHORT[verdict.shift]
    if short is not None:
        return short
    return format_uncertainty(verdict.uncertainty_m)


def snapshot(project_state: ProjectState) -> Dict[str, ObjectSnapshot]:
    """Reduce a probe result to the keys the diff is judged on."""
    target = project_state.target
    out: Dict[str, ObjectSnapshot] = {}

    # The authored transformation policy is part of the PROJECT's own inputs:
    # editing an operation is an authored change and must show as one. It is
    # deliberately NOT folded into any layer's state_key below — doing so would
    # make an edit to an unused CRS pair change every layer's hash, re-creating
    # the unconditional fan-out that rule 2 exists to prevent.
    # Identity, not labels: the captured definition plus the coordinate epoch.
    # Without the epoch here, an epoch-only edit leaves every key unchanged and
    # the change is never recorded at all — a strictly worse failure than losing
    # its explanation, because there is nothing left to explain.
    policy = context_fingerprint(project_state.context_entries)
    unreadable = ("policy",) if project_state.context_entries is None else ()
    project_key = (crs_identity(target.definition, target.epoch, target.authid,
                                target.definition_read),
                   target.datum_key, policy)
    out[PROJECT_ID] = ObjectSnapshot(
        object_id=PROJECT_ID,
        label="Project CRS",
        kind="project",
        self_key=project_key,
        state_key=project_key,
        summary="{} · {}".format(target.authid or "custom",
                                 target.datum_key or target.description),
        policy=policy,
        unreadable=unreadable,
    )

    for layer in project_state.layers:
        op = layer.operation
        out[layer.layer_id] = ObjectSnapshot(
            object_id=layer.layer_id,
            label=layer.layer_name,
            kind="layer",
            # Own inputs only: the layer's assigned CRS. Deliberately excludes
            # anything derived from the project, so that a project-driven change
            # never makes a layer look like an originator.
            self_key=(crs_identity(layer.source.definition, layer.source.epoch,
                                   layer.source.authid,
                                   layer.source.definition_read),
                      layer.source.datum_key, layer.source.is_valid),
            # The assessment TARGET is deliberately absent from this key. It
            # belongs to the observation, which records what each layer was
            # assessed against. Putting it here made a project-only edit change
            # every layer's hash — including a layer with no CRS, which nothing
            # about the project can affect — recreating the unconditional
            # fan-out the diff rule exists to prevent. A project epoch change is
            # recorded on the project; it reaches a layer only if that layer's
            # own assessment actually moved.
            state_key=(crs_identity(layer.source.definition, layer.source.epoch,
                                    layer.source.authid,
                                    layer.source.definition_read),
                       layer.source.datum_key,
                       op.name if op else None,
                       op.accuracy_m if op else None,
                       tuple(sorted((g.short_name, g.is_available)
                                    for g in (op.grids if op else ()))),
                       grade(layer).shift),
            summary=_layer_summary(layer),
        )
    return out


def _carry_forward(was: Optional[ObjectSnapshot],
                   now: ObjectSnapshot) -> ObjectSnapshot:
    """Reuse the last established value for anything unreadable this pass.

    Without this, a failed policy read would digest to "unobserved", differ from
    the previous digest, and be recorded as though the user had changed the
    authored policy — inventing an edit out of a lost read. Observe A, fail,
    observe A again must produce no policy change at all.
    """
    if not now.unreadable or was is None:
        return now
    if "policy" in now.unreadable:
        restored = (now.state_key[0], now.state_key[1], was.policy)
        return replace(now, self_key=restored, state_key=restored,
                       policy=was.policy)
    return now


def diff(previous: Optional[Dict[str, ObjectSnapshot]],
         current: Dict[str, ObjectSnapshot],
         ident: str,
         at: Optional[datetime] = None) -> Optional[ChangeSet]:
    """Compare two snapshots. Returns None when nothing changed at all."""
    at = at or datetime.now()
    baseline = not previous
    previous = previous or {}
    n_layers = sum(1 for s in current.values() if s.kind == "layer")

    changes: List[Change] = []
    for object_id in sorted(set(previous) | set(current),
                            key=lambda i: (i != PROJECT_ID, i)):
        was, now = previous.get(object_id), current.get(object_id)
        if now is not None:
            now = _carry_forward(was, now)

        if was is not None and now is not None and was.state_key == now.state_key:
            continue                       # rule 2: unchanged writes nothing

        ref = now or was
        changes.append(Change(
            object_id=object_id,
            label=ref.label,
            kind=ref.kind,
            before=was.summary if was else None,
            after=now.summary if now else None,
            # rule 3: this object's OWN captured inputs moved as well.
            # Appearing or disappearing is itself a change of own inputs.
            locally_changed=(was is None or now is None
                             or was.self_key != now.self_key),
            first_observation=baseline,
        ))

    if not changes:
        return None
    return ChangeSet(ident=ident, at=at, changes=tuple(changes),
                     n_tracked_layers=n_layers, is_baseline=baseline)


class History:
    """The store. Two traversals over one set of edges."""

    def __init__(self):
        self._sets: List[ChangeSet] = []
        self._last: Optional[Dict[str, ObjectSnapshot]] = None

    def record(self, project_state: ProjectState,
               at: Optional[datetime] = None) -> Optional[ChangeSet]:
        """Fold a probe result in. Returns the change set, or None if quiet."""
        current = snapshot(project_state)
        # Resolve unreadable inputs against the last established values BEFORE
        # storing, not just before diffing. Storing the raw snapshot let a gap
        # poison the next comparison: the placeholder became the new baseline,
        # so the next successful read of an unchanged policy looked like an edit.
        current = {object_id: _carry_forward(self._last.get(object_id)
                                             if self._last else None, snap)
                   for object_id, snap in current.items()}
        change_set = diff(self._last, current,
                          ident="CS-{}".format(len(self._sets) + 1), at=at)
        self._last = current
        if change_set is not None:
            self._sets.append(change_set)
        return change_set

    def all(self) -> Tuple[ChangeSet, ...]:
        """git log"""
        return tuple(reversed(self._sets))

    def for_object(self, object_id: str) -> Tuple[Tuple[ChangeSet, Change], ...]:
        """git log -- <object>"""
        found = []
        for change_set in reversed(self._sets):
            for change in change_set.changes:
                if change.object_id == object_id:
                    found.append((change_set, change))
                    break
        return tuple(found)

    def __len__(self) -> int:
        return len(self._sets)
