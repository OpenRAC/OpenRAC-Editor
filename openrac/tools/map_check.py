"""Checks a level for what a player would see wrong: holes and cracks in the ground, collision that
does not follow what is drawn, things floating, sunk or standing in each other.

Every check reads the level as the game will draw it (its terrain, its static models, its ties,
shrubs and mobys) and as the hero will meet it (Game/Collision, as the pack writes it). The disc's
own parts are left alone: its terrain pieces (Terrain_NNN), and with EDITED (what a pack found
changed) its own ties, shrubs and mobys.

- cracks: an edge of the map's own terrain with nothing on its other side. A vertex on the middle
  of another edge (a T-junction) or a little off it (a gap) shows as a line of sky through the
  ground; a small closed ring of such edges with nothing over it is a hole.
- collision: the walkable top of the map's own terrain (not under a tie or a model), sampled every STEP units near where the
  hero can go, against the collision's top there. No collision under it lets the hero fall
  through; collision over it makes him stand in the air; collision far under it sinks him.
- things: placement.py's check on the drawn ground only (never on collision nobody sees), and a
  tie or shrub floating far over it.
- overlaps: a tie or static model with another one's faces through its middle.

`openrac_editor.py check-map` runs it; Check, Play, Pack, Add as planet and map export refuse a
level with problems (--keep-problems packs it anyway). `--fix` first mends what can be mended
without a choice: cracks welded and small holes filled (crack_fix.py), collision made from the
drawn ground (collision_fix.py), ties and models moved out of each other, things set down.
numpy only (the launcher's Python has it)."""

from __future__ import annotations

import json
import math
import re
import struct
import sys
import zlib
from pathlib import Path

# The release brings numpy for the launcher's Python in vendor/ (build/package.py).
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

import numpy as np  # noqa: E402

import placement as pl  # noqa: E402

DISC_PIECE = re.compile(r"Terrain_\d+")
STEP = 1.0            # the collision check's sample spacing, in units
REACH = 16.0          # drawn ground this far from any collision is out of the hero's reach
TOLERANCE = 0.25      # collision may be this far over or under the drawn walkable ground
STAND = 1.0           # ground with a neighbour a STEP away higher or lower than this is too steep to stand on
RIM = 1.0             # collision this far to a side of the drawn ground still holds the hero
WINDOW = 3.0          # collision further than this under the drawn ground is none (a floor below)
WELD = 0.002          # vertices this close are one
T_JUNCTION = 0.01     # a vertex this close to another edge's middle splits it in the picture only
GAP = 0.5             # open edges this close to each other are a crack, not a boundary
HOLE = 64.0           # a closed ring of open edges shorter than this, inside a mesh, is a hole
TILE = 16.0           # problems in one square this wide are one place
MIN_AREA = 4.0        # collision mismatches smaller than this many square units are not reported
CORE = 0.5            # the middle share of a thing's width that nothing else may go through
COVER = 30.0          # a hole with another drawn mesh this far over it is not seen
FLY = 60.0            # a tie or shrub with ground this far under it floats (more: over the sea)
FAR = 1.0e4           # beyond this, a mesh is scenery out of reach (the horizon)


# --- the level as it is drawn ---

class Mesh:
    """Triangles (N, 3, 3) of one named node, as the game draws them."""

    def __init__(self, name: str, tris: np.ndarray, kind: str):
        self.name, self.tris, self.kind = name, np.asarray(tris, np.float64).reshape(-1, 3, 3), kind


def node_meshes(path: Path, world: np.ndarray | None = None) -> list[tuple[str, np.ndarray]]:
    """A glb's triangles per named node (its own name, as the editor shows it)."""
    data = Path(path).read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + length])
    out = []
    names = [n.get("name", f"node_{i}") for i, n in enumerate(doc.get("nodes", []))]
    whole = pl.glb_triangles(path)
    if len(names) <= 1 or "meshes" not in doc:
        out.append((names[0] if names else Path(path).stem, whole))
    else:
        # glb_triangles walks nodes in scene order; count each node's triangles the same way.
        counts = []
        sizes = {}

        def count(i):
            node = doc["nodes"][i]
            if "mesh" in node:
                n = 0
                for prim in doc["meshes"][node["mesh"]]["primitives"]:
                    if prim.get("mode", 4) != 4 or "POSITION" not in prim["attributes"]:
                        continue
                    acc = doc["accessors"][prim["indices"] if "indices" in prim else prim["attributes"]["POSITION"]]
                    n += acc["count"] // 3
                counts.append((names[i], n))
            for c in node.get("children", []):
                count(c)
        scene = doc.get("scenes", [{}])[doc.get("scene", 0)] if doc.get("scenes") else {"nodes": range(len(names))}
        for i in scene.get("nodes", []):
            count(i)
        at = 0
        for name, n in counts:
            sizes.setdefault(name, []).append(whole[at:at + n])
            at += n
        out = [(name, np.concatenate(parts)) for name, parts in sizes.items()]
    if world is not None:
        out = [(name, T @ world[:3, :3].T + world[:3, 3]) for name, T in out]
    return out


