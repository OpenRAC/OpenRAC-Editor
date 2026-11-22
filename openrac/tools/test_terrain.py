# SPDX-License-Identifier: MIT
# Copyright (c) 2026 the OpenRAC contributors
"""Tests for terrain.py: a small glb terrain is built, painted, mapped, sculpted and lit."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

import terrain


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


if __name__ == "__main__":
    unittest.main()
