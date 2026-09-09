# First-load handoff

Architecture is frozen. The deliverable is an installable candidate; the next
report should be *"this exact package loaded into this exact target, here is
what appeared"* — not more passing tests.

| | |
|---|---|
| **Package** | `dist/crs_inspector-0.1.0-66fbfe400d4f.zip` |
| **Build id** | `66fbfe400d4f` (also in `crs_inspector/BUILD.txt` inside the ZIP) |
| **Fixture** | `fixture/crs_inspector_fixture.qgz` + `fixture/README.md` |

Quote the build id in every result. A result that cannot be tied to the code
that produced it is not evidence.

## Install

**Plugins → Manage and Install Plugins → Install from ZIP.** Not a development
shell that puts the repository on `sys.path` — the package must supply its own
runtime, and a dev path can hide a missing module.

Verified: extracting the ZIP with the repository asserted *off* `sys.path` loads
`classFactory`, builds the plugin object, and classifies all three fixture
layers correctly.

Start in a disposable profile: **Settings → User Profiles → New profile.** Note
that a clean profile separates settings and plugins — it does **not** guarantee
an identical transformation environment. Re-record the resource conditions from
`fixture/README.md` on the machine under test rather than assuming them.

## Expected on the fixture

Project CRS `EPSG:6318` NAD83(2011). Observed on the build machine with the
NADCON5 grids **absent**:

| Layer | Expected | Depends on |
|---|---|---|
| `control_projection_only` | no datum shift | nothing — the control |
| `case_ballpark_nad27` | ballpark, flagged | NADCON5 grids being absent |
| `case_published_shift` | shift, 2 m | published operation availability |

Indicator should read `Datum · 1 flagged`.

If the machine under test *has* the NADCON5 grids, `case_ballpark_nad27` will
not be a ballpark. That is the fixture behaving correctly, not a failure —
record the resource conditions and the observed result.

---

## The session

Record **passed / failed / not run** for each. Offscreen success stays offscreen
success; a desktop result belongs to the build and target it was seen on.

| # | Exercise | Required behaviour | 4.2.x (Qt6) | 3.44 LTR (Qt5) |
|---|---|---|---|---|
| 1 | Enable with the fixture already open | Layers get an initial assessment, worded as **first observed**, never "added" | not run | not run |
| 2 | Refresh repeatedly, changing nothing | No new revisions. Last-checked time moves | not run | not run |
| 3 | Edit an **unused** transformation override | Project changes; no layer acquires a row | not run | not run |
| 4 | Change a relevant input, then break reassessment | Previous result stays as **history**, not current reassurance | not run | not run |
| 5 | Open another project mid-assessment | Nothing from the old project appears in the new one | not run | not run |
| 6 | Disable / re-enable twice, then make one change | One dock, one listener set, **one batch** | not run | not run |
| 7 | New Project → Add Layer | Observer resumes; the added layer reads as **added**, not first-observed | not run | not run |

**Cross-cutting:** open a clean saved project, note `isDirty()`, assess, check
again. Assessment must not edit the project it inspects. Do not "fix" a failure
by resetting the flag.

### Forcing #4 for real

1. Let `case_published_shift` assess cleanly.
2. Change its assigned CRS.
3. Move or rename the PROJ data directory so reassessment cannot complete.
4. The indicator must read `last checked HH:MM · inputs changed since`, and the
   panel must carry the failure note. It must **not** still read as current.

### Why #6 needs the trace, not the history

Zero new revisions does **not** prove only one observer ran — two accidentally
connected observers would both assess, and the diff would suppress the duplicate
records. The history would look perfect while the work happened twice.

Enable the trace to see it (`plugin.trace.enabled = True` from the Python
console), then read `plugin.trace.as_text()`. Batch and probe counts are kept
even when the trace is off.

```
session=<project>|<id> generation=12 assessed=3 changed=0
  publication=accepted elapsed_ms=18
```

`publication=discarded` means a superseded batch was correctly thrown away.

## The product test, not just the dock test

A dock appearing is necessary and not sufficient. With the fixture open, let the
ordinary QGIS transient warning disappear, then ask:

- Can I still find which layer needs attention?
- Can I see the exact evidence behind that?
- Can I tell **which coordinate use** was assessed?
- Can I tell a current result from a remembered one?

Then check that the **indicator, the panel, and the copied text record** describe
the same assessment and the same limitations.

- They disagree → a presentation/integration defect.
- They agree but need the model explained aloud → an interface problem.

Neither is a reason to touch the diff engine.

## Watch for

- **First assessment blocking the UI** on a large project — the probe runs on the
  main thread. If it stalls, that is the signal to move it to a `QgsTask`,
  copying inputs first, since QGIS forbids touching `QgsProject`, layers or GUI
  objects from a background thread.
- **Message-bar leakage.** `disableFallbackOperationHandler(True)` is set per
  probe transform so that inspecting a broken project does not itself warn.
- **PROJ writing to stderr** (`hgridshift: could not find required grid(s)`).
  Confirm it does not surface as an error dialog.
- **Two docks after re-enabling** — check the Panels menu, not just the screen.

## Result log

| Date | Build | Target | Profile | Result |
|---|---|---|---|---|
| | `66fbfe400d4f` | | | not run |