def scene_nodes(text: str):
    """(name, parent, resource path, basis, origin) of every instanced node of a level scene."""
    ids, out, current = {}, [], None
    for line in text.splitlines():
        m = pl.RESOURCE.match(line)
        if m:
            ids[m.group(2)] = m.group(1)
            continue
        m = pl.NODE.match(line)
        if m:
            if current is not None:
                out.append(tuple(current))
            name, parent, inst = m.groups()
            current = [name, parent, ids.get(inst, ""), np.eye(3), np.zeros(3)] if inst else None
            continue
        if current is not None and line.startswith("transform = "):
            current[3], current[4] = pl.read_matrix(pl.TRANSFORM.match(line).group(1))
    if current is not None:
        out.append(tuple(current))
    return out


class Level:
    """One level project's drawn meshes, collision and things."""

    def __init__(self, project: Path, level: int | None = None):
        self.project = Path(project)
        self.scene = pl.scene_path(self.project) if level is None else \
            self.project / "levels" / f"level_{level:02}" / f"level_{level:02}.tscn"
        self.dir = self.scene.parent
        self.text = self.scene.read_text(encoding="utf-8")
        self.nodes = scene_nodes(self.text)
        self.drawn: list[Mesh] = []       # terrain and static models: the ground
        self.things: list[Mesh] = []      # ties, shrubs, mobys
        self.cover = None                 # every drawn mesh as a pl.Ground, once asked for
        terrain = next((n for n in self.nodes if n[1] == "Game" and n[0] == "Terrain"), None)
        if terrain is not None and (self.project / terrain[2]).suffix == ".glb":
            for name, T in node_meshes(self.project / terrain[2]):
                T = T @ terrain[3].T + terrain[4]
                # The disc's own pieces keep their names (Terrain_NNN); a mesh made for the map
                # must not have one (the port culls it with the game's piece of that number).
                disc = DISC_PIECE.fullmatch(name) is not None
                self.drawn.append(Mesh(name, T, "disc terrain" if disc else "terrain"))
        self.made_terrain = any(m.kind == "terrain" for m in self.drawn)
        for path, source, matrix, _solid in self.static_placements():
            m = np.array(matrix, np.float64).reshape(4, 4).T
            T = pl.glb_triangles(source) @ m[:3, :3].T + m[:3, 3]
            self.drawn.append(Mesh(path.rsplit("/", 1)[-1], T, "static"))
        for name, parent, res, basis, origin in self.nodes:
            kind = pl.KINDS.get(parent)
            if kind is None or not res:
                continue
            T = pl.tris_of(self.project, (res, (basis, origin)))
            if len(T):
                self.things.append(Mesh(name, T, kind))

    def static_placements(self):
        import static_assets
        try:
            return static_assets.placements(self.project, self.text)
        except (ValueError, OSError):
            return []

    def collision(self) -> list[Mesh] | None:
        """The collision as the pack writes it, per node, in game units; None when it is the disc's
        own, untouched."""
        import collision_scene
        try:
            scene = collision_scene.scene_collision(self.project, self.dir, self.text)
        except Exception:  # noqa: BLE001 - the pack itself says what is wrong with it
            return None
        if scene is None:
            return None
        tris = np.array([q for q, _ in scene["triangles"]], np.float64).reshape(-1, 3, 3)
        tris[..., 0:2] /= 64.0
        tris[..., 2] /= 64.0
        # Which node each triangle came from, to name it: nearest owner by box.
        return [Mesh("Collision", tris, "collision")]


# --- problems ---

class Problem(dict):
    """{check, node, at: [x, y, z], size, text}: one place that is wrong."""

    def line(self) -> str:
        x, y, z = self["at"]
        return f"{self['node']}: {self['text']} at ({x:.0f}, {y:.0f}, {z:.0f})"


def clusters(P: np.ndarray, tile: float = TILE) -> list[np.ndarray]:
    """Indices of P grouped by the TILE-wide square they are in: one problem per place, never a
    whole seam across the level as one."""
    if len(P) == 0:
        return []
    keys = np.floor(P[:, :2] / tile).astype(np.int64)
    _, group = np.unique(keys, axis=0, return_inverse=True)
    group = group.reshape(-1)
    return [np.nonzero(group == g)[0] for g in range(group.max() + 1)]


# --- cracks ---

def open_edges(tris: np.ndarray, whole: bool = False):
    """The mesh welded (vertices within WELD), and its edges used by one face only:
    (vertices (V, 3), open edges (E, 2) as vertex indices, faces (F, 3)). WHOLE adds each corner's
    vertex (N * 3,) and which faces are kept (not collapsed by the weld)."""
    P = tris.reshape(-1, 3)
    keys = np.round(P / WELD).astype(np.int64)
    V, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    faces = inverse.reshape(-1, 3)
    good = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    faces = faces[good]
    verts = np.zeros((len(V), 3))
    np.add.at(verts, inverse, P)
    verts /= np.bincount(inverse, minlength=len(V))[:, None]
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    uniq, count = np.unique(edges, axis=0, return_counts=True)
    if whole:
        return verts, uniq[count == 1], faces, inverse, good
    return verts, uniq[count == 1], faces


def segment_buckets(A: np.ndarray, B: np.ndarray, cell: float) -> dict:
    lo = np.floor((np.minimum(A, B)[:, :2] - GAP) / cell).astype(np.int64)
    hi = np.floor((np.maximum(A, B)[:, :2] + GAP) / cell).astype(np.int64)
    out: dict = {}
    for e in range(len(A)):
        if (hi[e] - lo[e]).max() > 64:
            continue                       # an edge hundreds of units long: the horizon's
        for i in range(lo[e, 0], hi[e, 0] + 1):
            for j in range(lo[e, 1], hi[e, 1] + 1):
                out.setdefault((i, j), []).append(e)
    return out


