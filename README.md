# CRS Inspector

**A QGIS plugin that tells you when your coordinates were never actually shifted.**

[![tests](https://github.com/ChromiteExabyte/qgis-crs-inspector/actions/workflows/tests.yml/badge.svg)](https://github.com/ChromiteExabyte/qgis-crs-inspector/actions/workflows/tests.yml)
![QGIS 3.44 LTR and 4.x](https://img.shields.io/badge/QGIS-3.44%20LTR%20%7C%204.x-589632)
![GPL-2.0-or-later](https://img.shields.io/badge/licence-GPL--2.0--or--later-blue)

QGIS knows which datum shift it applied to every layer and how good that shift is.
It tells you once, in a message bar, and then throws it away.

The dangerous case is a **ballpark transform**: PROJ finds no operation between two
datums and reprojects without shifting. Nothing looks wrong. Every layer is wrong in
the same direction, so they still align *with each other*. The export is metres out
and nobody finds out.

![Interferogram of a ballpark transformation across Europe](docs/interferogram-europe-ballpark.png)

*What a ballpark transformation actually costs, across Europe. Each colour cycle is
20 m of ground displacement between the ballpark and the best published ED50 → WGS 84
operation; the field runs from 42 m to 189 m. Measured on a live PROJ install, not
illustrated — see [the interferogram mockup](mockups/interferogram.html).*

---

## The bug this exists to catch

`QgsCoordinateTransform.fallbackOperationOccurred()` looks like the ballpark signal.
It is not. Straight from [`spike/m0_crs_state.txt`](spike/m0_crs_state.txt):

```
FIXTURE  NAD27 grids   EPSG:4267 -> EPSG:6318
  candidate_operations:
    name:        NAD27 to NAD83(2011) (NADCON5, CONUS)
    accuracy_m:  0.18                     <- unavailable, grids not installed
    name:        Ballpark geographic offset from NAD27 to NAD83(2011)
    accuracy_m:  -1.0
  instantiated:
    name:        Ballpark geographic offset from NAD27 to NAD83(2011)
  BALLPARK_USED: False                    <- the flag, while a ballpark is in force
```

It reports that an **explicitly specified** operation failed and PROJ fell back. When
nothing is specified and PROJ simply picks a ballpark as its best available option —
the common case — the flag stays `False`.

The usable signal is a published accuracy of `-1`, but it is overloaded:
`EPSG:3857 → EPSG:4326` also reports `-1` and is perfectly healthy, because no datum
shift is involved at all. So the first cut is on **datum equality**, never the number:

|                     | same datum          | different datum      |
|---------------------|---------------------|----------------------|
| **accuracy known**  | shift               | shift                |
| **accuracy absent** | no shift — healthy  | **needs attention**  |

The bottom-right cell splits again, because PROJ documents a negative accuracy as
*"unknown or error"* — weaker than *"the datum shift was omitted"*. Only an operation
that names itself a ballpark supports the stronger claim; anything else is reported as
**unpublished accuracy across a datum boundary**. Both want attention. Only one states
a diagnosis.

Every direction of this is pinned in [`tests/test_grading.py`](tests/test_grading.py),
because getting it backwards fails loudly one way and silently the other.

I designed against `fallbackOperationOccurred()`, wrote a throwaway spike to check the
API before building on it, and the spike proved the design wrong. It corrected five
claims in total, recorded in [DESIGN.md §11](DESIGN.md).

## Install

**Plugins → Manage and Install Plugins → Install from ZIP**, using a build from
[`tools/package.py`](tools/package.py) or the CI artifact.

Targets **QGIS 3.44 LTR (Qt5) and 4.x (Qt6)**. Qt imports go through the `qgis.PyQt`
shim — but that covers imports, *not* enum scoping, which is a separate Qt6 break that
stays invisible until a widget is constructed. See
[`crs_inspector/ui/qt_compat.py`](crs_inspector/ui/qt_compat.py). CI constructs the
panel on both toolkits for exactly that reason.

## What it does

A dock listing every layer **grouped by its source CRS**. Layers sharing a source CRS
share the whole route to the project CRS, so they share one assessment — the verdict
sits on the group rather than repeating down every row.

```
1 flagged · checked 07:16
▸ NAD27  (EPSG:4267)          no datum shift available   case_ballpark_nad27
▸ NAD83(2011)  (EPSG:6318)    no shift needed            control_projection_only
▸ WGS 84  (EPSG:4326)         shifted, 2 m               case_published_shift
```

Grouping carries identity, so there is no categorical colour palette to cap or explain.
Severity is a themed QGIS icon, which survives the dark themes.

**Freshness is tracked separately from state.** Change a layer's CRS, have the re-check
fail, and the previous result stays readable but stops being presented as current —
`last checked 07:16 · inputs changed since`. Every grading test can pass while a panel
shows yesterday's correct answer against today's inputs.

**Copy record** puts a plain-text provenance record on the clipboard, designed to be
pasted into a language model, a report, or an email. That is deliberately the opposite
of embedding a model in the plugin: no API keys, no running cost, no data leaving
without a deliberate press of a button, nothing to rot as models change.

## What it cannot see

**A layer with a wrong assigned CRS looks perfect.** The transformation is valid; the
premise is not. The text record says so, so a reader does not overclaim on your behalf.

Published accuracy is the nominal figure for an operation *as a method*, not measured
error at any point. And comparing two operations can never establish that either is
correct — a shared error cancels in the difference.

## Development

`crs_inspector/core/` imports no Qt and no PyQGIS except in `probe.py`, the single seam
where the QGIS API enters. Everything else is pure functions over dataclasses, so the
tests run on plain Python with nothing installed:

```bash
python -m pytest tests/ -q          # 58 passed
python tools/package.py             # build the installable ZIP
python tools/verify_package.py      # load it standalone and check the fixture
```

`verify_package.py` extracts the ZIP, strips the repository from `sys.path`, and asserts
the import resolved to the extracted copy — so a development path can never supply what
the installed package lacks.

The spike still runs, headless against fixture CRS pairs or pasted into the QGIS Python
console against a real project:

```bash
"<OSGeo4W>/bin/python-qgis.bat" spike/m0_dump_crs_state.py
```

## Layout

| | |
|---|---|
| `crs_inspector/core/` | model, probe, grading, change sets, freshness, text record — no Qt |
| `crs_inspector/ui/` | the dock panel and the Qt5/Qt6 enum shim |
| `spike/` | the throwaway that corrected the design, its raw output, and the acceptance sheet |
| `fixture/` | a self-contained project with a control and two known cases |
| `mockups/` | interferogram and registration views, built from measured fields |
| `tools/` | packaging, verification, icon and image generation |
| `tests/` | 58 tests, no QGIS required |
| [DESIGN.md](DESIGN.md) | the model, what the spike settled, and what was tried and dropped |

## Status

Early, and honest about it. The core, probe, change-set engine, text record and panel
are written and tested. **It has not yet been loaded into a desktop QGIS session** —
everything so far is headless or offscreen. The acceptance sheet for that first load is
[`spike/acceptance_session.md`](spike/acceptance_session.md); panel screenshots land
here once it has been run.

Not built: the log view, pairwise layer comparison, persistence, and the interferogram
as an in-plugin tab.

## Licence

GPL-2.0-or-later. See [LICENSE](LICENSE).
