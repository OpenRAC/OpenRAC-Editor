#!/usr/bin/env python3
"""Checks a release archive as the player gets it, on the system it is for, before it is published:
unpacked as OpenRAC's launcher unpacks it (launcher/core/src/editor_install.rs) into a folder with
spaces and letters past ASCII, its manifest and program where they belong, the program able to run,
and the editor started without a window on a small project in that folder.

  python3 build/smoke.py ARCHIVE.zip VERSION
"""

import json
import os
from pathlib import Path
import py_compile
import plistlib
import subprocess
import sys
import tempfile
import zipfile

SYSTEM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
SCRIPT = """extends SceneTree

func _init() -> void:
\tvar file := FileAccess.open("res://smoke.txt", FileAccess.WRITE)
\tfile.store_string("ok %s" % Engine.get_version_info().string)
\tfile.close()
\tquit()
"""


def unpack(archive: Path, into: Path) -> None:
    """As the launcher does: files inside the folder only, with their Unix modes."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            if "\\" in name or name.startswith("/") or ".." in name.split("/"):
                raise SystemExit(f"{archive.name}: the launcher refuses {name!r}")
            target = into / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            mode = info.external_attr >> 16 & 0o777
            if mode and os.name == "posix":
                target.chmod(mode & 0o755)


def check(archive: Path, version: str) -> list[str]:
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "Mateusz Kłysz" / "Open RAC"
        editor = home / "editor"
        unpack(archive, editor)
        manifest = json.loads((editor / "openrac" / "editor.json").read_text(encoding="utf-8"))
        if manifest.get("version") != version:
            problems.append(f"the manifest says version {manifest.get('version')}, not {version}")
        program = editor.joinpath(*manifest["program"][SYSTEM].split("/"))
        if not program.is_file():
            return problems + [f"no program at {manifest['program'][SYSTEM]}"]
        if os.name == "posix" and not os.access(program, os.X_OK):
            problems.append(f"{program.name} cannot run (no execute bit)")
        if SYSTEM == "macos":
            bundle = program.parents[2]
            info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
            if info.get("CFBundleExecutable") != program.name:
                problems.append(f"Info.plist starts {info.get('CFBundleExecutable')}, the program is {program.name}")
        for rel in ("openrac/tools/openrac_editor.py", "openrac/tools/openrac_root.py", "openrac/project"):
            if not (editor / rel).exists():
                problems.append(f"no {rel}")
        for source in sorted((editor / "openrac" / "tools").glob("*.py")):
            try:
                py_compile.compile(str(source), cfile=str(Path(tmp) / "c.pyc"), doraise=True)
            except py_compile.PyCompileError as error:
                problems.append(f"{source.name} does not compile on Python {sys.version.split()[0]}: {error.msg}")
        project = home / "projects" / "level 00 ąę"
        project.mkdir(parents=True)
        (project / "project.godot").write_text("config_version=5\n", encoding="utf-8", newline="\n")
        (project / "smoke.gd").write_text(SCRIPT, encoding="utf-8", newline="\n")
        try:
            done = subprocess.run([str(program), "--headless", "--path", str(project), "--script", "res://smoke.gd"],
                                  capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return problems + ["the editor did not quit within 5 minutes"]
        except OSError as error:
            return problems + [f"the program does not start: {error.strerror}"]
        said = (project / "smoke.txt").read_text(encoding="utf-8") if (project / "smoke.txt").is_file() else ""
        if not said.startswith("ok "):
            output = (done.stdout + done.stderr).decode(errors="replace")[-2000:]
            problems.append(f"the editor did not run its script (exit {done.returncode}):\n{output}")
        else:
            print(f"{archive.name}: {said}")
    return problems


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    # A Windows console's code page cannot print every letter of the editor's output.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    problems = check(Path(sys.argv[1]), sys.argv[2])
    for problem in problems:
        print(f"{sys.argv[1]}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