def seam_edges(faces: np.ndarray) -> np.ndarray:
    """Edges three or more faces share (two pieces meeting a third along one line): an open edge
    may end on one of them as on an open edge."""
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    uniq, count = np.unique(edges, axis=0, return_counts=True)
    return uniq[count >= 3]


def crack_points(verts: np.ndarray, edges: np.ndarray, where: bool = False, targets: np.ndarray | None = None):
    """For each vertex on an open edge: its distance to the nearest other open edge's middle part
    (inf: none within GAP). Returns (vertex ids, distances); WHERE adds that edge (its index in
    TARGETS: EDGES and any more edges to look for, seam_edges) and how far along it the nearest
    point is (0 to 1)."""
    ids = np.unique(edges.reshape(-1))
    if targets is None:
        targets = edges
    edges = targets
    A, B = verts[edges[:, 0]], verts[edges[:, 1]]
    cell = 2.0
    table = segment_buckets(A, B, cell)
    P = verts[ids]
    best = np.full(len(ids), np.inf)
    edge = np.full(len(ids), -1)
    along = np.zeros(len(ids))
    keys = np.floor(P[:, :2] / cell).astype(np.int64)
    by_cell: dict = {}
    for i, k in enumerate(map(tuple, keys)):
        by_cell.setdefault(k, []).append(i)
    for k, members in by_cell.items():
        cand = table.get(k)
        if not cand:
            continue
        cand = np.array(cand)
        m = np.array(members)
        a, b = A[cand], B[cand]
        ab = b - a
        q = P[m]
        t = np.einsum("mcj,cj->mc", q[:, None, :] - a[None], ab) / np.maximum((ab * ab).sum(1), 1e-12)[None]
        inner = (t > 0.02) & (t < 0.98)
        near = a[None] + ab[None] * np.clip(t, 0, 1)[..., None]
        d = np.linalg.norm(q[:, None, :] - near, axis=2)
        own = (edges[cand][None, :, 0] == ids[m][:, None]) | (edges[cand][None, :, 1] == ids[m][:, None])
        d = np.where(inner & ~own, d, np.inf)
        k = d.argmin(1)
        dk = d[np.arange(len(m)), k]
        better = dk < best[m]
        best[m[better]] = dk[better]
        edge[m[better]] = cand[k[better]]
        along[m[better]] = t[np.arange(len(m)), k][better]
    if where:
        return ids, best, edge, along
    return ids, best


def hole_rings(verts: np.ndarray, edges: np.ndarray, faces: np.ndarray) -> list[np.ndarray]:
    """Closed rings of open edges shorter than HOLE that are not their piece's outline."""
    if len(edges) == 0:
        return []
    n = len(verts)
    parent = np.arange(n)

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    rings: dict = {}
    for e, (a, b) in enumerate(edges):
        rings.setdefault(find(a), []).append(e)
    # The piece (faces joined by vertices) each ring belongs to; its longest ring is its outline.
    piece = np.arange(n)

    def pfind(i):
        root = i
        while piece[root] != root:
            root = piece[root]
        while piece[i] != root:
            piece[i], i = root, piece[i]
        return root
    for f in faces:
        r0 = pfind(f[0])
        for v in f[1:]:
            rv = pfind(v)
            if rv != r0:
                piece[rv] = r0
    length = np.linalg.norm(verts[edges[:, 0]] - verts[edges[:, 1]], axis=1)
    longest: dict = {}
    info = []
    for root, es in rings.items():
        es = np.array(es)
        total = float(length[es].sum())
        owner = pfind(edges[es[0], 0])
        # A lone edge is no ring, and neither is a line of edges that stops (it runs along a
        # seam where pieces of the mesh meet: no sky shows through it).
        ends = np.bincount(edges[es].reshape(-1))
        if len(es) >= 3 and (ends[ends > 0] == 2).all():
            info.append((owner, total, es))
        longest[owner] = max(longest.get(owner, 0.0), total)
    return [es for owner, total, es in info if total < HOLE and total < longest[owner]]


def covered(level: Level, name: str, at: np.ndarray) -> bool:
    """Whether another drawn mesh is over AT (within COVER units): a hole there is not seen."""
    if level.cover is None:
        meshes = [m for m in level.drawn if np.abs(m.tris).max() < FAR]
        level.cover = pl.Ground(np.concatenate([m.tris for m in meshes]),
                                np.concatenate([np.full(len(m.tris), m.name, object) for m in meshes]))
    z, _ = level.cover.floor(at[None, :2], np.array([at[2] + COVER]), np.array([at[2] - 0.05]), name)
    return not np.isnan(z[0])


