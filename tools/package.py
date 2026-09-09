"""Build an installable plugin ZIP for Plugins -> Install from ZIP.

Deliberately packages the *plugin directory only*, so the test exercises what a
user would actually install. A development shell that puts the repository on
sys.path can supply modules the package itself lacks — this exists to make that
impossible to do by accident.

Stamps a build id derived from the packaged file contents, so an acceptance
result can name the exact build it belongs to.

    python tools/package.py

Plain Python. No QGIS needed.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "crs_inspector"
DIST = ROOT / "dist"

#: Anything the installed plugin must not need in order to run.
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def packaged_files():
    for path in sorted(PLUGIN.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        if path.name == "BUILD.txt":
            continue                      # regenerated each build
        yield path


def build_id(files) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(PLUGIN).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def read_version() -> str:
    for line in (PLUGIN / "metadata.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    return "0.0.0"


def main() -> int:
    files = list(packaged_files())
    if not files:
        print("no files found under {}".format(PLUGIN))
        return 1

    entry = PLUGIN / "__init__.py"
    if "classFactory" not in entry.read_text(encoding="utf-8"):
        print("refusing to package: __init__.py has no classFactory()")
        return 1

    ident = build_id(files)
    version = read_version()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    build_note = (
        "CRS Inspector\n"
        "version   {}\n"
        "build     {}\n"
        "packaged  {}\n"
        "files     {}\n\n"
        "Quote the build id in any acceptance result. A result without one\n"
        "cannot be tied to the code that produced it.\n"
    ).format(version, ident, stamp, len(files))

    DIST.mkdir(exist_ok=True)
    out = DIST / "crs_inspector-{}-{}.zip".format(version, ident)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, Path("crs_inspector") / path.relative_to(PLUGIN))
        zf.writestr("crs_inspector/BUILD.txt", build_note)

    names = zipfile.ZipFile(out).namelist()
    required = ["crs_inspector/__init__.py", "crs_inspector/metadata.txt",
                "crs_inspector/plugin.py"]
    missing = [r for r in required if r not in names]
    if missing:
        print("package is incomplete, missing: {}".format(missing))
        return 1

    print(build_note)
    print("wrote {}  ({:,} bytes, {} entries)".format(
        out.relative_to(ROOT), out.stat().st_size, len(names)))
    print("\nInstall via Plugins -> Manage and Install Plugins -> Install from ZIP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
