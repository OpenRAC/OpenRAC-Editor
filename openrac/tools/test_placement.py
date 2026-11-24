"""placement: things set down on made-up ground (a flat field, a slope, a wall, water)."""

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

import placement


def write_glb(path: Path, tris, animated: bool = False) -> None:
    """A glb of triangles (N, 3, 3): positions and indices only."""
    P = np.asarray(tris, np.float32).reshape(-1, 3)
    idx = np.arange(len(P), dtype=np.uint32)
    blob = P.tobytes() + idx.tobytes()
    j = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
         "buffers": [{"byteLength": len(blob)}],
         "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(P) * 12},
                         {"buffer": 0, "byteOffset": len(P) * 12, "byteLength": len(idx) * 4}],
         "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(P), "type": "VEC3",
                        "min": P.min(0).tolist(), "max": P.max(0).tolist()},
                       {"bufferView": 1, "componentType": 5125, "count": len(idx), "type": "SCALAR"}]}
    if animated:
        j["animations"] = [{"channels": [], "samplers": []}]
    text = json.dumps(j).encode()
    text += b" " * (-len(text) % 4)
    blob += b"\0" * (-len(blob) % 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(text) + 8 + len(blob))
                     + struct.pack("<I", len(text)) + b"JSON" + text + struct.pack("<I", len(blob)) + b"BIN\0" + blob)


