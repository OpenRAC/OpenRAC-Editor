#!/usr/bin/env python3
"""The release archive of OpenRAC Editor for one system, as OpenRAC's launcher downloads it
(launcher/core/src/editor_install.rs): the editor's program where openrac/editor.json says, the
editor's Python tools and project files (openrac/, tests left out), the guide and the licenses.

  python3 build/package.py PROGRAM linux|windows|macos VERSION OUT.zip

PROGRAM is the build's (bin/godot.<platform>.editor.<arch>[.exe]); on macOS it goes into the
application bundle misc/dist/macos_tools.app.
"""

import json
from pathlib import Path
import sys
import zipfile

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "openrac" / "editor.json"


def wanted(rel: Path) -> bool:
    """Whether openrac/REL goes into the archive: everything but tests and caches."""
    if "__pycache__" in rel.parts or rel.suffix == ".pyc":
        return False
    return not rel.name.startswith("test_") and rel.name != "run_tests.sh"


def put(archive: zipfile.ZipFile, name: str, data: bytes, executable: bool = False) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    archive.writestr(info, data)


def package(program: Path, system: str, version: str, out: Path) -> None:
    manifest = json.loads(MANIFEST.read_text())
    manifest["version"] = version
    at = manifest["program"][system]
    with zipfile.ZipFile(out, "w") as archive:
        if system == "macos":
            bundle = REPO / "misc/dist/macos_tools.app"
            top = at.split("/Contents/")[0]
            for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
                put(archive, f"{top}/{path.relative_to(bundle).as_posix()}", path.read_bytes())
        put(archive, at, program.read_bytes(), executable=True)
        for path in sorted(p for p in (REPO / "openrac").rglob("*") if p.is_file()):
            rel = path.relative_to(REPO / "openrac")
            if rel.as_posix() == "editor.json" or not wanted(rel):
                continue
            put(archive, f"openrac/{rel.as_posix()}", path.read_bytes())
        put(archive, "openrac/editor.json", (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
        for doc in ("docs/LEVEL_EDITOR.md", "LICENSE.txt", "COPYRIGHT.txt"):
            if (REPO / doc).is_file():
                put(archive, doc, (REPO / doc).read_bytes())


def main() -> int:
    if len(sys.argv) != 5 or sys.argv[2] not in ("linux", "windows", "macos"):
        print(__doc__, file=sys.stderr)
        return 2
    package(Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
