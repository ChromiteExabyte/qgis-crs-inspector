"""A bounded, opt-in diagnostic trace of the observer's own behaviour.

This is **not** CRS Inspector's history. The two answer different questions:

  * the state store records what changed about the coordinates
  * this records whether the mechanism producing those records behaved

That distinction matters because of one thing the semantic store deliberately
hides: **zero new revisions does not prove that only one observer ran.** Two
accidentally-connected observers would both assess the project, and the diff
would suppress the duplicate records — the history would look perfect while the
work was done twice. Batch counts, probe counts and session ids make that
visible; the history never can.

Opt-in and bounded on purpose. Off by default, capped ring buffer, and it never
records data-source strings or credentials — only counts, identifiers and
timings.

Pure. No Qt, no PyQGIS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

MAX_ENTRIES = 200


@dataclass(frozen=True)
class TraceEntry:
    session: str
    generation: int
    assessed: int
    changed: int
    publication: str          # accepted | discarded | failed
    elapsed_ms: int
    stage: str = ""           # for failures: which stage, never the payload

    def __str__(self) -> str:
        head = "session={} generation={} assessed={} changed={}".format(
            self.session, self.generation, self.assessed, self.changed)
        tail = "publication={} elapsed_ms={}".format(self.publication, self.elapsed_ms)
        if self.stage:
            tail += " stage={}".format(self.stage)
        return head + "\n  " + tail


class Trace:
    """A capped ring of observer batches. Off unless explicitly enabled."""

    def __init__(self, enabled: bool = False, limit: int = MAX_ENTRIES):
        self.enabled = enabled
        self._limit = limit
        self._entries: List[TraceEntry] = []
        self._batches = 0
        self._probes = 0

    def record(self, session: str, generation: int, assessed: int, changed: int,
               publication: str, elapsed_ms: int, stage: str = "") -> None:
        self._batches += 1
        if publication != "discarded":
            self._probes += assessed
        if not self.enabled:
            return
        self._entries.append(TraceEntry(
            session=session, generation=generation, assessed=assessed,
            changed=changed, publication=publication,
            elapsed_ms=int(elapsed_ms), stage=stage))
        if len(self._entries) > self._limit:
            del self._entries[0:len(self._entries) - self._limit]

    # -- what the history cannot tell you ---------------------------------
    @property
    def batches(self) -> int:
        """Counted even when the trace is disabled — it is the duplicate-work
        signal, and it costs one integer."""
        return self._batches

    @property
    def probes(self) -> int:
        return self._probes

    @property
    def entries(self):
        return tuple(self._entries)

    def reset_counters(self) -> None:
        self._batches = 0
        self._probes = 0

    def as_text(self) -> str:
        if not self._entries:
            return "trace: {} batches, {} probes (no entries — trace disabled)".format(
                self._batches, self._probes)
        lines = ["trace: {} batches, {} probes".format(self._batches, self._probes)]
        lines.extend(str(e) for e in self._entries)
        return "\n".join(lines)