def check_cracks(level: Level, water: float | None) -> list[Problem]:
    problems = []
    for mesh in level.drawn:
        # The ground only: a model's open edges are its leaves and cards.
        if mesh.kind != "terrain" or len(mesh.tris) == 0:
            continue
        if np.abs(mesh.tris).max() > FAR:
            continue
        verts, edges, faces = open_edges(mesh.tris)
        if len(edges) == 0:
            continue
        ids, dist = crack_points(verts, edges, targets=np.concatenate([edges, seam_edges(faces)]))
        bad = dist < GAP
        found = []
        for i, d in zip(ids[bad], dist[bad]):
            found.append((verts[i], "T-junction" if d < T_JUNCTION else "gap"))
        cracked = set(ids[bad].tolist())
        for es in hole_rings(verts, edges, faces):
            vs = np.unique(edges[es].reshape(-1))
            if cracked.intersection(vs.tolist()):
                continue
            centre = verts[vs].mean(0)
            if covered(level, mesh.name, centre):
                continue                      # under a rock or a house: nobody sees it
            found.append((centre, "hole"))
        if water is not None:
            found = [f for f in found if f[0][2] > water + 0.1]
        if not found:
            continue
        P = np.array([f[0] for f in found])
        for group in clusters(P):
            kinds = {found[i][1] for i in group}
            what = "a hole" if kinds == {"hole"} else "a crack"
            problems.append(Problem(check="cracks", node=mesh.name, at=P[group].mean(0).round(1).tolist(),
                                    size=len(group), text=f"{what} in the drawn ground ({len(group)} "
                                    f"{'points' if len(group) > 1 else 'point'}: {', '.join(sorted(kinds))})"))
    return problems


# --- collision ---

def check_collision(level: Level, water: float | None) -> list[Problem]:
    if not level.made_terrain:
        return []
    found = level.collision()
    col = found[0].tris if found else np.zeros((0, 3, 3))
    drawn = [m for m in level.drawn if np.abs(m.tris).max() < FAR] if level.drawn else []
    if not drawn:
        return []
    # Ties are drawn too: where one is the top, the ground under it is not what the hero meets.
    drawn += [m for m in level.things if m.kind == "tie"]
    tris = np.concatenate([m.tris for m in drawn])
    owners = np.concatenate([np.full(len(m.tris), m.name, object) for m in drawn])
    seen = pl.Ground(tris, owners)
    if len(col) == 0:
        return []
    hit = pl.Ground(col)
    # Sample only near the collision: the hero walks there; scenery far out is not reached.
    lo = col.reshape(-1, 3).min(0)[:2] - REACH
    hi = col.reshape(-1, 3).max(0)[:2] + REACH
    xs = np.arange(lo[0], hi[0], STEP) + STEP / 2
    ys = np.arange(lo[1], hi[1], STEP) + STEP / 2
    X, Y = np.meshgrid(xs, ys)
    P = np.c_[X.ravel(), Y.ravel()]
    inside = reach(level, P, X.shape, STEP)
    grid = np.full(len(P), np.nan)
    grid[inside], _ = seen.floor(P[inside], np.full(inside.sum(), 1e9), np.full(inside.sum(), -1e9))
    # Ground the hero can stand on: its neighbours a STEP away are within STAND of it, so a ledge
    # on a cliff or a spire's tip is no ground.
    G = grid.reshape(X.shape)
    level_enough = np.isfinite(G)
    for axis in (0, 1):
        for shift in (1, -1):
            other = np.roll(G, shift, axis)
            with np.errstate(invalid="ignore"):
                level_enough &= np.abs(other - G) <= STAND
    ok = level_enough.ravel()
    if water is not None:
        ok &= grid > water + 0.3
    P, zd = P[ok], grid[ok]
    # Owners of the drawn floor at each point (for naming), the collision top near it.
    # The collision's floor nearest the drawn one, here or up to RIM to a side: the hero is a unit
    # wide, so a cliff's edge that far off is not felt.
    zc = np.full(len(P), np.nan)
    for d in ((0, 0), (0.5, 0), (-0.5, 0), (0, 0.5), (0, -0.5), (RIM, 0), (-RIM, 0), (0, RIM), (0, -RIM)):
        z, _ = hit.floor(P + np.array(d), zd + 2.0, zd - WINDOW)
        closer = np.isnan(zc) | (np.abs(z - zd) < np.abs(zc - zd))
        zc = np.where(closer & ~np.isnan(z), z, zc)
    zany, _ = hit.floor(P, np.full(len(P), 1e9), np.full(len(P), -1e9))
    missing = np.isnan(zc)
    above = ~missing & (zc > zd + TOLERANCE)
    below = ~missing & (zc < zd - TOLERANCE)
    # Higher collision over the drawn ground (an invisible block) counts too.
    lifted = ~np.isnan(zany) & (zany > zd + 2.0)
    # Only where the ground is the modder's own: the disc's terrain and collision are the game's.
    names = owner_names(seen, P, zd)
    made = {m.name for m in drawn if m.kind == "terrain"}
    mine = np.isin(names.astype(str), list(made))
    P, zd, zc, zany, names = P[mine], zd[mine], zc[mine], zany[mine], names[mine]
    missing, above, below, lifted = missing[mine], above[mine], below[mine], lifted[mine]
    problems = []
    for mask, text in ((missing, "no collision under the drawn ground: the hero falls through"),
                       (above | lifted, "collision over the drawn ground: the hero stands in the air"),
                       (below, "collision under the drawn ground: the hero sinks into it")):
        idx = np.nonzero(mask)[0]
        if len(idx) == 0:
            continue
        Q = np.c_[P[idx], zd[idx]]
        for group in clusters(Q):
            area = len(group) * STEP * STEP
            if area < MIN_AREA:
                continue
            g = idx[group]
            off = (zc[g] - zd[g]) if mask is not missing else None
            extra = f", {np.nanmedian(np.abs(np.where(lifted[g], zany[g] - zd[g], off))):.1f} units off" \
                if off is not None else ""
            name = most_common(names[g])
            problems.append(Problem(check="collision", node=name, at=Q[group].mean(0).round(1).tolist(),
                                    size=area, text=f"{text} ({area:.0f} square units{extra})"))
    return problems


