# CRS Inspector — Design

A QGIS plugin that keeps a readable record of what happened to your coordinates.

Status: **implemented through the change-set engine.** `crs_inspector/` loads as a
plugin; `core/` (model, probe, grading, diffing, serialize) and the dock panel
are written and covered by 24 headless tests. The log, timeline and alignment
views are not built.

M0 is done — `spike/m0_dump_crs_state.py` runs headless against QGIS 4.2.2 /
PROJ 9.8, output in `spike/m0_crs_state.txt`. Between it and the first offscreen
widget test, five claims in this document were corrected; see
[§11](#11-what-m0-settled).

---

## 1. The problem

QGIS already knows all of this. It just throws most of it away.

| Fact | Where it lives today | Failure mode |
|---|---|---|
| Project CRS | Status bar button | Fine |
| Layer CRS | Layer Properties → Source | One layer at a time, three clicks |
| Chosen datum operation | Project Properties → Transformations | A table you visit once and forget |
| **Ballpark transform used** | Message-bar toast | **Gone in seconds; the map still looks perfect** |
| Missing NTv2 / geoid grid | Nowhere | Silently degrades to a Helmert fit |

The ballpark case is the wound to build around. A ballpark transform means PROJ
found no datum shift between two datums and reprojected without one. Nothing
visibly breaks — layers still align *with each other*, because they are all wrong
in the same direction. You export the PDF, it is several metres out, and nobody
finds out.

The information exists for exactly as long as you are not looking at it.

## 2. What we took from the scale bar

The scale bar is the design lineage, but the thing worth copying is its
**character, not its form**: one object that is useful in many ways, built simply.

What that means concretely:

- **One underlying thing, several views.** The scale bar is a canvas decoration, a
  layout item, and a numeric readout — all reading from one fact.
- **Ambient and persistent.** It does not interrupt, and it does not vanish.
- **Same currency as the map.** It speaks in the units you are already thinking in.
- **Graceful.** Under pressure it degrades to a different value, never to an error.

An earlier draft of this document took the scale bar's *shape* instead, and
proposed an ambient corner widget drawing transform uncertainty as a bar. That is
[dropped](#13-tried-and-dropped).

## 3. The model: one kind of record

### 3.1 State chronology is story

There are two chronologies and they nest:

- **Session chronology** — you loaded a layer, you changed the project CRS.
  Wall-clock time.
- **Pipeline chronology** — your coordinate was on British National Grid, then
  un-projected, then shifted via OSTN15, then projected. Steps, not time.

Same shape at two scales. Every moment in session time contains a pipeline per
layer. A story is just a chronology of states, so the record only ever has to
store states.

### 3.2 The project is an object

Changing the project CRS is **not** an event with twelve effects. It is one more
state change — on the project — that the other changes point at. Twelve layers
plus the project is thirteen state changes of a single kind.

This matters because the alternative quietly grows a second primitive. Once you
have "states that record" and "events that cause," the event half accumulates its
own semantics until you are back to an activity log with better manners.

### 3.3 The diff is the primitive

A record is written when state **changes**, not when something happens. This is
the rule that keeps a log from becoming noise you learn to skip.

It applies one level down too. Changing the project CRS does not write a row per
layer — it writes rows only for layers whose state actually differed. Layers
already in the target frame, or needing no datum shift, do not move and are not
recorded. The summary line then reads:

> `project CRS → EPSG:3857 — 3 of 12 layers affected`

which is worth reading precisely because it was computed rather than asserted.

### 3.4 The originating object is derived, not recorded

A layer's state is a function of its own CRS and the project's. So if a layer's
state changed while its own inputs did not, the change came from upstream. The
originator of a change set falls out of the dependency graph and never has to be
stored or asserted.

This is what keeps §3.2 honest — without it, "which object caused this" would be
event metadata smuggled back in.

### 3.5 Change sets: two traversals of one store

A change set is the id shared by everything written in one action. Storage is
per-object diffs; display is by change set. This is `git log` versus
`git log -- parcels.shp` over one store — not a storage-versus-display tension,
just two traversals of the same edges.

### 3.6 Three consequences to settle early

**Records are emitted at the end of an action, not inline.** The recompute has to
finish before a change set can be written, because you cannot diff against a state
you have not computed yet. This kills the log-as-you-go idiom entirely. Worth
knowing before building.

**A failed or slow probe needs an explicit record.** If the recompute cannot
complete, write `state unknown` and resolve it later. Silence is the exact failure
mode this project exists to fix, so it must not be the error path.

**History is keyed on durable object identity.** Effects are written on the layer,
so a layer's history has to survive the layer's membership in the project. Not
path, not name — both change under you. QGIS's generated layer id persists in the
project file and is the candidate. Removing a layer should tombstone its history,
not delete it. (Consequence to accept: remove and re-add the same file and you get
a fresh history, because it is genuinely a new membership.)

## 4. The record is the product

The primary artifact is **text**, and the views read from it. Not the other way
round.

Part of the reason is that people will paste it into an LLM, and that is the right
place for the analysis to happen. Putting a model *inside* the plugin means API
keys, running cost, plugin-repository scrutiny, data leaving without a clear press
of a button, and a feature that rots as models change. Making the output legible
to whatever model the user already has costs nothing and avoids all of it.

That promotes copy-out from a convenience to a designed format, with obligations a
screen does not have:

- **Self-describing.** Full CRS identity, not just `EPSG:4230`. Operation names
  with authority codes. Grid file names *and* whether they were found on disk.
- **Explicit unknowns.** `unbounded`, `not published`, `state unknown` as written
  values — never a blank, which a model will fill in confidently.
- **Carries what a human reader would skip.** Datum names, ensemble membership,
  coordinate epochs, units.
- **States its own blind spot.** A layer with a *wrong* assigned CRS looks perfect
  to this tool. The text says so, so the model does not overclaim on the user's
  behalf.

## 5. What the interface is

**A current transformation-evidence inspector that has history — not a history
browser whose newest rows happen to describe the present.** The store stays as
it is; the primary question the UI answers is "what is true now, and what does
the evidence support?"

Three things coexist in one state record and must never become interchangeable:

| | |
|---|---|
| **configured inputs** | project CRS, authored transformation policy, layer CRS |
| **observed behaviour** | the operation actually instantiated, its published accuracy, grid availability |
| **conclusions** | the grade, and only what the observation supports |

### 5.0 The assessment target is part of the claim

Quality is not an intrinsic property of a layer. It is a property of a
*particular use* of that layer's coordinates, under particular inputs, within a
particular scope. `layer → project CRS` does not establish `layer → export CRS`:
QGIS's vector writer takes its own coordinate transform. And PROJ can select
among alternative operations per input coordinate, so one probed point is
evidence about that point, not an execution audit of every feature.

So every assessment carries its target and scope, and the wording stays bounded:
**"assessed for current map display"** is supportable; **"safe to export"** is not.

**The log.** Change sets, newest first, reached by drilling down from a current
row rather than as the front door. Each headline is an object's own diff.

**The timeline.** Objects as rows in layer-tree order, change sets as columns,
each cell a state. Built: `mockups/datum-log.html`. It exists to make §3.2 and
§3.3 visible — the project is a row like any other, and a change set that touches
the project visibly flips which layers are in agreement with it.

**Pairwise comparison — a view, not a stored object.** For "why don't these two
line up on this map?", the relevant comparison is `A → map CRS` against
`B → map CRS` — the two display routes actually in force. A freshly constructed
`A → B` operation answers a different question and is not what either layer is
using. The view juxtaposes both legs' datum, effective policy, instantiated
operation and missing dependencies. It is computed from states already held, so
it adds no record type and no synchronisation duty.

It must not manufacture a relative uncertainty by combining two published
accuracies — there are no measured residuals and no error-correlation model. And
the deeper limit is structural: if both routes carry the same displacement, it
cancels in their difference. Agreement between two layers can never establish
that either is correctly placed. That is exactly the failure this plugin exists
to catch, which is why pairwise is a diagnostic entry point and not the
definition of coordinate integrity.

**The alignment view.** See §6.

**A layout item** — a provenance block for the printed map, next to the scale bar.
Survey deliverables and regulatory submissions ship as PDFs carrying a scale bar
and a north arrow and nothing about coordinate provenance. Later, but it is the
piece that makes this professionally interesting.

### 5.1 Scoping

Scope to visible layers, but **never silently**. A hidden layer with no datum
shift still ships in the export. Any filter carries a persistent count of what it
excludes — `4 layers hidden · 1 with no shift available` — where you can see it.
Filtering is fine; disappearing a problem is not.

One filter, applied to every view, rather than a separate control per surface.

## 6. The alignment view: measured displacement, not published accuracy

Published operation accuracy is a nominal EPSG figure for a *method*. It is not
your error at any point, and drawing it implies a precision it does not have.

Draw the **measured difference** instead. Take the canvas centre, run it through
two different coordinate paths, and the difference is a real vector in map units,
drawable at the map's own scale. Two comparisons earn their place:

- **Current operation vs. best available** — *this is what you are losing by not
  having OSTN15*, as an arrow you can see rather than a number you have to weigh.
- **Layer A's frame vs. layer B's** — *these two disagree by 4.7 m right here*,
  which answers "why don't these line up" with a measurement.

Both are computed, both are directional, and both change as you pan — contextual
in the way the scale bar is contextual. The label must say what it is: the
difference between two models, not a claim about truth.

Ballpark has nothing to difference against, so it stays textual. That asymmetry is
honest and should not be smoothed over.

## 7. Encoding

**Colour is the datum. Texture is the state of the shift.**

This is the inverse of the usual red-means-bad scheme, and it is more informative.
The underlying question is *which of these layers are actually in the same
reference frame* — so two rows sharing a hue are in agreement and need no shift
between them, readable in one sweep before any label. Severity is one bit; datum
identity is the structure of the project.

Texture then carries state: a solid band for no shift, an offset ghost for a shift
applied, interrupted bands for no shift available, 45° hatch for no CRS set.

Palette: three categorical hues from a validated colourblind-safe set, capped at
three because the comparison task is all-pairs, not adjacent. A fourth datum folds
into a neutral. Validated with the palette checker in both light and dark against
the actual surfaces; the one sub-3:1 hue in light mode carries a visible text
label in every cell, which the relief rule requires.

## 8. Cognitive accessibility

Specific commitments, each constraining an implementation decision:

- **Never modal.** No dialogs. Information waits where it can be found.
- **Persistent, not transient.** The direct inversion of the toast that causes the
  problem.
- **Plain language leads, jargon follows.** "No datum shift available," then
  `EPSG:4230`, never the reverse.
- **Redundant encoding.** Shape, position and text carry state; colour reinforces.
- **Exactly one suggested action per warning.** Removing the *now what?* is an
  accessibility feature, not a convenience.
- **Copy-out is first-class.** Externalising memory is the point (§4).
- **Real `QWidget` surfaces, not `QGraphicsItem` drawing.** Screen-reader
  reachable; inherits Qt palette and UI font scaling.
- **No animation** beyond one optional pulse when a new problem appears, behind a
  reduce-motion setting.
- **Density is a live risk.** The mockup currently shows three regions at once.
  The model is simple; the screen is not yet. The small version — the thing you
  glance at rather than open — is unsolved and is the next design question.

### 8.1 Controlled vocabulary

One word per concept across UI, docs and code. Synonyms are a reading tax.

| Use | Never |
|---|---|
| shift | transformation, conversion, datum transform |
| uncertainty | error, accuracy, precision, tolerance |
| grid file | grid, shift file, NTv2 file |
| unbounded | infinite, unknown, undefined |
| operation | pipeline, method, transform path |
| change set | event, action, transaction |

`accuracy` survives in code only where it names a value read from PROJ/EPSG
metadata, translated at the UI boundary.

## 9. Architecture

```
crs_inspector/
  plugin.py            # init/unload, wiring only
  core/                # ZERO Qt imports — headless-testable
    model.py           #   ObjectState, ChangeSet, dataclasses
    probe.py           #   the ONLY module that touches QgsProject
    diffing.py         #   pure: two state maps -> change set (incl. originator)
    narrate.py         #   pure: state -> plain-language pipeline steps
    serialize.py       #   pure: record -> the text format of §4
  ui/
    log_panel.py
    timeline.py
    alignment.py
  layout/item.py       # later
  tests/
```

`core/` imports no Qt and no GUI. `diffing.py`, `narrate.py` and `serialize.py`
are pure functions over dataclasses, so they get table-driven pytest with no QGIS
instance running. `probe.py` is the single seam where PyQGIS enters, and therefore
the only thing to stub.

**Invalidation:** `QgsProject.crsChanged`, `transformContextChanged`,
`layersAdded` / `layersRemoved`, per-layer `crsChanged`, project read/clear, layer
visibility. Debounce ~150 ms — project loading fires these in a burst, and per
§3.6 the change set is written once the recompute settles.

**Performance:** constructing a `QgsCoordinateTransform` can hit the PROJ database
on disk. Cache state on `(source_authid_or_wkt_hash, target_authid, context_hash,
epoch)`. If profiling demands a `QgsTask`, note that PROJ contexts are
thread-local — build transforms inside the worker and return only dataclasses.

## 10. Test and demonstration cases

Fixtures and README screenshots, deliberately the same set.

| Case | Pair | Expected |
|---|---|---|
| **The grid file** | `EPSG:27700` → `EPSG:4326`, with and without OSTN15 | ~0.1 m vs ~5 m — same project, difference is one file on disk |
| **True ballpark** | `EPSG:4267` → `EPSG:6318`, NADCON5 grids absent | accuracy `-1` across differing datums; unbounded |
| ~~ED50 ballpark~~ | ~~`EPSG:4230` → `EPSG:27700`~~ | **wrong** — a published 2 m operation exists (§11.2) |
| **The inversion** | project CRS `27700` → `3857` | layers that were fine now need a shift, and two ballpark layers resolve |
| **No CRS** | layer with unset CRS | recorded as `unknown`, no exception |
| **No change** | project CRS moves under a layer with no CRS | **no record written** — the diff rule, one level down |

The last one is the test that proves §3.3 rather than describing it.

## 11. What M0 settled

Run on QGIS 4.2.2 / PROJ 9.8 / Python 3.12. Raw output: `spike/m0_crs_state.txt`.

### 11.1 Ballpark detection — the plan was wrong

**`fallbackOperationOccurred()` is not the ballpark signal.** On
`EPSG:4267 → EPSG:6318` it returns `False` while the operation actually in force
is named *"Ballpark geographic offset from NAD27 to NAD83(2011)"*. It reports
something narrower: that a **specified** operation failed and PROJ fell back. When
nothing is specified and PROJ simply picks a ballpark as its best option, that is
not a fallback — it is the primary choice, and the flag stays false.

**The real signal is `instantiatedCoordinateOperationDetails().accuracy == -1.0`**
— with one complication. It is overloaded: `EPSG:3857 → EPSG:4326` also reports
`-1.0` and is perfectly healthy, because no datum shift is involved at all. `-1`
means "no accuracy published," which covers both the benign and the alarming case.

**Disambiguate on the datum, not the number:**

| | same datum | different datum |
|---|---|---|
| **accuracy ≥ 0** | shift with published uncertainty | shift with published uncertainty |
| **accuracy == -1** | no shift needed — healthy | **ballpark** |

The datum comparison needed here is the same key §7 needs for colour, so one
function serves both.

Also settled: the static handler setters are marked *not available in Python
bindings*, so "proactive only" is now a fact rather than a judgement call.

### 11.2 Other corrections

**`isShortCircuited()` means identical CRS, not same datum.** It is `False` for
`3857 → 4326`. Do not use it to mean "no shift required."

**Datum identity needs two tiers.** `datumEnsemble()` returns nothing for
single-datum CRSs — OSGB36, ED50, NAD27 and GDA2020 all come back empty. The WKT
`DATUM[...]` node does work (`"Ordnance Survey of Great Britain 1936"`). So: use
the ensemble when present, else the WKT datum node. §7's colour encoding is
feasible.

**`authority` and `code` on the instantiated operation are usually empty.**
Populated for `3857 → 4326`, empty for the OSGB36 and GDA2020 pairs. The operation
*name* is the only identifier you can rely on.

**`coordinateEpoch()` returns `nan`, not `None`.** Test with `math.isnan`.

**ED50 → OSGB36 is not a ballpark.** There is a published *"OSGB36 to ED50 (1)"*
at 2 m. §10 said otherwise and was wrong; the genuine ballpark fixture is
NAD27 → NAD83(2011) with the NADCON5 grids absent.

**PROJ writes to stderr while probing** (`hgridshift: could not find required
grid(s)`). A plugin probing every layer will emit this. Capture or suppress it.

### 11.3 §6 is feasible, with real numbers

Pinning a context to a specific operation via `addCoordinateOperation()` works,
and differencing two pinned transforms at one point produces exactly the
measurement §6 asks for. At a central-England point:

| pair | widest disagreement among available operations |
|---|---|
| `27700 → 4326` | **104.8 m** (the ballpark alternative) |
| `4230 → 27700` | **147.3 m** |
| `3857 → 4326` | 0.0 m — same datum, correctly nothing |
| `4267 → 6318` | 0.0 m — see below |

The NAD27 result is the interesting one. It reports zero not because the pair is
healthy but because **the ballpark is already the operation in force**, so there
is nothing to difference it against. That is §6's predicted asymmetry, confirmed
empirically rather than assumed: where ballpark is all you have, the visual has
nothing to draw and the finding stays textual.

### 11.4 Qt5/Qt6 is not solved by the shim

Found while building, not by reading: `qgis.PyQt` abstracts *which* Qt is
present, so imports work unchanged on both. It does **not** abstract enum
scoping. Qt6 removed the flat spelling:

    Qt5   QAbstractItemView.SelectRows
    Qt6   QAbstractItemView.SelectionBehavior.SelectRows

The scope names are also not guessable — the setter is `setEditTriggers` but the
enum type is `EditTrigger`, singular. `ui/qt_compat.py` resolves each member by
searching the owner's nested enum types rather than trusting a hand-written
scope name, and every UI enum goes through it.

This is invisible to an import check. It appeared the moment a widget was
actually constructed, which is the argument for the offscreen panel test.

### 11.5 The transform context cannot be read back from the convenience API

Two findings, both verified:

- `coordinateOperations()` returns `dict[(str, str), str]` keyed on **authid
  strings only**. It carries no fallback flag, and QGIS warns that `USER:` ids
  are not portable between machines or profiles — so two unnamed custom CRSs can
  collide in those keys.
- `allowFallbackTransform()` does **not** faithfully read back the stored flag: a
  pair written with `allowFallback=False` reads back `True`.

So the authored policy is extracted from `writeXml`, whose `<srcDest>` elements
carry `allowFallback`, `coordinateOp`, and full WKT for both CRSs. The DOM work
lives in `probe.py`; canonicalisation lives in `fingerprint.py`. The raw XML is
never hashed — a serialiser change would otherwise look like a user edit.

The fingerprint promises: *identical captured configuration produces an identical
digest under this schema version*. It does not promise that semantically
equivalent CRS definitions or pipelines hash alike across QGIS and PROJ releases.
Operation strings are treated as opaque and never reordered.

**The invariant that makes this safe:** for every tracked object, a change in
`self_key` must imply a change in `state_key`. Without it, an edited-but-unused
setting could vanish from history for having changed no output. Enforced by test.

**And its counterweight:** the global policy digest is in the *project's* keys
only. It invalidates cached assessments but never enters a layer's `state_key` —
otherwise editing an unused CRS pair would change every layer's hash.

### 11.6 Environment

QGIS **4.2.2**, not the 3.34 LTR this document assumed. `QgsProjUtils` exposes
`projVersionMajor()` and `projVersionMinor()` but **not** `projVersionMicro()`.
The minimum-version decision should be re-opened against 4.x rather than 3.34.

**OSTN15 is not installed on the development machine.** The 1 m grid operation
reports `isAvailable: False` and QGIS silently instantiates the 2 m Helmert
instead. §10's headline demonstration is therefore reproducible here with no
setup at all.

## 11.7 Remaining risks

**Do not overclaim.** Published accuracy is nominal, for the method. Say
"published uncertainty for this shift." A domain reviewer will check.

**The blind spot.** A layer with a wrong assigned CRS looks perfect here — the
transform is valid, the premise is not. In the README, and in the text record.

**Open:** minimum QGIS version, now against 4.x; vertical CRS and geoid models;
what the small, glanceable view is (§8); which comparator §6 draws by default,
since "widest disagreement" and "cost of the missing grid" are different numbers.

## 12. Milestones

| | Milestone |
|---|---|
| ~~**M0**~~ | **Done.** `spike/m0_dump_crs_state.py` — dual mode: headless against the §10 fixtures, or in the QGIS console against an open project. Findings in §11. |
| **M1** | `core/` — state, diffing, originator derivation, narration, the text format. Pytest green, no UI. |
| **M2** | The log panel |
| **M3** | The text format polished + copy-out, tested by pasting into a model cold |
| **M4** | Timeline view (mockup already exists) |
| **M5** | Alignment view (§6) |
| **M6** | Layout item; CI on the `qgis/qgis` release image; packaging via `qgis-plugin-ci` |

## 13. Tried and dropped

Kept deliberately, so these do not get re-proposed.

**An ambient canvas bar drawing transform uncertainty at map scale.** Took the
scale bar's form instead of its character. Two problems: it drew published nominal
accuracy as if it were measured error, and a bar is a length while uncertainty is
directionless. Superseded by §6, which draws a real measured difference.

**A six-state visual language for that bar** (sub-pixel / visible / overflow /
unbounded / indeterminate / no-shift), and the specimen-sheet-plus-zoom-rig mockup
built to tune it. Died with the bar.

**Events as a primitive**, with one cause and N effects hanging off it. Replaced by
§3.2 — the cause is itself a state change, on the project.

**Writing a row per layer on a project-wide change.** Records activity, not change.
Replaced by §3.3.

**An LLM inside the plugin.** Replaced by §4 — make the output legible to the model
the user already has.
