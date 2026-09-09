"""Can the lifecycle checks reject the defects they were written to prevent?

Three deliberate regressions, each a single-statement cut through one boundary
of the real edit path — not a mutation-testing project, and not `_invalidate()`
removed wholesale, which would only show that deleting all behaviour breaks
everything.

| mutation                 | cut                          | must fail                    |
|--------------------------|------------------------------|------------------------------|
| omit_layer_subscription  | the crsChanged connect       | epoch_edit.revokes_currency  |
| omit_immediate_render    | render_current in _invalidate| epoch_edit.renders_stale     |
| omit_scheduling          | _timer.start in _invalidate  | epoch_edit.schedules_recheck |

The middle row is the important one: it must fail on the *widget's displayed
status* while the observer-side assertion still passes. Otherwise the harness has
not been shown to detect the original model-correct / screen-wrong defect.

A mutant counts as detected only when the mutation applied to exactly one site,
setup succeeded, the scenario ran, the designated assertion failed, and the
preserved controls still passed. A nonzero exit is NOT the criterion — that would
recreate the attribution problem with an inverted success condition.

    "<OSGeo4W>/bin/python-qgis.bat" tools/sensitivity_check.py --zip PATH
"""

from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "crs_inspector/plugin.py"
TIMEOUT = 300

SUBSCRIBE_OLD = """            try:
                layer.crsChanged.connect(handler)
            except Exception:
                continue"""
SUBSCRIBE_NEW = """            try:
                pass  # MUTANT: the subscription is deliberately not made
            except Exception:
                continue"""

INVALIDATE_OLD = """        self.observer.invalidate()
        if self.panel is not None:
            self.panel.render_current()
        self._timer.start()"""
RENDER_NEW = """        self.observer.invalidate()
        if self.panel is not None:
            pass  # MUTANT: the immediate render is deliberately omitted
        self._timer.start()"""
SCHEDULE_NEW = """        self.observer.invalidate()
        if self.panel is not None:
            self.panel.render_current()
        pass  # MUTANT: the reassessment is deliberately not scheduled"""

MUTATIONS = [
    {
        "name": "omit_layer_subscription",
        "old": SUBSCRIBE_OLD, "new": SUBSCRIBE_NEW,
        "expected_failure": "epoch_edit.revokes_currency",
        # The witness must still pass, or a broken fixture would masquerade as
        # successful detection of missing plugin wiring.
        "preserved": ["epoch_edit.signal_observed"],
    },
    {
        "name": "omit_immediate_render",
        "old": INVALIDATE_OLD, "new": RENDER_NEW,
        "expected_failure": "epoch_edit.renders_stale",
        "preserved": ["epoch_edit.revokes_currency",
                      "epoch_edit.schedules_recheck"],
    },
    {
        "name": "omit_scheduling",
        "old": INVALIDATE_OLD, "new": SCHEDULE_NEW,
        "expected_failure": "epoch_edit.schedules_recheck",
        "preserved": ["epoch_edit.revokes_currency",
                      "epoch_edit.renders_stale"],
    },
]


def arg(flag, default=None):
    argv = sys.argv[1:]
    return argv[argv.index(flag) + 1] if flag in argv else default


def extract(archive: Path) -> Path:
    workdir = Path(tempfile.mkdtemp(prefix="crs_inspector_sens_"))
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(workdir)
    return workdir


def apply_mutation(workdir: Path, mutation) -> str:
    """Alter exactly one site, and leave the module importable."""
    target = workdir / TARGET
    source = target.read_text(encoding="utf-8")
    hits = source.count(mutation["old"])
    if hits != 1:
        return "mutation site matched {} times, expected exactly 1".format(hits)
    target.write_text(source.replace(mutation["old"], mutation["new"]),
                      encoding="utf-8")
    try:
        py_compile.compile(str(target), doraise=True)
    except Exception as exc:
        return "mutated module does not compile: {}".format(exc)
    return ""


def child_python():
    """The interpreter that actually has a working QGIS environment.

    On Windows, sys.executable is the bare python3.exe inside OSGeo4W; launching
    it directly crashed the child with 0xC0000409 and no output, because the
    Qt/QGIS environment python-qgis.bat establishes was never applied. In a
    QGIS container python3 is already correct.
    """
    override = arg("--python")
    if override:
        return override
    root = os.environ.get("OSGEO4W_ROOT")
    if root:
        launcher = Path(root) / "bin" / "python-qgis.bat"
        if launcher.exists():
            return str(launcher)
    return sys.executable


