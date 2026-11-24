# SPDX-License-Identifier: MIT
# Copyright (c) 2026 the OpenRAC contributors
"""Tests for terrain_tools.py: a small glb terrain is built, painted, mapped, sculpted and lit."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import terrain_tools as terrain


def flat_terrain(path: Path, size=40, step=2.0):
    """A flat square of ground at z 0, one primitive, as the extractor writes terrain."""
    xs = np.arange(0, size + step / 2, step)
    X, Y = np.meshgrid(xs, xs, indexing="xy")
    P = np.stack([X.ravel(), Y.ravel(), np.zeros(X.size)], 1)
    n = len(xs)
    idx = np.arange(n * n).reshape(n, n)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    F = np.concatenate([np.stack([a, b, d], -1).reshape(-1, 3), np.stack([a, d, c], -1).reshape(-1, 3)])
    t = terrain.Terrain.__new__(terrain.Terrain)
    t.path = path
    t.json = {"asset": {"version": "2.0"}}
    t.materials, t.textures, t.images, t.samplers = [], [], [], []
    m = t.material_for("textures/terrain_0003.png")
    t.nodes = [["Ground", None, [terrain.Prim(m, {"POSITION": P, "NORMAL": np.tile([0, 0, 1.0], (len(P), 1)),
                                                  "TEXCOORD_0": P[:, :2] / 8, "COLOR_0": np.ones((len(P), 4))}, F)]]]
    t.save(path)


class TerrainTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "terrain.glb"
        flat_terrain(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def run_op(self, *args):
        terrain.main([args[0], str(self.path), *args[1:]])
        return terrain.Terrain(self.path)

    def test_round_trip_keeps_triangles(self):
        t = terrain.Terrain(self.path)
        self.assertEqual(sum(len(p.faces) for _, _, p in t.prims()), 800)

    def test_paint_splits_by_picture(self):
        t = self.run_op("paint", "--texture", "textures/terrain_0017.png", "--at", "10", "10", "0", "--radius", "6")
        uris = {t.texture_uri(p.material): len(p.faces) for _, _, p in t.prims()}
        self.assertIn("textures/terrain_0017.png", uris)
        self.assertEqual(sum(uris.values()), 800)

    def test_sculpt_raises_in_the_brush_only(self):
        t = self.run_op("sculpt", "--mode", "raise", "--at", "20", "20", "0", "--radius", "8", "--strength", "3")
        P = np.concatenate([p.pos for _, _, p in t.prims()])
        self.assertAlmostEqual(P[:, 2].max(), 3.0, delta=0.3)
        far = np.hypot(P[:, 0] - 20, P[:, 1] - 20) > 8
        self.assertTrue(np.allclose(P[far, 2], 0))

    def test_a_dragged_stroke_is_one_change(self):
        t = self.run_op("sculpt", "--mode", "raise", "--at", "8", "20", "0", "--then", "32", "20", "0",
                        "--radius", "5", "--strength", "2")
        P = np.concatenate([p.pos for _, _, p in t.prims()])
        for x in (8, 32):
            near = np.hypot(P[:, 0] - x, P[:, 1] - 20) < 1
            self.assertGreater(P[near, 2].max(), 1.5)
        t = self.run_op("undo")
        P = np.concatenate([p.pos for _, _, p in t.prims()])
        self.assertTrue(np.allclose(P[:, 2], 0))

    def test_undo_takes_the_change_back(self):
        self.run_op("sculpt", "--mode", "raise", "--at", "20", "20", "0", "--radius", "8")
        t = self.run_op("undo")
        P = np.concatenate([p.pos for _, _, p in t.prims()])
        self.assertTrue(np.allclose(P[:, 2], 0))

    def test_uv_box_projection_tile(self):
        t = self.run_op("uv", "--tile", "4")
        p = next(p for _, _, p in t.prims())
        self.assertTrue(np.allclose(p.attrs["TEXCOORD_0"][:, 0], p.pos[:, 0] / 4))

    def test_rocks_make_overhangs_and_shadows(self):
        recipe = {"sea": 0.0, "rocks": [{"x": 20, "y": 20, "z0": -4, "z1": 20, "radius": 8, "undercut": 0.5,
                                          "bulge": 0.2, "seed": 1}]}
        (self.path.parent / terrain.RECIPE).write_text(json.dumps(recipe))
        t = self.run_op("build", "--step", "1.5")
        self.assertIn("Rocks", [n for n, _, _ in t.nodes])
        s = terrain.info(t)
        self.assertGreater(s["overhang"], 0.02)
        t = self.run_op("bake-light", "--ao", "8", "--cell", "0.75")
        s = terrain.info(t)
        self.assertGreater(s["in_shadow"], 0.05)
        self.assertGreater(s["light_spread"], 0.2)

    def test_subdivide_keeps_the_shape_and_the_glow(self):
        t = terrain.Terrain(self.path)
        p = next(t.prims())[2]
        p.keep = np.zeros(len(p.pos), bool)
        p.keep[0] = True
        area = lambda q: np.linalg.norm(np.cross(q.pos[q.faces[:, 1]] - q.pos[q.faces[:, 0]],
                                                 q.pos[q.faces[:, 2]] - q.pos[q.faces[:, 0]]), axis=1).sum() / 2
        before = area(p)
        terrain.subdivide(p, 0.6)
        self.assertGreater(len(p.faces), 800 * 9)
        self.assertAlmostEqual(area(p), before, places=3)
        self.assertEqual(len(p.keep), len(p.pos))
        self.assertTrue(p.keep.any())

    def test_model_baked_with_a_point_light_and_its_shadow(self):
        # A wall model over the ground: the ground behind it from the lamp stays dark, the lamp's
        # side is lit, and the lamp's own glowing face keeps its colour.
        model = Path(self.dir.name) / "wall.glb"
        flat_terrain(model, size=4, step=4.0)
        m = terrain.Terrain(model)
        p = next(m.prims())[2]
        P = p.pos.copy()
        p.attrs["POSITION"] = np.stack([P[:, 0] + 18, np.full(len(P), 20.0), P[:, 1]], 1)
        p.attrs["NORMAL"] = np.tile([0, -1.0, 0], (len(P), 1))
        p.attrs["COLOR_0"][:] = [1.5, 1.5, 1.5, 1]
        m.save()
        lights = Path(self.dir.name) / "lights.json"
        lights.write_text(json.dumps([{"at": [20, 17, 1.5], "colour": [2, 2, 2], "radius": 9}]))
        terrain.main(["bake-light", str(self.path), "--models", str(model), "--lights", str(lights), "--glow", "1.2",
                      "--sun", "0", "-89", "--ao", "4", "--cell", "0.5"])
        t, m = terrain.Terrain(self.path), terrain.Terrain(model)
        P = np.concatenate([q.pos for _, _, q in t.prims()]); C = np.concatenate([q.attrs["COLOR_0"] for _, _, q in t.prims()])
        lit = C[np.argmin(np.linalg.norm(P - [20, 16, 0], axis=1)), 0]
        hidden = C[np.argmin(np.linalg.norm(P - [20, 24, 0], axis=1)), 0]
        self.assertGreater(lit, hidden + 0.3)
        self.assertTrue(np.allclose(next(m.prims())[2].attrs["COLOR_0"][:, :3], 1.5))

    def test_bounced_light_reaches_the_shade_and_the_sun_is_soft(self):
        # A rock on the ground: what its shadow and its foot get back from the lit ground and rock
        # round them comes with --bounce; a wide sun leaves part-lit points at the shadow's edge.
        recipe = {"sea": 0.0, "rocks": [{"x": 20, "y": 20, "z0": -4, "z1": 14, "radius": 7, "undercut": 0.5,
                                          "bulge": 0.2, "seed": 2}]}
        (self.path.parent / terrain.RECIPE).write_text(json.dumps(recipe))
        self.run_op("build", "--step", "1.5")
        built = self.path.read_bytes()

        def light(*extra):
            self.path.write_bytes(built)
            t = self.run_op("bake-light", "--ao", "12", "--cell", "0.75", "--bounce-colour", "0", "0", "0", *extra)
            return np.concatenate([q.attrs["COLOR_0"][:, :3] for _, _, q in t.prims()]).sum(1)

        plain, bounced = light(), light("--bounce", "1")
        dark = plain < np.percentile(plain, 20)
        self.assertGreater(bounced[dark].mean(), plain[dark].mean() * 1.1)
        self.assertTrue(np.all(bounced >= plain - 1e-6))
        soft = light("--sun-samples", "8", "--sun-soft", "4")
        self.assertGreater(len(np.unique(np.round(soft, 3))), len(np.unique(np.round(plain, 3))))


if __name__ == "__main__":
    unittest.main()
