"""The level editor's writers beside the extractor's modules: class scenes, projects, the project
format, and the helpers the packer keeps its own copy of."""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import moby_collision_scene
import pack
import sky
from godot import write_marker, write_project
from mobys import moby_class_names


class MobySceneTests(unittest.TestCase):
    def test_class_scene_with_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "moby_11.tscn"
            write_marker(path, 11, "11 vendor", ("res://levels/level_03/mobys/moby_11.glb", 0.5, 1.25))
            text = path.read_text()
        self.assertIn('[ext_resource type="PackedScene" path="res://levels/level_03/mobys/moby_11.glb" id="model"]',
                      text)
        self.assertIn('[node name="Model" parent="." instance=ExtResource("model")]\n'
                      "transform = Transform3D(0.5, 0, 0, 0, 0.5, 0, 0, 0, 0.5, 0, 0, 0)", text)
        self.assertIn("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1.65)", text)
        self.assertNotIn("Marker", text)


    def test_class_names_and_marker(self):
        self.assertEqual(moby_class_names()[11], "vendor")
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mobys" / "moby_11.tscn"
            write_marker(path, 11, "11 vendor")
            text = path.read_text()
        self.assertIn('text = "11 vendor"', text)
        self.assertIn("metadata/rc1_class = 11", text)


    def test_a_later_game_s_project_says_so(self):
        with tempfile.TemporaryDirectory() as temp:
            write_project(Path(temp) / "one", [30], "NTSC-U", "rac2")
            write_project(Path(temp) / "all", [1, 2], "NTSC-U", "rac4")
            self.assertIn('config/name="Ratchet & Clank: Going Commando: Insomniac Museum (USA, early access)"',
                          (Path(temp) / "one/project.godot").read_text())
            self.assertIn('config/name="Ratchet: Deadlocked levels (USA, early access)"',
                          (Path(temp) / "all/project.godot").read_text())


    def test_a_project_names_its_disc_by_market(self):
        with tempfile.TemporaryDirectory() as temp:
            for region, market in [("PAL", "Europe"), ("NTSC-U", "USA"), ("NTSC-J", "Japan"), ("NTSC-K", "Korea"),
                                   ("Other", "Other")]:
                write_project(Path(temp) / region, [3], region)
                self.assertIn(f'config/name="Ratchet & Clank: Kerwan ({market})"',
                              (Path(temp) / region / "project.godot").read_text())
            self.assertNotIn("NTSC", (Path(temp) / "NTSC-U/project.godot").read_text().split("[rendering]")[0])


    def test_keeps_minus_zero_unlike_packs(self):
        self.assertEqual(pack.f32(-0.0), struct.pack("<f", 0.0))


class ProjectFormat(unittest.TestCase):
    def test_the_launcher_reads_what_the_editor_writes(self):
        import json
        import disc
        import godot
        stated = json.loads((godot.PROJECT_FORMAT_FILE).read_text())
        self.assertEqual(godot.SCHEMA, stated["schema"])
        self.assertEqual(stated["gameplay"], {"rac1/pal": disc.PAL.gameplay})



class UnitTests(unittest.TestCase):
    """moby_collision_scene.unit and sky.unit stay two: one for both would have to give
    the same bits for every vector, and on Python 3.12 and newer sum() (compensated) and a plain
    x*x + y*y + z*z differ in the last bit for some."""

    SAMPLES = ((1.0, 0.0, 0.0), (0.0, -2.0, 0.0), (3.0, 4.0, 12.0), (1e-3, 2e5, -7.5), (0.1, 0.3, 0.1))

    def test_both_scale_to_length_one(self):
        for v in self.SAMPLES:
            for unit in (moby_collision_scene.unit, sky.unit):
                with self.subTest(v=v, unit=unit.__module__):
                    self.assertAlmostEqual(math.sqrt(sum(c * c for c in unit(v))), 1.0, places=15)
        self.assertEqual(sky.unit((3.0, 4.0, 12.0)), (3 / 13, 4 / 13, 12 / 13))

    def test_they_differ_in_the_last_bit_since_python_3_12(self):
        mine, sky_s = moby_collision_scene.unit((0.1, 0.3, 0.1)), sky.unit((0.1, 0.3, 0.1))
        if sys.version_info >= (3, 12):
            self.assertNotEqual(mine, sky_s)
            self.assertEqual([abs(a - b) <= math.ulp(b) for a, b in zip(mine, sky_s)], [True] * 3)
        else:
            self.assertEqual(mine, sky_s)



if __name__ == "__main__":
    unittest.main()
