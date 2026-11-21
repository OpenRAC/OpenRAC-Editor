"""The Assets dock end to end, in a real editor: test_editor_e2e.gd clicks, types and drags as a
person would in a headless editor on hand-built level projects (no disc), and checks what each
action did: search, filters, planets, the tree, each way of adding an asset, drags into the 3D
view and the Scene dock, undo and redo, old drops stood up, the foreign class switch, a scene
that is not a level, the dock's layout when narrow, and the plugin switched off and on. One
editor run does it all, in seconds:

    OPENRAC_GODOT=/path/to/godot python3 -m unittest test_editor_e2e -v

With OPENRAC_E2E_DRAWN=1 (and xvfb-run) it also draws the dock under a virtual display, and saves
the pictures to the folder OPENRAC_E2E_SHOTS names.

The editor's own errors fail it too: any ERROR line in its output that is not expected.
"""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_library_dock import level_project

GODOT = os.environ.get("OPENRAC_GODOT")
HERE = Path(__file__).parent

# The level as the extractor writes it: Game turns the game's z-up to Godot's y-up, an extracted
# moby carries rc1_index, and StaticAssets holds one model an older editor dropped lying down
# (its up on the game's y) and one standing.
LEVEL = """[gd_scene format=3]

[ext_resource type="PackedScene" path="res://levels/level_05/mobys/moby_11.tscn" id="moby_11"]

[node name="Level" type="Node3D"]
metadata/rc1_level = 5

[node name="Game" type="Node3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 0, 1, 0, -1, 0, 0, 0, 0)

[node name="Mobys" type="Node3D" parent="Game"]

[node name="Vendor_0000" parent="Game/Mobys" instance=ExtResource("moby_11")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 2, 0)
metadata/rc1_index = 0

[node name="StaticAssets" type="Node3D" parent="Game"]

[node name="OldDrop" type="Node3D" parent="Game/StaticAssets"]
transform = Transform3D(2, 0, 0, 0, 0, -2, 0, 2, 0, 5, 6, 7)
metadata/rc1_asset = "rac1/ntsc/level_03/mobys/75"

[node name="Standing" type="Node3D" parent="Game/StaticAssets"]
transform = Transform3D(0.87758255, -0.47942555, 0, 0.47942555, 0.87758255, 0, 0, 0, 1, 1, 2, 3)
metadata/rc1_asset = "rac1/ntsc/level_03/mobys/75"
"""

# Errors the editor prints that are no fault of the dock: the fork's leaks at exit, and the
# broken assets' own (read on purpose).
EXPECTED_ERRORS: list[str] = ["were leaked at exit", "not a model"]

# Levels of another game that only make the library bigger, and their mobys each.
STRESS_LEVELS = 12
STRESS_MOBYS = 40

# How many scenarios test_editor_e2e.gd runs.
SCENARIOS = 34

XVFB_RUN = shutil.which("xvfb-run")


def projects(root: Path) -> Path:
    """Four levels of two games and two discs; the first is the one edited."""
    here = level_project(root, "rac1", "ntsc", 5, "Ratchet & Clank: Rilgar (USA)", {11: ("vendor", 0.25)}, [1000])
    level_project(root, "rac1", "ntsc", 3, "Ratchet & Clank: Kerwan (USA)",
                  {11: ("vendor", 0.25), 75: ("red_ship", 0.5), 1430: ("codebot_slot", 1.0)}, [682, 1000])
    level_project(root, "rac1", "pal", 3, "Ratchet & Clank: Kerwan (Europe)", {1201: ("turret", 0.5)}, [])
    level_project(root, "rac3", "ntsc", 1, "Ratchet & Clank: Up Your Arsenal: Veldin (USA)",
                  {10: ("something", 1.0)}, [])
    # Broken assets: a model whose texture is gone, and a file that is no model.
    broken = level_project(root, "rac1", "ntsc", 6, "Ratchet & Clank: Blarg Station (USA)",
                           {29: ("turret", 1.0), 30: ("gun", 1.0)}, [])
    (broken / "levels" / "level_06" / "textures" / "t.png").unlink()
    (broken / "levels" / "level_06" / "mobys" / "moby_30.glb").write_bytes(b"not a model")
    # Many levels of another game, for the list's speed.
    for number in range(STRESS_LEVELS):
        level_project(root, "rac4", "ntsc", number, f"Ratchet: Deadlocked: Arena {number} (USA)",
                      {100 + i: (f"thing_{i}", 1.0) for i in range(STRESS_MOBYS)}, [])
    (here / "levels" / "level_05" / "level_05.tscn").write_text(LEVEL)

    # The test's own plugin, enabled after the dock's.
    addon = here / "addons" / "openrac_e2e"
    addon.mkdir(parents=True)
    shutil.copy(HERE / "test_editor_e2e.gd", addon / "plugin.gd")
    (addon / "plugin.cfg").write_text('[plugin]\n\nname="OpenRAC e2e"\ndescription=""\nauthor=""\nversion="1"\n'
                                      'script="plugin.gd"\n')
    settings = here / "project.godot"
    text = settings.read_text(encoding="utf-8")
    # The extractor's texture defaults (godot.py), so a drawing editor does not import them again.
    text += ('\n[importer_defaults]\n\ntexture={\n"compress/mode": 0,\n"detect_3d/compress_to": 0,\n'
             '"mipmaps/generate": true\n}\n')
    settings.write_text(re.sub(r'enabled=PackedStringArray\((.*)\)',
                               lambda m: f'enabled=PackedStringArray({m.group(1)}, "res://addons/openrac_e2e/plugin.cfg")', text))
    return here


