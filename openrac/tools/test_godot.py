"""The GLB writer, scene text and project files; synthetic meshes only."""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
from pathlib import Path
import random
import struct
import tempfile
import unittest

from glb_check import attribute, read_glb  # noqa: F401

from godot import ADDON_CFG, LIBRARY_ADDON_CFG, GAME_TO_GODOT, Raw, Scene, install_addon, number, transform, write_project
from mesh import Mesh


def triangle() -> Mesh:
    mesh = Mesh("tie_7", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 0), (1, 0), (0, 1)])
    mesh.add_face(("tie", 3), (0, 1, 2))
    return mesh


class SceneTests(unittest.TestCase):
    def test_numbers_round_trip_as_float32(self):
        self.assertEqual([number(x) for x in (0.0, -0.0, 1.0, 0.1, 312.456787)], ["0", "0", "1", "0.1", "312.4568"])
        rng = random.Random(1)
        for _ in range(2000):
            x = struct.unpack("<f", struct.pack("<f", rng.uniform(-1e4, 1e4)))[0]
            self.assertEqual(struct.pack("<f", float(number(x))), struct.pack("<f", x))

    def test_transform_lists_basis_rows_then_origin(self):
        self.assertEqual(transform(GAME_TO_GODOT), "Transform3D(1, 0, 0, 0, 0, 1, 0, -1, 0, 0, 0, 0)")
        m = [1, 2, 3, 0, 4, 5, 6, 0, 7, 8, 9, 0, 10, 20, 30, 1]
        self.assertEqual(transform(m), "Transform3D(1, 4, 7, 2, 5, 8, 3, 6, 9, 10, 20, 30)")

    def test_scene_text(self):
        scene = Scene()
        glb = scene.resource("PackedScene", "res://a.glb", "a")
        colour = scene.subresource("Environment", "environment", background_mode=2,
                                   ambient_light_color=Raw("Color(1, 1, 1, 1)"))
        scene.node("Root", kind="Node3D")
        scene.node("World", ".", "WorldEnvironment", environment=colour)
        scene.node("A", ".", instance=glb, transform=transform(GAME_TO_GODOT),
                   visible=False, metadata__rc1_colour=[1, 2, 3], metadata__rc1_draw_distance=2.5)
        scene.editable.append("A")
        self.assertEqual(scene.text(), """[gd_scene format=3]

[ext_resource type="PackedScene" path="res://a.glb" id="a"]

[sub_resource type="Environment" id="environment"]
background_mode = 2
ambient_light_color = Color(1, 1, 1, 1)

[node name="Root" type="Node3D"]

[node name="World" type="WorldEnvironment" parent="."]
environment = SubResource("environment")

[node name="A" parent="." instance=ExtResource("a")]
transform = Transform3D(1, 0, 0, 0, 0, 1, 0, -1, 0, 0, 0, 0)
visible = false
metadata/rc1_colour = [1, 2, 3]
metadata/rc1_draw_distance = 2.5

[editable path="A"]
""")

    def test_project_files(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            write_project(project, [3, 5])
            text = (project / "project.godot").read_text(encoding="utf-8")
            self.assertIn('run/main_scene="res://levels/level_03/level_03.tscn"', text)
            self.assertIn('"meshes/generate_lods": false', text)
            self.assertTrue((project / "rc1" / "level.gd").is_file())
            self.assertTrue((project / "rc1" / "check.gd").is_file())
            # The Collision dock, enabled.
            self.assertTrue((project / "addons" / "openrac_collision" / "plugin.cfg").is_file())
            self.assertTrue((project / "addons" / "openrac_library" / "plugin.cfg").is_file())
            self.assertIn(f'[editor_plugins]\n\nenabled=PackedStringArray("{ADDON_CFG}", "{LIBRARY_ADDON_CFG}")', text)

    def test_addon_into_an_older_project(self):
        """An export made before the dock gets it on update; plugins of the modder's stay."""
        with tempfile.TemporaryDirectory() as temp:
            plain, other = Path(temp) / "plain", Path(temp) / "other"
            for project in (plain, other):
                project.mkdir()
            (plain / "project.godot").write_text("config_version=5\n\n[application]\n\nconfig/name=\"x\"\n")
            (other / "project.godot").write_text(
                'config_version=5\n\n[editor_plugins]\n\nenabled=PackedStringArray("res://addons/mine/plugin.cfg")\n')
            for project in (plain, other):
                install_addon(project)
                install_addon(project)          # twice: still once
                text = (project / "project.godot").read_text(encoding="utf-8")
                self.assertEqual(text.count(ADDON_CFG), 1)
                self.assertEqual(text.count(LIBRARY_ADDON_CFG), 1)
                self.assertEqual(text.count("[editor_plugins]"), 1)
                self.assertTrue((project / "addons" / "openrac_collision" / "plugin.gd").is_file())
            self.assertIn(f'enabled=PackedStringArray("{ADDON_CFG}", "{LIBRARY_ADDON_CFG}", "res://addons/mine/plugin.cfg")',
                          (other / "project.godot").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