def run_child(workdir: Path, report: Path):
    cmd = [child_python(), str(ROOT / "tools" / "lifecycle_check.py"),
           "--package-dir", str(workdir), "--scenario", "epoch_edit",
           "--json", str(report)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "timeout", ""
    if done.returncode not in (0, 1):
        return None, "abnormal exit {}".format(done.returncode), done.stdout + done.stderr
    if done.returncode < 0:
        return None, "signal {}".format(-done.returncode), done.stdout
    if not report.exists():
        return None, "no report produced", done.stdout
    return json.loads(report.read_text(encoding="utf-8")), "normal", done.stdout


def evaluate(name, data, termination, expected_failure, preserved):
    """Detected requires the designated failure AND the surviving controls."""
    if data is None:
        return {"mutation": name, "termination": termination, "result": "inconclusive"}
    by_id = {r["id"]: r["ok"] for r in data["results"]}
    setup_ok = all(ok for rid, ok in by_id.items() if rid.startswith("setup."))
    reached = any(rid.startswith("epoch_edit.") for rid in by_id)
    designated = by_id.get(expected_failure)
    controls_ok = all(by_id.get(c) is True for c in preserved)

    if not setup_ok:
        result = "setup_failed"
    elif not reached:
        result = "scenario_not_reached"
    elif designated is None:
        result = "designated_assertion_missing"
    elif designated is True:
        result = "NOT DETECTED"
    elif not controls_ok:
        result = "detected_but_controls_broken"
    else:
        result = "detected"
    return {
        "mutation": name, "setup": "passed" if setup_ok else "failed",
        "scenario": "epoch_edit" if reached else "not reached",
        "expected_failure": expected_failure,
        "observed_failures": data["failed"],
        "preserved_controls": "passed" if controls_ok else "FAILED",
        "termination": termination, "result": result,
    }


def main() -> int:
    argv = sys.argv[1:]
    if "--zip" not in argv:
        print("usage: sensitivity_check.py --zip PATH")
        return 2
    archive = Path(argv[argv.index("--zip") + 1]).resolve()
    import hashlib
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print("artifact {}".format(archive.name))
    print("sha256   {}\n".format(digest))

    reports = []

    # Baseline: the unmodified artifact must pass. An assertion that always
    # failed would reject every mutant — and this too.
    work = extract(archive)
    report = work.parent / "baseline.json"
    data, termination, out = run_child(work, report)
    baseline_ok = data is not None and not data["failed"]
    print("baseline: {}  ({})".format(
        "passed" if baseline_ok else "FAILED", termination))
    if data is not None and data["failed"]:
        print("  unexpected failures: {}".format(data["failed"]))
    if not baseline_ok:
        print(out[-1500:])
    shutil.rmtree(work, ignore_errors=True)
    reports.append({"mutation": "(none)", "result":
                    "baseline_passed" if baseline_ok else "baseline_failed"})

    for mutation in MUTATIONS:
        # Each mutant starts from a FRESH extraction of the same archive, so a
        # modified derivative is never confused with the original artifact.
        work = extract(archive)
        problem = apply_mutation(work, mutation)
        if problem:
            reports.append({"mutation": mutation["name"], "result": "not_applied",
                            "detail": problem})
            shutil.rmtree(work, ignore_errors=True)
            continue
        report = work.parent / "{}.json".format(mutation["name"])
        data, termination, _out = run_child(work, report)
        reports.append(evaluate(mutation["name"], data, termination,
                                mutation["expected_failure"],
                                mutation["preserved"]))
        shutil.rmtree(work, ignore_errors=True)

    print()
    for entry in reports[1:]:
        print("mutation:          {}".format(entry["mutation"]))
        for key in ("setup", "scenario", "expected_failure", "observed_failures",
                    "preserved_controls", "termination", "detail"):
            if key in entry:
                print("  {:<18} {}".format(key + ":", entry[key]))
        print("  {:<18} {}\n".format("result:", entry["result"]))

    detected = [e for e in reports[1:] if e.get("result") == "detected"]
    ok = baseline_ok and len(detected) == len(MUTATIONS)
    print("baseline passes and {}/{} deliberate regressions detected".format(
        len(detected), len(MUTATIONS)))
    print("\nCLAIM: the harness detects these three specified regressions."
          "\n       Not that it is complete.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