def reach(level: Level, P: np.ndarray, shape: tuple, step: float) -> np.ndarray:
    """Which of the grid points P (SHAPE, STEP apart) the hero can reach: within REACH of the
    level's collision, or on the collision made from the drawn ground (collision_fix.py, itself
    made that far out: so a second check or fix sees the same place)."""
    import collision_fix
    nodes = collision_fix.old_collision(level)
    made = [t for name, t, _ in nodes if name == collision_fix.NAME]
    rest = [t for name, t, _ in nodes if name != collision_fix.NAME]
    out = np.zeros(len(P), bool)
    if rest:
        out |= dilate(coverage(np.concatenate(rest), P, step).reshape(shape), int(REACH / step)).ravel()
    if made:
        out |= coverage(np.concatenate(made), P, step)
    return out


def coverage(tris: np.ndarray, P: np.ndarray, step: float = STEP) -> np.ndarray:
    """Which points have a collision triangle's box over them (a cheap reach test)."""
    lo, hi = tris.min(1)[:, :2], tris.max(1)[:, :2]
    cells = set()
    for a, b in zip(np.floor(lo / step).astype(int), np.floor(hi / step).astype(int)):
        for i in range(a[0], b[0] + 1):
            for j in range(a[1], b[1] + 1):
                cells.add((i, j))
    keys = np.floor(P / step).astype(int)
    return np.array([(int(k[0]), int(k[1])) in cells for k in keys])


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    # Separable square growth: rows then columns, by cumulative sums.
    for axis in (0, 1):
        c = np.cumsum(np.pad(out.astype(np.int32), [(r + 1, r) if a == axis else (0, 0) for a in (0, 1)]), axis=axis)
        n = out.shape[axis]
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        out = (hi - lo) > 0
    return out


def owner_names(ground: pl.Ground, P: np.ndarray, z: np.ndarray) -> np.ndarray:
    names = np.full(len(P), "Terrain", object)
    keys = np.floor(P / pl.CELL).astype(int)
    for i, k in enumerate(map(tuple, keys)):
        cand = ground.floor_cells.get(k)
        if cand is None:
            continue
        T = ground.floors[cand]
        d = np.abs(T[:, :, 2].mean(1) - z[i]) + np.linalg.norm(T[:, :, :2].mean(1) - P[i], axis=1)
        names[i] = ground.floor_owner[cand][d.argmin()] or "Terrain"
    return names


def most_common(values) -> str:
    u, c = np.unique(np.asarray(values, str), return_counts=True)
    return str(u[c.argmax()])


# --- things ---

def check_things(level: Level, water: float | None, fix: bool = False, edited=None,
                 move: bool = False) -> tuple[list[Problem], str]:
    """placement.py on the drawn ground, for the things in EDITED (names; None: every thing).
    Returns (problems, the scene text, with them set down when FIX)."""
    if edited is not None and not edited:
        return [], level.text
    names = None if edited is None else [n.replace("[", "[[]") for n in edited]
    text, report = pl.place_scene(level.project, level.text, names=names, fix=fix, water=water, move=move,
                                  drawn_only=True)
    problems = []
    for r in report:
        if r["problem"]:
            text_ = r["problem"]
        elif not fix:
            text_ = f"{r['kind']} not on the ground: it would be {r['done']}"
        else:
            continue
        problems.append(Problem(check="things", node=r["node"], at=r["position"], size=1, text=text_))
    # placement.py leaves a thing far over the ground as it is (it may hang or fly on purpose); a
    # tie or shrub of a map does not fly.
    told = {p["node"] for p in problems}
    ground = None
    for m in level.things:
        if m.kind not in ("tie", "shrub") or m.name in told or (edited is not None and m.name not in edited):
            continue
        V = m.tris.reshape(-1, 3)
        lo, hi = V.min(0), V.max(0)
        if np.linalg.norm((hi - lo)[:2]) > pl.BIG:
            continue
        foot = V[V[:, 2] <= lo[2] + max(0.15 * (hi[2] - lo[2]), 0.02)]
        foot = foot[np.linspace(0, len(foot) - 1, min(len(foot), 32)).astype(int)]
        if ground is None:
            ground = pl.level_ground(level.project, text, drawn_only=True)
        # Ground a little over its foot counts: a rock set into a slope is sunk, not floating.
        g, _ = ground.floor(foot[:, :2], foot[:, 2] + 2.0, foot[:, 2] - FLY, m.name)
        if water is not None:
            g = np.where(g < water - 0.25, np.nan, g)      # the sea's floor is not seen
        if np.isnan(g).all():
            continue                                  # nothing under it at all: over the sea
        lift = float(np.nanmin(foot[:, 2] - g))
        if lift <= pl.LIFT or (water is not None and np.nanmax(g) < water):
            continue
        at = ((lo + hi) / 2).round(1).tolist()
        if fix:
            text = move_node(text, m.name, np.array([0.0, 0.0, -lift]))
        else:
            problems.append(Problem(check="things", node=m.name, at=at, size=1,
                                    text=f"{m.kind} floats {lift:.1f} units over the ground"))
    return problems, text