def editor_settings(config: Path, scale: float) -> dict:
    """Editor settings with the display scale SCALE, in a config folder of their own (XDG_CONFIG_HOME),
    so the run does not read or change the person's settings: the environment for the run."""
    folder = config / "openrac_editor"
    folder.mkdir(parents=True)
    (folder / "editor_settings-4.7.tres").write_text(
        '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
        'interface/editor/appearance/display_scale = 7\n'
        f'interface/editor/appearance/custom_display_scale = {scale}\n')
    return dict(os.environ, XDG_CONFIG_HOME=str(config))


def run_editor(test: unittest.TestCase, scenarios: str = "", drawn: bool = False, scale: float | None = None) -> dict:
    """One editor run on fresh projects: the result file, after checking the log has no errors. SCALE
    sets the editor's display scale (Editor Settings) for the run."""
    with tempfile.TemporaryDirectory() as temp:
        here = projects(Path(temp) / "levels")
        env = editor_settings(Path(temp) / "config", scale) if scale is not None else None
        out = Path(temp) / "result.json"
        command = [GODOT, "--editor", "--resolution", "1280x900", "--path", str(here),
                   "res://levels/level_05/level_05.tscn", "--", str(out)] + ([scenarios] if scenarios else [])
        if drawn:
            # No sound card there either.
            command = [XVFB_RUN, "-a", "-s", "-screen 0 1280x960x24", GODOT, "--audio-driver", "Dummy"] + command[1:]
        else:
            command.insert(1, "--headless")
        began = time.monotonic()
        run = subprocess.run(command, capture_output=True, text=True, timeout=180, env=env)
        log = run.stdout + run.stderr
        test.assertTrue(out.is_file(), log[-4000:])
        result = json.loads(out.read_text(encoding="utf-8"))
        print(f"\n  editor run {time.monotonic() - began:.1f} s, scenarios {result['total_ms']} ms: "
              + ", ".join(f"{name} {ms}" for name, ms in result["times"].items()))

        for entry in result["checks"]:
            with test.subTest(scenario=entry["scenario"], check=entry["name"]):
                test.assertTrue(entry["ok"], entry["detail"])

        # Every scenario ran (one that stops on a script error leaves the ones after it out).
        test.assertEqual(result["stopped_in"], "", "a scenario stopped: see the ERROR lines")
        errors = [line for line in log.splitlines()
                  if re.search(r"\b(ERROR|SCRIPT ERROR)\b", line)
                  and not any(expected in line for expected in EXPECTED_ERRORS)]
        test.assertEqual(errors, [], "\n".join(errors[:20]))
        return result


@unittest.skipUnless(GODOT, "set OPENRAC_GODOT")
class AssetsDockEndToEnd(unittest.TestCase):
    def test_every_way_of_using_the_dock(self):
        result = run_editor(self)
        self.assertEqual(len(result["times"]), SCENARIOS, result["times"])

    # The same at 200%, as on a 4K screen: every click, drag and layout check still holds (the
    # editor's window grows with the scale, as a 4K screen has the room).
    def test_every_way_of_using_the_dock_at_200_percent(self):
        result = run_editor(self, scale=2.0)
        self.assertEqual(result["editor_scale"], 2.0)
        self.assertEqual(len(result["times"]), SCENARIOS, result["times"])

    # Every size the OpenRAC docks and Inspector sections set follows the editor's display scale
    # (Editor Settings): at 200% (a 4K screen) each is twice what it is at 100%, text included. A size
    # typed in pixels stays the same and shows tiny, as the Assets dock's colour legend once did.
    def test_every_size_follows_the_display_scale(self):
        runs = {scale: run_editor(self, "ui_sizes", scale=scale) for scale in (1.0, 2.0)}
        self.assertEqual([runs[scale]["editor_scale"] for scale in runs], [1.0, 2.0])
        small, large = runs[1.0]["sizes"], runs[2.0]["sizes"]
        self.assertEqual(sorted(small), ["assets", "behaviour", "collision", "cutscene", "foreign"])
        wrong = []
        for root, controls in small.items():
            self.assertEqual(sorted(controls), sorted(large[root]), f"{root}: other controls at 200%")
            for path, entry in controls.items():
                for key, value in entry.items():
                    # Rounded to whole pixels at each scale (a pixel either way), and a size made of
                    # the editor's own theme sizes may grow a little more than twice (they round up).
                    if not 2 * value - 2 <= large[root][path][key] <= 2.25 * value + 2:
                        wrong.append(f"{root}/{path} {key}: {value} at 100%, {large[root][path][key]} at 200%")
        self.assertEqual(wrong, [], "\n".join(wrong[:40]))

    # The dock as drawn, under a virtual display: each state has something on screen. Set
    # OPENRAC_E2E_SHOTS to a folder to keep the pictures.
    @unittest.skipUnless(XVFB_RUN and os.environ.get("OPENRAC_E2E_DRAWN"), "set OPENRAC_E2E_DRAWN, needs xvfb-run")
    def test_the_dock_as_drawn(self):
        result = run_editor(self, "looks,layout", drawn=True)
        self.assertEqual(len(result["times"]), 2, result["times"])
        self.assertTrue(any(entry["scenario"] == "looks" for entry in result["checks"]), "the editor did not draw")


if __name__ == "__main__":
    unittest.main()
