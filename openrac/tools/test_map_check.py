"""map_check, crack_fix and collision_fix on made-up ground: cracks found and closed, collision
laid out from a height field, a pack's report read."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

import collision_fix
import crack_fix
import map_check as mc
from test_placement import write_glb


def grid(x0, y0, size, n, height=lambda x, y: 0.0):
    """A square of N x N cells as triangles (two per cell)."""
    out = []
    s = size / n
    for i in range(n):
        for j in range(n):
            a = (x0 + j * s, y0 + i * s)
            b = (a[0] + s, a[1])
            c = (a[0], a[1] + s)
            d = (a[0] + s, a[1] + s)
            p = [(*q, height(*q)) for q in (a, b, c, d)]
            out += [[p[0], p[1], p[3]], [p[0], p[3], p[2]]]
    return np.array(out, np.float64)


def cracks(tris):
    verts, edges, _faces = mc.open_edges(np.asarray(tris, np.float64))
    _ids, dist = mc.crack_points(verts, edges)
    return int((dist < mc.T_JUNCTION).sum()), int(((dist >= mc.T_JUNCTION) & (dist < mc.GAP)).sum())


class Cracks(unittest.TestCase):
    def test_t_junction_and_gap_found(self):
        coarse = grid(0, 0, 4, 1)
        fine = grid(4, 0, 4, 2)                     # its middle vertex lies on the coarse edge
        self.assertEqual(cracks(np.concatenate([coarse, fine]))[0], 1)
        apart = grid(4.2, 0, 4, 2)                  # 0.2 beside the coarse one, a vertex across its middle
        self.assertGreater(cracks(np.concatenate([coarse, apart]))[1], 0)
        self.assertEqual(cracks(np.concatenate([coarse, grid(4, 0, 4, 1)])), (0, 0))

    def test_mend_closes_t_junction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "terrain.glb"
            write_glb(path, np.concatenate([grid(0, 0, 4, 1), grid(4, 0, 4, 2)]))
            done = crack_fix.mend(path)
            self.assertGreaterEqual(done["split"], 1)
            after = mc.node_meshes(path)[0][1]
            self.assertEqual(cracks(after), (0, 0))
            # The ground keeps its shape: the same area, at the same height.
            area = np.linalg.norm(np.cross(after[:, 1] - after[:, 0], after[:, 2] - after[:, 0]), axis=1).sum() / 2
            self.assertAlmostEqual(area, 32.0, places=4)

    def test_mend_closes_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "terrain.glb"
            right = grid(4.1, 0, 4, 2)
            write_glb(path, np.concatenate([grid(0, 0, 4, 1), right]))
            crack_fix.mend(path)
            self.assertEqual(cracks(mc.node_meshes(path)[0][1]), (0, 0))

    def test_mend_joins_close_vertices(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "terrain.glb"
            # Two squares 0.03 apart: no vertex across a middle, only corners next to corners.
            write_glb(path, np.concatenate([grid(0, 0, 4, 1), grid(4.03, 0, 4, 1)]))
            self.assertEqual(crack_fix.mend(path)["joined"], 4)
            _verts, edges, _faces = mc.open_edges(mc.node_meshes(path)[0][1])
            self.assertEqual(len(edges), 6)                  # one outline round both

    def test_t_junction_on_a_seam_mended(self):
        # A wall on the coarse edge makes it a seam (three faces): a vertex on it still counts.
        wall = np.array([[[4, 0, 0], [4, 4, 0], [4, 2, 3]], [[4, 4, 0], [4, 0, 0], [4, 2, -3]]], np.float64)

        def on_seam(tris):
            verts, edges, faces = mc.open_edges(tris)
            _ids, dist = mc.crack_points(verts, edges, targets=np.concatenate([edges, mc.seam_edges(faces)]))
            return int((dist < mc.T_JUNCTION).sum())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "terrain.glb"
            write_glb(path, np.concatenate([grid(0, 0, 4, 1), wall, grid(4, 0, 4, 2)]))
            self.assertGreaterEqual(on_seam(mc.node_meshes(path)[0][1]), 1)
            crack_fix.mend(path)
            self.assertEqual(on_seam(mc.node_meshes(path)[0][1]), 0)

    def test_hole_found(self):
        tris = grid(0, 0, 8, 8)
        centre = tris.mean(1)
        holed = tris[~((np.abs(centre[:, 0] - 4) < 1) & (np.abs(centre[:, 1] - 4) < 1))]
        verts, edges, faces = mc.open_edges(holed)
        self.assertEqual(len(mc.hole_rings(verts, edges, faces)), 1)
        verts, edges, faces = mc.open_edges(tris)
        self.assertEqual(mc.hole_rings(verts, edges, faces), [])


class Collision(unittest.TestCase):
    def test_flat_ground_takes_few_triangles(self):
        Z = np.zeros((65, 65))
        found = collision_fix.squares(Z)
        self.assertEqual(len(found), 4)
        self.assertEqual(len(collision_fix.triangles(0, 0, Z, found)), 8)

    def test_triangles_follow_the_ground(self):
        y, x = np.mgrid[0:65, 0:65].astype(float)
        Z = np.sin(x / 7) * 2 + np.where(x > 40, 5.0, 0.0)          # rolling ground and a cliff
        tris = collision_fix.triangles(0, 0, Z, collision_fix.squares(Z))
        self.assertLess(len(tris), 2 * 64 * 64)
        # Every sample is under a triangle within the tolerance (across the surface).
        import placement
        ground = placement.Ground(tris)
        P = np.c_[x.ravel(), y.ravel()][(x.ravel() < 64) & (y.ravel() < 64) & (np.abs(x.ravel() - 40.5) > 1)]
        z, _ = ground.floor(P + 0.001, np.full(len(P), 1e9), np.full(len(P), -1e9))
        want = np.sin(P[:, 0] / 7) * 2 + np.where(P[:, 0] > 40, 5.0, 0.0)
        self.assertLess(np.nanmax(np.abs(z - want)), collision_fix.TOLERANCE * 1.5 + 0.05)

    def test_holes_in_the_ground_stay_open(self):
        Z = np.zeros((33, 33))
        Z[10:20, 10:20] = np.nan
        tris = collision_fix.triangles(0, 0, Z, collision_fix.squares(Z))
        centre = tris.mean(1)
        self.assertFalse(((centre[:, 0] > 11) & (centre[:, 0] < 18) & (centre[:, 1] > 11) & (centre[:, 1] < 18)).any())


class Reports(unittest.TestCase):
    REPORT = ("level 11\n  tie OM_Tie_0001: added, after 12\n  shrub Shrub_0003: moved\n  Crate_7: moved\n"
              "  0012: deleted\n  not packed: Thing: why\nlevel 02\n  Moby_0001: added\n")

    def test_sections_and_names(self):
        sections = mc.level_sections(self.REPORT)
        self.assertEqual(sorted(sections), [2, 11])
        self.assertEqual(mc.edited_names(sections[11]), {"OM_Tie_0001", "Shrub_0003", "Crate_7"})

    def test_edited_from_scenes(self):
        head = '[ext_resource type="PackedScene" path="res://ties/tie_1.glb" id="t1"]\n\n'
        node = '[node name="{}" parent="Game/Ties" instance=ExtResource("t1")]\ntransform = {}\n\n'
        same = "Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 2, 3)"
        moved = "Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 2, 4)"
        before = head + node.format("A", same) + node.format("B", same)
        after = head + node.format("A", same) + node.format("B", moved) + node.format("C", same)
        self.assertEqual(mc.edited_from_scenes(before, after), {"B", "C"})

    def test_move_node(self):
        text = '[node name="A" parent="Game/Ties"]\ntransform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 2, 3)\n'
        out = mc.move_node(text, "A", np.array([0.0, 0.0, -1.0]))
        self.assertIn("1, 2, 2)", out)


if __name__ == "__main__":
    unittest.main()
