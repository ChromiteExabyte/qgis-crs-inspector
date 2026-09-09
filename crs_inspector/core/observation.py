"""When an observation applies — and when it has stopped applying.

The classifier being honest is not enough. The next dangerous false assurance is
not a misread ballpark; it is showing yesterday's correct assessment against
today's different inputs:

    a layer is assessed        -> no flagged transforms
    its assigned CRS changes
    reassessment fails
    the panel keeps showing    -> no flagged transforms

Every grading test passes and the plugin still misleads. So freshness is tracked
separately from semantic state, and the moment a relevant input change is known,
the previous evidence stops being presented as a *current* assessment. It stays
readable — labelled as the last successful observation.

Two rules make this work:

* `observed_at` never enters a `state_key`. A successful no-op recheck must add
  zero revisions and update freshness only. Putting the timestamp in the state
  would turn every refresh into a change.
* A failed capture is not "nothing changed". It changes what the plugin can
  currently claim to know, which is a different thing entirely.

Ordering is not a transaction boundary
--------------------------------------
Qt leaves the ordering of zero-duration timers against other event sources
unspecified, so a debounce firing is not evidence that a user action finished.
Hence a **generation counter** rather than an input digest: inputs can change
away and back while work is pending, and a digest would call that "unchanged".
A session token covers the project-switch case, so results captured against one
project can never be published into another.

Signals invalidate. Snapshots supply evidence. Diffs create records.

Pure. No Qt, no PyQGIS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .model import ProjectState


@dataclass(frozen=True)
class Capture:
    """The tokens an assessment batch was started under."""

    generation: int
    session: str


@dataclass(frozen=True)
class Observation:
    """One assessment batch's outcome.

    Carries the whole batch's inputs together: the project's configuration is
    captured once per batch, never re-read per layer, so what is presented as
    one coherent result really was assessed against one configuration.
    """

    capture: Capture
    at: datetime
    ok: bool
    state: Optional[ProjectState] = None
    error: str = ""


@dataclass(frozen=True)
class Presentation:
    """What may be shown right now, and what claim it supports."""

    state: Optional[ProjectState]
    observed_at: Optional[datetime]
    is_current: bool
    note: str = ""

    @property
    def has_evidence(self) -> bool:
        return self.state is not None


class Observer:
    """Tracks freshness across assessment batches. Holds no Qt, starts nothing."""

    def __init__(self, session: str = "session-1"):
        self._generation = 0
        self._session = session
        self._retired = False
        self._last_ok: Optional[Observation] = None
        self._last_attempt: Optional[Observation] = None

    # -- lifecycle ---------------------------------------------------------
    def begin(self) -> Capture:
        """Take the tokens a batch is about to be assessed under."""
        return Capture(self._generation, self._session)

    def invalidate(self) -> int:
        """A relevant input changed. Anything in flight is now superseded."""
        self._generation += 1
        return self._generation

    def retire(self) -> None:
        """Close the current session without opening another.

        Verified ordering of QgsProject.clear() is:
            aboutToBeCleared -> layersWillBeRemoved -> layersRemoved -> cleared
        so layer-removal notifications arrive *during* teardown. Retiring on
        aboutToBeCleared means those arrive against an already-dead session and
        cannot be read as project edits. Waiting for cleared() would leave a
        window in which teardown looks like the user deleting everything.
        """
        self._session = "retired:{}".format(self._session)
        self._generation += 1
        self._retired = True
        self._last_ok = None
        self._last_attempt = None

    def start_session(self, session: str) -> None:
        """A different project. Nothing observed before may be shown for it.

        Called on project switch rather than treating the switch as the user
        deleting every layer — QGIS fires `cleared()` in both cases, and reading
        it as a mass removal would fabricate history that never happened.
        """
        self._session = session
        self._generation += 1
        self._retired = False
        self._last_ok = None
        self._last_attempt = None

    # -- publication -------------------------------------------------------
    def is_current(self, capture: Capture) -> bool:
        # A retired observer matches nothing. Bumping tokens alone was not
        # enough: a capture taken *after* retirement carried the retired
        # session and generation, so it compared equal and published.
        if self._retired:
            return False
        return (capture.generation == self._generation
                and capture.session == self._session)

    def publish(self, observation: Observation) -> bool:
        """Accept a batch's result, or reject it as superseded.

        Returns False when the capture is stale — the caller should discard the
        pending result and reassess rather than showing it.
        """
        if not self.is_current(observation.capture):
            return False
        self._last_attempt = observation
        if observation.ok:
            self._last_ok = observation
        return True

    # -- what may be shown -------------------------------------------------
    @property
    def presentation(self) -> Presentation:
        last_ok = self._last_ok
        if last_ok is None:
            attempt = self._last_attempt
            note = ("Not checked." if attempt is None
                    else "Check failed: {}".format(attempt.error or "unknown"))
            return Presentation(None, None, False, note)

        fresh = self.is_current(last_ok.capture)
        attempt = self._last_attempt
        failed = attempt is not None and not attempt.ok

        # Latest-attempt status and known input change are separate facts, and
        # neither may hide the other. A failed re-check with no input change was
        # previously swallowed entirely, because the last success still matched
        # the current generation.
        if fresh and not failed:
            return Presentation(last_ok.state, last_ok.at, True, "")
        if fresh and failed:
            return Presentation(
                last_ok.state, last_ok.at, False,
                "Re-checking failed ({}). Showing the last successful check; no "
                "input change was observed.".format(attempt.error or "unknown"))
        if failed:
            return Presentation(
                last_ok.state, last_ok.at, False,
                "Inputs changed and re-checking failed ({}). Showing the last "
                "successful check.".format(attempt.error or "unknown"))
        return Presentation(last_ok.state, last_ok.at, False,
                            "Inputs have changed since this check. Not yet re-checked.")


def freshness_line(presentation: Presentation) -> str:
    """The freshness half of the compact indicator.

    Says what is known and when. Never implies a stale result is current, and
    never says "verified" — the evidence is operation metadata, which cannot
    support a claim about absolute position.
    """
    if not presentation.has_evidence:
        return presentation.note or "not checked"
    stamp = presentation.observed_at.strftime("%H:%M") if presentation.observed_at else "?"
    if presentation.is_current:
        return "checked {}".format(stamp)
    return "last checked {} · inputs changed since".format(stamp)
