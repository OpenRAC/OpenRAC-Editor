"""The editor with a gamepad, end to end: test_editor_gamepad.gd presses a pretend pad's buttons and
sticks in a headless editor on a hand-built level (no disc) and checks what each did: focus, the
d-pad and its repeat, the next panel, tabs, the menu, the right-click menu, text fields, flying the
3D view, turning and carrying objects with undo, and the Steam Deck's two touchpads.

    OPENRAC_GODOT=/path/to/editor python3 -m unittest test_editor_gamepad -v
"""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_editor_e2e import LEVEL
from test_library_dock import level_project

GODOT = os.environ.get("OPENRAC_GODOT")
HERE = Path(__file__).parent

# How many scenarios test_editor_gamepad.gd runs.
SCENARIOS = 26

EXPECTED_ERRORS: list[str] = ["were leaked at exit"]


def project(root: Path) -> Path:
    here = level_project(root, "rac1", "ntsc", 5, "Ratchet & Clank: Rilgar (USA)", {11: ("vendor", 0.25)}, [1000])
    (here / "levels" / "level_05" / "level_05.tscn").write_text(LEVEL)
    addon = here / "addons" / "openrac_gamepad_test"
    addon.mkdir(parents=True)
    shutil.copy(HERE / "test_editor_gamepad.gd", addon / "plugin.gd")
    (addon / "plugin.cfg").write_text('[plugin]\n\nname="OpenRAC gamepad test"\ndescription=""\nauthor=""\n'
                                      'version="1"\nscript="plugin.gd"\n')
    settings = here / "project.godot"
    settings.write_text(re.sub(r'enabled=PackedStringArray\((.*)\)',
                               lambda m: f'enabled=PackedStringArray({m.group(1)}, '
                                         '"res://addons/openrac_gamepad_test/plugin.cfg")',
                               settings.read_text(encoding="utf-8")))
    return here


@unittest.skipUnless(GODOT, "set OPENRAC_GODOT")
class GamepadEndToEnd(unittest.TestCase):
    def test_every_part_with_a_gamepad(self):
        with tempfile.TemporaryDirectory() as temp:
            here = project(Path(temp) / "levels")
            out = Path(temp) / "result.json"
            run = subprocess.run([GODOT, "--headless", "--editor", "--resolution", "1280x900", "--path", str(here),
                                  "res://levels/level_05/level_05.tscn", "--", str(out)],
                                 capture_output=True, text=True, timeout=180)
            log = run.stdout + run.stderr
            self.assertTrue(out.is_file(), log[-4000:])
            result = json.loads(out.read_text(encoding="utf-8"))
            for entry in result["checks"]:
                with self.subTest(scenario=entry["scenario"], check=entry["name"]):
                    self.assertTrue(entry["ok"], entry["detail"])
            self.assertEqual(result["stopped_in"], "", "a scenario stopped: see the ERROR lines")
            self.assertEqual(len(result["times"]), SCENARIOS, result["times"])
            errors = [line for line in log.splitlines()
                      if re.search(r"\b(ERROR|SCRIPT ERROR)\b", line)
                      and not any(expected in line for expected in EXPECTED_ERRORS)]
            self.assertEqual(errors, [], "\n".join(errors[:20]))


if __name__ == "__main__":
    unittest.main()