def box(lo, hi):
    """A closed box's triangles."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6),
             (3, 0, 4), (3, 4, 7)]
    return [[c[a], c[b], c[d]] for a, b, d in faces]


def quad(p0, p1, p2, p3):
    return [[p0, p1, p2], [p0, p2, p3]]


def ground():
    """Flat at z 0 for x < 10; a 45 degree slope up from x 10 to 14; flat again at z 4 beyond; a
    wall (a block 2 wide, 1.5 tall) at x 20..22."""
    tris = quad((-20, -20, 0), (10, -20, 0), (10, 20, 0), (-20, 20, 0))
    tris += quad((10, -20, 0), (14, -20, 4), (14, 20, 4), (10, 20, 0))
    tris += quad((14, -20, 4), (40, -20, 4), (40, 20, 4), (14, 20, 4))
    tris += box((20, -20, 4), (22, 20, 5.5))
    return tris


def transform(x, y, z, yaw=0.0, basis=None):
    if basis is None:
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        basis = [c, -s, 0, s, c, 0, 0, 0, 1]
    return "Transform3D(" + ", ".join(f"{v:.6g}" for v in list(basis) + [x, y, z]) + ")"


class Placement(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.project = Path(self.dir.name)
        level = self.project / "levels" / "level_00"
        write_glb(level / "terrain.glb", ground())
        write_glb(level / "mobys" / "moby_500.glb", box((-12, -12, 0), (12, 12, 24)))          # a crate, 1 unit
        write_glb(level / "mobys" / "moby_13.glb", box((-6, -6, 0), (6, 6, 6)))               # a bolt
        write_glb(level / "mobys" / "moby_1246.glb", box((-12, -12, 0), (12, 12, 24)), animated=True)  # an enemy
        for c in (500, 13, 1246):
            (level / "mobys" / f"moby_{c}.tscn").write_text(
                f'[gd_scene format=3]\n\n[ext_resource type="PackedScene" path="res://levels/level_00/mobys/moby_{c}.glb" id="model"]\n\n'
                f'[node name="Moby_{c}" type="Node3D"]\n\n[node name="Model" parent="." instance=ExtResource("model")]\n'
                "transform = Transform3D(0.041666668, 0, 0, 0, 0.041666668, 0, 0, 0, 0.041666668, 0, 0, 0)\n")
        write_glb(level / "shrubs" / "shrub_1.glb", box((-0.5, -0.5, 0), (0.5, 0.5, 0.8)))      # a bush
        write_glb(level / "shrubs" / "shrub_2.glb", box((-1, -1, 0), (1, 1, 0.05)))            # rings in the sand
        self.scene = level / "level_00.tscn"

    def tearDown(self):
        self.dir.cleanup()

    def write(self, things):
        """THINGS: (name, kind, class, transform text)."""
        lines = ['[gd_scene format=3]', '',
                 '[ext_resource type="PackedScene" path="res://levels/level_00/terrain.glb" id="terrain"]',
                 '[ext_resource type="PackedScene" path="res://levels/level_00/mobys/moby_500.tscn" id="moby_500"]',
                 '[ext_resource type="PackedScene" path="res://levels/level_00/mobys/moby_13.tscn" id="moby_13"]',
                 '[ext_resource type="PackedScene" path="res://levels/level_00/mobys/moby_1246.tscn" id="moby_1246"]',
                 '[ext_resource type="PackedScene" path="res://levels/level_00/shrubs/shrub_1.glb" id="shrub_1"]',
                 '[ext_resource type="PackedScene" path="res://levels/level_00/shrubs/shrub_2.glb" id="shrub_2"]', '',
                 '[node name="Level" type="Node3D"]', '', '[node name="Game" type="Node3D" parent="."]', '',
                 '[node name="Terrain" parent="Game" instance=ExtResource("terrain")]', '']
        for name, kind, cls, xform in things:
            parent = {"moby": "Game/Mobys", "shrub": "Game/Shrubs"}[kind]
            lines += [f'[node name="{name}" parent="{parent}" instance=ExtResource("{kind}_{cls}")]', f"transform = {xform}",
                      "metadata/rc1_index = 0", ""]
        self.scene.write_text("\n".join(lines), encoding="utf-8")

    def placed(self, **kw):
        text, report = placement.place_scene(self.project, self.scene.read_text(), **kw)
        self.scene.write_text(text, encoding="utf-8")
        return {r["node"]: r for r in report}

    def origin(self, name):
        text = self.scene.read_text()
        start = text.index(f'name="{name}"')
        line = text[start:].split("transform = Transform3D(", 1)[1].split(")", 1)[0]
        return placement.read_matrix(line)[1]

    def test_floating_crate_is_lowered(self):
        self.write([("Crate", "moby", 500, transform(0, 0, 0.5))])
        report = self.placed()
        self.assertIn("lowered", report["Crate"]["done"])
        self.assertAlmostEqual(self.origin("Crate")[2], 0.0, delta=0.02)

    def test_sunk_crate_is_raised(self):
        self.write([("Crate", "moby", 500, transform(0, 0, -0.8))])
        self.placed()
        self.assertAlmostEqual(self.origin("Crate")[2], 0.0, delta=0.02)

    def test_crate_on_slope_moves_to_level_ground_only_when_asked(self):
        self.write([("Crate", "moby", 500, transform(12, 0, 2.5))])
        report = self.placed()
        self.assertIn("not level", report["Crate"]["problem"])
        self.assertEqual(list(self.origin("Crate")), [12, 0, 2.5])
        report = self.placed(move=True)
        self.assertIn("moved", report["Crate"]["done"])
        x, _, z = self.origin("Crate")
        self.assertTrue(x <= 9.6 and abs(z) < 0.02 or x >= 14.4 and abs(z - 4) < 0.02, (x, z))

    def test_crate_in_a_wall_is_moved_out(self):
        self.write([("Crate", "moby", 500, transform(20.2, 0, 4))])  # half in the wall
        report = self.placed(move=True)
        self.assertIn("moved", report["Crate"]["done"])
        x, _, z = self.origin("Crate")
        self.assertTrue(x <= 19.6 or x >= 22.4 or abs(z - 5.5) < 0.02, (x, z))   # beside it, or on top

    def test_crate_inside_a_block_is_moved_out(self):
        self.write([("Crate", "moby", 500, transform(21, 0, 4))])
        report = self.placed(move=True)
        self.assertIn("moved", report["Crate"]["done"])
        x, _, z = self.origin("Crate")
        self.assertTrue(x <= 19.6 or x >= 22.4 or abs(z - 5.5) < 0.02, (x, z))   # beside it, or on top

    def test_crate_on_a_crate(self):
        self.write([("Low", "moby", 500, transform(0, 0, 0.3)), ("High", "moby", 500, transform(0, 0, 1.6))])
        self.assertEqual(placement.main(self.project), 0)
        self.assertAlmostEqual(self.origin("Low")[2], 0.0, delta=0.02)
        self.assertAlmostEqual(self.origin("High")[2], 1.0, delta=0.03)

    def test_floating_plant_is_lowered_but_a_sunk_one_stays(self):
        self.write([("Up", "shrub", 1, transform(0, 0, 0.6)), ("Down", "shrub", 1, transform(3, 0, -0.3))])
        report = self.placed()
        self.assertAlmostEqual(self.origin("Up")[2], 0.0, delta=0.02)
        self.assertNotIn("Down", report)
        self.assertAlmostEqual(self.origin("Down")[2], -0.3, delta=1e-6)

    def test_ground_cover_lies_on_the_slope(self):
        # Flat on the 45 degree slope (ground z = x - 10): one edge would float a whole unit.
        self.write([("Rings", "shrub", 2, transform(12, 0, 2.0))])
        report = self.placed()
        self.assertIn("turned", report["Rings"]["done"])
        text = self.scene.read_text()
        line = text[text.index('name="Rings"'):].split("transform = Transform3D(", 1)[1].split(")", 1)[0]
        basis, origin = placement.read_matrix(line)
        shape = placement.Shape(np.asarray(box((-1, -1, 0), (1, 1, 0.05)), float).reshape(-1, 3, 3), False)
        feet = shape.pose(basis).feet + origin
        gaps = feet[:, 2] - (feet[:, 0] - 10)
        self.assertLess(gaps.max(), 0.15, gaps)

    def test_turned_plant_stands_on_its_turned_underside(self):
        # On its side (turned 90 degrees about x): its underside is now its old side, 0.5 below.
        self.write([("Side", "shrub", 1, transform(0, 0, 1.0, basis=[1, 0, 0, 0, 0, -1, 0, 1, 0]))])
        self.placed()
        self.assertAlmostEqual(self.origin("Side")[2], 0.5, delta=0.02)

    def test_bolts_actors_and_far_things_stay(self):
        self.write([("Bolt", "moby", 13, transform(0, 0, 0.6)), ("Enemy", "moby", 1246, transform(2, 0, 0.6)),
                    ("Bird", "moby", 500, transform(4, 0, 9.0))])
        self.assertEqual(self.placed(), {})

    def test_things_afloat_stay(self):
        # Water at 0.5 over the flat ground: a plant on it is afloat, not floating.
        self.write([("Lily", "shrub", 1, transform(0, 0, 0.5))])
        self.assertEqual(self.placed(water=0.5), {})
        self.assertIn("Lily", self.placed())

    def test_check_changes_nothing(self):
        self.write([("Crate", "moby", 500, transform(0, 0, 0.5))])
        before = self.scene.read_text()
        self.assertEqual(placement.main(self.project, check=True), 1)
        self.assertEqual(self.scene.read_text(), before)


if __name__ == "__main__":
    unittest.main()