def move_node(text: str, name: str, by: np.ndarray) -> str:
    """TEXT with node NAME's transform moved BY."""
    head = f'[node name="{name}" '
    at = text.find(head)
    if at < 0:
        return text
    line = text.find("\ntransform = ", at)
    nxt = text.find("\n[", at + 1)
    if line < 0 or (0 <= nxt < line):
        return text
    end = text.find("\n", line + 1)
    basis, origin = pl.read_matrix(pl.TRANSFORM.match(text[line + 1:end]).group(1))
    return text[:line + 1] + "transform = " + pl.write_matrix(basis, origin + by) + text[end:]


def check_overlaps(level: Level, edited=None) -> list[Problem]:
    """A tie or static model with another one's faces through its middle, where one of
    the two is in EDITED (names; None: any) or a static model (always the modder's own)."""
    # Plants grow into each other and out of rocks; what must not is one solid thing inside another.
    solid = [m for m in level.things if m.kind == "tie"] + [m for m in level.drawn if m.kind == "static"]
    if len(solid) < 2:
        return []
    boxes = np.array([[*m.tris.reshape(-1, 3).min(0), *m.tris.reshape(-1, 3).max(0)] for m in solid])
    size = np.linalg.norm(boxes[:, 3:5] - boxes[:, 0:2], axis=1)
    keep = size < pl.BIG
    cell = 8.0
    table: dict = {}
    for i in np.nonzero(keep)[0]:
        for a in range(int(boxes[i, 0] // cell), int(boxes[i, 3] // cell) + 1):
            for b in range(int(boxes[i, 1] // cell), int(boxes[i, 4] // cell) + 1):
                table.setdefault((a, b), []).append(i)
    seen = set()
    problems = []
    for members in table.values():
        for i in members:
            for j in members:
                if i >= j or (i, j) in seen:
                    continue
                seen.add((i, j))
                if (boxes[i, 3:] < boxes[j, :3]).any() or (boxes[j, 3:] < boxes[i, :3]).any():
                    continue
                if edited is not None and not any(solid[k].name in edited or solid[k].kind == "static"
                                                  for k in (i, j)):
                    continue
                for a, b in ((i, j), (j, i)):
                    if through_middle(solid[a], boxes[a], solid[b]):
                        small, big = (a, b) if size[a] <= size[b] else (b, a)
                        centre = (boxes[small, :3] + boxes[small, 3:]) / 2
                        problems.append(Problem(check="overlaps", node=solid[small].name,
                                                at=centre.round(1).tolist(), size=1, other=solid[big].name,
                                                text=f"stands inside {solid[big].name}"))
                        break
    return problems


def through_middle(mesh: Mesh, box: np.ndarray, other: Mesh) -> bool:
    lo, hi = box[:3].copy(), box[3:].copy()
    h = hi[2] - lo[2]
    lo[2] += 0.2 * h                      # its foot may be in the ground with the other's
    centre = (lo + hi) / 2
    half = (hi - lo) / 2 * np.array([CORE, CORE, 0.8])
    T = other.tris
    near = ~((T.min(1) > centre + half).any(1) | (T.max(1) < centre - half).any(1))
    if not near.any():
        return False
    return bool(pl.tri_box(T[near] - centre, half).any())


# --- the report ---

def edited_names(report: str) -> set[str]:
    """The nodes a pack's changes.txt says were added or changed ("  tie Name: moved")."""
    out = set()
    for line in report.splitlines():
        words = line.strip().split(": ", 1)
        if len(words) == 2 and not words[0].startswith(("level ", "not packed")):
            name = words[0].split(" ")[-1]
            if name and not name.isdigit():
                out.add(name)
    return out


def edited_from_scenes(pristine: str, text: str) -> set[str]:
    """The ties, shrubs and mobys of TEXT that PRISTINE (the level as extracted) does not have
    where they are now: added, moved or given another model."""
    before = {(n[0], n[1]): (n[2], np.round(n[3], 4).tobytes(), np.round(n[4], 3).tobytes())
              for n in scene_nodes(pristine) if n[1] in pl.KINDS}
    return {n[0] for n in scene_nodes(text) if n[1] in pl.KINDS and before.get((n[0], n[1])) !=
            (n[2], np.round(n[3], 4).tobytes(), np.round(n[4], 3).tobytes())}


def water_of(project: Path) -> float | None:
    """The project's map/water setting (project.godot [openrac]): the sea's height, under which
    nothing shows; None when it has none."""
    import planet
    try:
        value = planet.settings(project).get("map/water")
        return float(value) if value not in (None, "") else None
    except (OSError, ValueError):
        return None


def level_sections(report: str) -> dict[int, str]:
    """A pack's changes.txt cut into its levels ("level NN" and the lines under it)."""
    out: dict[int, list[str]] = {}
    current = None
    for line in report.splitlines():
        if line.startswith("level ") and line[6:8].isdigit():
            current = out.setdefault(int(line[6:8]), [])
        elif current is not None and line.startswith("  "):
            current.append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def packed_problems(project: Path, report: str) -> list[str]:
    """The problems of each level a pack of PROJECT (one level's project, or its folder of them,
    or one project with every level) changed, as "<node>: <what> at (x, y, z)" lines."""
    lines = []
    for number, section in sorted(level_sections(report).items()):
        name = f"level_{number:02}"
        for root in (project / name, project):
            if (root / "levels" / name / f"{name}.tscn").is_file():
                break
        else:
            continue
        found = check_level(root, water_of(root) if (root / "project.godot").is_file() else None,
                            edited=edited_names(section), level=number)
        lines += [p.line() for ps in found["problems"].values() for p in ps]
    return lines


def separate(level: Level, overlaps: list[Problem], text: str, water: float | None = None) -> tuple[str, int]:
    """Each tie in OVERLAPS moved to the nearest spot (up to its own width away) where nothing
    goes through its middle and the drawn ground is under it, at that ground's height. Returns
    (the scene text, how many moved)."""
    # Ties, and static models placed straight under Game/StaticAssets (their own transform is
    # where they stand).
    direct = {n[0] for n in level.nodes if n[1] == "Game/StaticAssets"}
    ties = {m.name: m for m in level.things if m.kind == "tie"}
    ties.update({m.name: m for m in level.drawn if m.kind == "static" and m.name in direct})
    solid = [m for m in level.things if m.kind == "tie"] + [m for m in level.drawn if m.kind == "static"]
    ground = pl.level_ground(level.project, text, drawn_only=True)
    moved = 0

    def free_spot(mesh):
        """The nearest move (up to twice its width, else down into the ground) that frees MESH,
        or None."""
        V = mesh.tris.reshape(-1, 3)
        lo, hi = V.min(0), V.max(0)
        centre = (lo + hi) / 2
        width = float(np.linalg.norm((hi - lo)[:2]))
        here, _ = ground.floor(centre[None, :2], np.array([lo[2] + 2.0]), np.array([lo[2] - FLY]), mesh.name)
        if np.isnan(here[0]) or (water is not None and here[0] < water - 0.25):
            here = np.array([lo[2]])           # over the sea (on a cliff's edge): its own foot
        others = [m for m in solid if m is not mesh and
                  np.linalg.norm(m.tris.reshape(-1, 3).mean(0)[:2] - centre[:2]) < width * 3 + pl.BIG]
        for r in np.arange(0.5, max(4.0, 2 * width) + 0.01, 0.5):
            for k in range(int(8 + r * 4)):
                a = 2 * math.pi * k / int(8 + r * 4)
                d = np.array([math.cos(a) * r, math.sin(a) * r, 0.0])
                # The ground there may be higher or lower (a slope), up to half its width.
                rise = max(2.0, width / 2)
                g, _ = ground.floor((centre + d)[None, :2], np.array([here[0] + rise]), np.array([here[0] - rise]),
                                    mesh.name)
                if np.isnan(g[0]) or (water is not None and g[0] < water - 0.25):
                    continue
                d[2] = g[0] - here[0]
                box = np.concatenate([lo + d, hi + d])
                shifted = Mesh(mesh.name, mesh.tris + d, mesh.kind)
                if not any(through_middle(shifted, box, o) or
                           through_middle(o, np.concatenate([o.tris.reshape(-1, 3).min(0),
                                                             o.tris.reshape(-1, 3).max(0)]), shifted)
                           for o in others):
                    return d
        # No free spot near (a crowded slope): sunk into the ground, up to most of its height.
        for k in np.arange(0.1, 0.71, 0.1):
            d = np.array([0.0, 0.0, -k * (hi[2] - lo[2])])
            shifted = Mesh(mesh.name, mesh.tris + d, mesh.kind)
            box = np.concatenate([lo + d, hi + d])
            if not any(through_middle(shifted, box, o) or
                       through_middle(o, np.concatenate([o.tris.reshape(-1, 3).min(0),
                                                         o.tris.reshape(-1, 3).max(0)]), shifted)
                       for o in others):
                return d
        return None

    for problem in overlaps:
        # The one named first, else the one it stands inside.
        other = re.search(r"stands inside (\S+)", problem["text"])
        for name in (problem["node"], other.group(1) if other else None):
            mesh = ties.get(name)
            spot = None if mesh is None else free_spot(mesh)
            if spot is None:
                continue
            text = move_node(text, mesh.name, spot)
            mesh.tris = mesh.tris + spot
            moved += 1
            break
    return text, moved


def check_level(project: Path, water: float | None = None, fix: bool = False, edited=None,
                level: int | None = None) -> dict:
    """Every check on PROJECT (one level's project; LEVEL: that level of a project with every
    level). EDITED: the things a pack found changed (edited_names), so the disc's own placements
    are not judged; None judges every thing. FIX mends first what can be mended (the level's own
    terrain's cracks, ties inside each other, things off the drawn ground) and then checks."""
    number = level
    level = Level(project, number)
    fixed = []
    if fix:
        if level.made_terrain:
            import crack_fix
            done = crack_fix.mend(level.dir / "terrain.glb", water=water)
            if any(done.values()):
                fixed.append(f"cracks in the terrain closed ({done['split']} faces split, {done['moved']} "
                             f"vertices moved onto their edge, {done['filled']} holes filled, {done['joined']} vertices "
                             f"joined across a sliver)")
                level = Level(project, number)
        import collision_fix
        made = collision_fix.make(level, water) if check_collision(level, water) else None
        if made is not None:
            level.scene.write_text(made["text"], encoding="utf-8", newline="\n")
            gone = ", ".join(made["replaced"]) or "nothing"
            fixed.append(f"collision made from the drawn ground ({made['triangles']} triangles, in place of {gone})")
            level = Level(project, number)
        # Ties out of each other, then things set down; setting down may bring a tie back into
        # another, so a few passes.
        moved_total, carried_total, set_down = 0, 0, False
        for _pass in range(3):
            level = Level(project, number)
            text, moved = separate(level, check_overlaps(level, edited), level.text, water)
            moved_total += moved
            original = level.text
            for _round in range(6):               # crates on crates settle over a few rounds
                # A made thing (a crate) that cannot stand where it is goes to the nearest level spot.
                _, after = check_things(with_text(level, text), water, True, edited, move=True)
                if after == text:
                    break
                text = after
            level.text = original
            if text == level.text:
                break
            set_down = set_down or text != original
            # The collision made for a tie goes where the tie went (the scene on disk: where it stood).
            text, carried = collision_fix.follow(Level(project, number), text)
            carried_total += carried
            level.scene.write_text(text, encoding="utf-8", newline="\n")
            level = Level(project, number)
            if not moved:
                break
        if moved_total:
            fixed.append(f"{moved_total} tie(s) or model(s) moved out of another")
        if set_down:
            fixed.append("things set down on the drawn ground")
        if carried_total:
            fixed.append(f"{carried_total} collision triangles moved with their ties")
        text, trimmed = collision_fix.trim(level)
        if trimmed:
            level.scene.write_text(text, encoding="utf-8", newline="\n")
            fixed.append(f"{trimmed} collision floors over nothing drawn taken out")
            level = Level(project, number)
    found = {"cracks": check_cracks(level, water), "collision": check_collision(level, water),
             "overlaps": check_overlaps(level, edited)}
    found["things"], _ = check_things(level, water, False, edited)
    return {"problems": found, "fixed": fixed, "level": level}


def with_text(level: Level, text: str) -> Level:
    """LEVEL as its scene would be with TEXT (for placement, which reads the scene's text)."""
    level.text = text
    return level


def picture(level: Level, problems: dict, path: Path, size: int = 1024) -> None:
    """A top view of the level: drawn ground grey, problems as coloured dots (red collision,
    orange cracks, yellow things, magenta overlaps)."""
    tris = [m.tris for m in level.drawn if len(m.tris) and np.abs(m.tris).max() < FAR]
    pts = [np.array(p["at"]) for ps in problems.values() for p in ps]
    every = np.concatenate([t.reshape(-1, 3) for t in tris]) if tris else np.array(pts).reshape(-1, 3)
    if len(every) == 0:
        return
    lo, hi = every.min(0)[:2], every.max(0)[:2]
    scale = (size - 1) / max(hi - lo).max() if (hi - lo).max() > 0 else 1.0
    img = np.full((size, size, 3), 24, np.uint8)
    if tris:
        c = np.concatenate([t.mean(1) for t in tris])
        ij = ((c[:, :2] - lo) * scale).astype(int).clip(0, size - 1)
        img[size - 1 - ij[:, 1], ij[:, 0]] = (110, 110, 110)
    colours = {"collision": (230, 40, 40), "cracks": (255, 150, 0), "things": (240, 220, 0),
               "overlaps": (220, 60, 220)}
    for check, ps in problems.items():
        for p in ps:
            x, y = ((np.array(p["at"][:2]) - lo) * scale).astype(int)
            r = 4 if check != "collision" else 3 + int(min(10, math.sqrt(p["size"]) / 2))
            img[max(0, size - 1 - y - r):size - y + r, max(0, x - r):x + r + 1] = colours[check]
    write_png(path, img)


def write_png(path: Path, img: np.ndarray) -> None:
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main(project: Path, water: float | None = None, fix: bool = False, report: Path | None = None) -> int:
    """openrac_editor.py check-map: prints each problem as "openrac-problem: <node>: <what> at
    (x, y, z)" (the editor lists them and selects the node), 1 when there is any. WATER defaults
    to the project's map/water setting."""
    if water is None:
        water = water_of(project)
    result = check_level(project, water, fix)
    for what in result["fixed"]:
        print(f"openrac_editor: fixed: {what}")
    total = 0
    for check, ps in result["problems"].items():
        for p in ps:
            print(f"openrac-problem: {p.line()}", flush=True)
        total += len(ps)
        print(f"openrac_editor: {check}: {len(ps)} problem(s)")
    if report is not None:
        report = Path(report)
        report.parent.mkdir(parents=True, exist_ok=True)
        picture(result["level"], result["problems"], report.with_suffix(".png"))
        report.with_suffix(".json").write_text(json.dumps(
            {k: [dict(p) for p in v] for k, v in result["problems"].items()}, indent=1) + "\n", encoding="utf-8",
            newline="\n")
    print(f"openrac_editor: the map has {total} problem(s)" if total else "openrac_editor: the map is clean")
    return 1 if total else 0
