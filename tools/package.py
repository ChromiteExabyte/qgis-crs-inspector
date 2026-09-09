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

#: Files outside the plugin directory that must travel inside the archive.
#: The repository root is not present in an installed plugin, so a licence
#: living only there ships nothing and leaves __init__.py pointing at a file
#: the user does not have.
STAGED = {"crs_inspector/LICENSE": ROOT / "LICENSE"}

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
    """Identity of the packaged CONTENT, staged files included.

    Distinct from the archive's own hash: the note embeds a packaging
    timestamp, so two builds of identical content produce the same build id and
    different archive bytes. Neither substitutes for the other, so both are
    reported.
    """
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(PLUGIN).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    for arcname in sorted(STAGED):
        digest.update(arcname.encode("utf-8"))
        digest.update(STAGED[arcname].read_bytes())
    return digest.hexdigest()[:12]


def archive_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        for arcname, source in sorted(STAGED.items()):
            if not source.exists():
                print("refusing to package: missing {}".format(source))
                return 1
            zf.write(source, arcname)
        zf.writestr("crs_inspector/BUILD.txt", build_note)

    names = zipfile.ZipFile(out).namelist()
    required = ["crs_inspector/__init__.py", "crs_inspector/metadata.txt",
                "crs_inspector/plugin.py", "crs_inspector/LICENSE"]
    missing = [r for r in required if r not in names]
    if missing:
        print("package is incomplete, missing: {}".format(missing))
        return 1

    print(build_note)
    print("wrote {}  ({:,} bytes, {} entries)".format(
        out.relative_to(ROOT), out.stat().st_size, len(names)))
    print("sha256    {}".format(archive_sha256(out)))
    print("\nInstall via Plugins -> Manage and Install Plugins -> Install from ZIP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
