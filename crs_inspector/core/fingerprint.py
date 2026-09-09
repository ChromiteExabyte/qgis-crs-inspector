"""Fingerprinting the project's authored transformation policy.

The project's transform context is part of the project's **own inputs**: editing
it is an authored change, and the project should show as having locally changed
inputs when it moves. Without this, changing a coordinate operation produces a
change set with no locally-changed object at all.

Two separate questions, deliberately not conflated:

  * *Did the authored configuration change?*  Fingerprint the whole stored
    context. That is what lives in the project's own-input key.
  * *Did this layer's assessment change?*  Compare only that layer's relevant
    policy and observed operation.

The context fingerprint therefore **invalidates cached assessments** but must
never be folded into a layer's state key. If it were, editing an entry for a CRS
pair no layer uses would change every layer's hash and re-create exactly the
unconditional fan-out the diff rule exists to avoid.

What this promises
------------------
Identical captured configuration produces an identical fingerprint *under this
schema version*. It does **not** promise that semantically equivalent CRS
definitions or pipelines hash alike, now or across future QGIS and PROJ
releases. When the schema changes, that is a migration — not a user edit.

Pure. No Qt, no PyQGIS: extraction lives in `probe`, canonicalisation lives here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

#: Bump when the canonical encoding changes. A fingerprint from a different
#: schema is not comparable and must be treated as a migration.
SCHEMA_VERSION = 1

EMPTY = "empty-context"


@dataclass(frozen=True)
class ContextEntry:
    """One authored source→destination transformation policy entry.

    `operation` of "" means *stored, explicitly empty* — select default operation
    behaviour — which is a different authored state from having no entry at all.
    The absence of an entry is represented by the entry simply not being present.
    """

    source: str            # full CRS definition (WKT), not an authid
    destination: str
    operation: str
    allow_fallback: bool
    source_authid: str = ""       # metadata only; USER: ids are not portable
    destination_authid: str = ""

    def as_canonical(self) -> list:
        # Direction is preserved: (source, destination) is never reordered.
        return [self.source, self.destination, self.operation,
                bool(self.allow_fallback)]


def canonical_encoding(entries: Sequence[ContextEntry]) -> str:
    """A stable text encoding of the authored policy.

    Entries are sorted so that storage order does not affect the digest, but the
    source/destination pair inside each entry keeps its direction.
    """
    payload = {
        "schema": SCHEMA_VERSION,
        "entries": sorted((e.as_canonical() for e in entries),
                          key=lambda row: (row[0], row[1], row[2], row[3])),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True)


def context_fingerprint(entries: Optional[Iterable[ContextEntry]]) -> str:
    """Digest the authored policy. Distinct from 'no context observed'."""
    if entries is None:
        return "unobserved"
    entries = tuple(entries)
    if not entries:
        return "{}:{}".format(SCHEMA_VERSION, EMPTY)
    digest = hashlib.sha256(canonical_encoding(entries).encode("utf-8"))
    return "{}:{}".format(SCHEMA_VERSION, digest.hexdigest()[:16])


def relevant_entries(entries: Sequence[ContextEntry],
                     source_authid: str,
                     destination_authid: str) -> Tuple[ContextEntry, ...]:
    """The authored entries that bear on one particular source→destination use.

    Used to decide whether a *layer's* assessment should re-diff — a policy edit
    touching an unrelated CRS pair must not disturb it. Direction is checked both
    ways because a stored entry can apply reversed.
    """
    if not source_authid or not destination_authid:
        return ()
    wanted = {(source_authid, destination_authid),
              (destination_authid, source_authid)}
    return tuple(e for e in entries
                 if (e.source_authid, e.destination_authid) in wanted)
