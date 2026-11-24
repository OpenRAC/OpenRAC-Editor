"""Puts a level's things down on its ground: no floating, no sinking, no standing in a wall.

The ground is what the player sees: the level's terrain and its static models (Game/Terrain,
Game/StaticAssets). Each thing (Game/Mobys, Game/Ties, Game/Shrubs) is read with its own model,
so it is set down by its whole underside, not by the one point under its middle:

- soft things (plants, rocks: shrubs and ties) are lowered until no part of their underside is
  above the ground, and turned with the slope when the slope would bury one side;
- hard things (mobys: crates, lamps, people) need level ground under all of their underside and
  nothing of a wall inside them; where the spot is too steep or in a wall, the nearest good spot
  within SEARCH units is taken instead, else the thing is listed as a problem.

Things far over the ground (more than LIFT units: on water, hanging, flying) and the classes that
float by design (HOVER: bolts, Swingshot targets) are left as they are.

`openrac_editor.py place PROJECT [--check]` runs it on a project's level scene; the editor's
`place_on_ground` tool (MCP) and level generators use place_scene()."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np


FLOOR_NZ = 0.35      # a face this upright or more is ground
WALL_NZ = 0.6        # a face less upright than this is wall
LIFT = 1.6           # a thing's underside further over its ground than this is not on the ground
DROP = 1.6           # ground a thing is sunk into deeper than this is not its ground (a floor above)
FLOAT_OK = 0.12      # gaps under a thing up to this are not seen
SEARCH = 4.0         # how far a hard thing may be moved to find a good spot
CELL = 4.0           # the ground's lookup grid
HARD_SINK = 0.6      # a hard thing's high side may sink this share of its height (set into a slope)
NEW_SINK = 0.25      # the same for a thing being placed now (MOVE): it stands on level ground
HEADROOM = 1.0       # a thing being placed now needs this many of its heights free above it
                     # (a tree high over a crate is fine; a block it is sunk in is not)
MAX_TILT = 25.0      # soft things are turned with the ground up to this many degrees
BIG = 12.0           # soft things wider than this (islets, towers) are left as they are
SOFT_SINK = 0.45     # soft things may sink this share of their height on the high side before they turn

# rac1 classes that float by design: bolts, the gold bolt, Swingshot and Versa targets. (Animated
# classes, the actors, are left as placed too: see animated().)
HOVER = {13, 14, 15, 16, 1134, 758, 803}

NODE = re.compile(r'^\[node name="([^"]+)" parent="([^"]+)"(?: instance=ExtResource\("([^"]+)"\))?')
RESOURCE = re.compile(r'^\[ext_resource type="PackedScene" path="res://([^"]+)" id="([^"]+)"\]')
TRANSFORM = re.compile(r"^transform = Transform3D\(([^)]*)\)", re.M)
CLASS = re.compile(r"_(\d+)\.(?:glb|tscn)$")
KINDS = {"Game/Mobys": "moby", "Game/Ties": "tie", "Game/Shrubs": "shrub"}


# --- reading ---

def read_matrix(text: str) -> tuple[np.ndarray, np.ndarray]:
    """A Transform3D's basis (rows, as the scene lists them) and origin."""
    v = [float(x) for x in text.split(",")]
    return np.array(v[:9], np.float64).reshape(3, 3), np.array(v[9:12], np.float64)


def write_matrix(basis: np.ndarray, origin: np.ndarray) -> str:
    return "Transform3D(" + ", ".join(f"{float(v):.7g}" for v in list(basis.reshape(-1)) + list(origin)) + ")"


def glb_triangles(path: Path) -> np.ndarray:
    """A glb's triangles (N, 3, 3) as its nodes place them (translation, rotation, scale or a
    matrix, through the node tree)."""
    import json
    import struct
    data = Path(path).read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    j = json.loads(data[20:20 + length])
    blob = data[20 + length + 8:]
    sizes = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    types = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}

    def read(i):
        a = j["accessors"][i]
        v = j["bufferViews"][a["bufferView"]]
        n, dt = sizes[a["type"]], np.dtype(types[a["componentType"]])
        off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = v.get("byteStride", 0) or n * dt.itemsize
        raw = np.frombuffer(blob, np.uint8, (a["count"] - 1) * stride + n * dt.itemsize, off)
        rows = np.lib.stride_tricks.as_strided(raw, (a["count"], n * dt.itemsize), (stride, 1))
        return rows.copy().view(dt).reshape(a["count"], n).astype(np.float64)

    def local(node):
        if "matrix" in node:
            return np.array(node["matrix"], np.float64).reshape(4, 4).T
        m = np.eye(4)
        x, y, z, w = node.get("rotation", [0, 0, 0, 1])
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        m[:3, :3] = r * np.array(node.get("scale", [1, 1, 1]))[None]
        m[:3, 3] = node.get("translation", [0, 0, 0])
        return m

    out = []

    def walk(i, parent):
        node = j["nodes"][i]
        m = parent @ local(node)
        if "mesh" in node:
            for prim in j["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4 or "POSITION" not in prim["attributes"]:
                    continue
                P = read(prim["attributes"]["POSITION"])
                F = read(prim["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in prim \
                    else np.arange(len(P) - len(P) % 3).reshape(-1, 3)
                out.append((P @ m[:3, :3].T + m[:3, 3])[F])
        for c in node.get("children", []):
            walk(c, m)

    scene = j.get("scenes", [{}])[j.get("scene", 0)] if j.get("scenes") else {"nodes": range(len(j.get("nodes", [])))}
    for i in scene.get("nodes", []):
        walk(i, np.eye(4))
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def animated(path: Path) -> bool:
    """Whether a glb has animations: an actor (the hero, enemies, people) moves and is set down by
    the game itself, so it is left as placed."""
    import json
    import struct
    data = path.read_bytes()
    length = struct.unpack("<I", data[12:16])[0]
    return bool(json.loads(data[20:20 + length]).get("animations"))


def model_of(project: Path, res: str) -> tuple[Path, np.ndarray] | None:
    """The glb a scene resource draws and the transform its own scene gives it (a moby scene
    holds its model scaled, as node Model)."""
    path = project / res
    if path.suffix == ".glb":
        return (path, np.eye(4)) if path.is_file() else None
    if path.suffix != ".tscn" or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    ids = {}
    for line in text.splitlines():
        m = RESOURCE.match(line)
        if m:
            ids[m.group(2)] = m.group(1)
    model = re.search(r'^\[node name="Model" parent="\." instance=ExtResource\("([^"]+)"\)\]\n(transform = Transform3D\(([^)]*)\))?', text, re.M)
    if not model or model.group(1) not in ids:
        return None
    m = np.eye(4)
    if model.group(3):
        b, o = read_matrix(model.group(3))
        m[:3, :3], m[:3, 3] = b, o
    glb = project / ids[model.group(1)]
    return (glb, m) if glb.is_file() else None


class Shape:
    """A model's points, to find its underside however it is turned (Pose)."""

    def __init__(self, tris: np.ndarray, below_origin_buried: bool = False):
        V = np.unique(np.round(tris.reshape(-1, 3), 4), axis=0)
        if len(V) > 1500:
            V = V[np.linspace(0, len(V) - 1, 1500).astype(int)]
        self.points = V
        self.actor = False
        # A moby's model under its own origin is meant in the ground (a lamp's post): its
        # underside is taken at the origin.
        self.buried = below_origin_buried and V[:, 2].min() < -0.05 and V[:, 2].max() > 0.05
        self.flat = np.ptp(V[:, 2]) < 0.05
        self._poses: dict = {}

    def pose(self, basis: np.ndarray) -> "Pose":
        key = np.round(basis, 5).tobytes()
        if key not in self._poses:
            if len(self._poses) > 64:
                self._poses.clear()
            self._poses[key] = Pose(self, basis)
        return self._poses[key]


class Pose:
    """A shape turned by a basis, about its origin: its feet (points round its underside's
    outline, the centre last), its middle's feet (core), its box and size."""

    def __init__(self, shape: Shape, basis: np.ndarray):
        W = shape.points @ basis.T
        if shape.buried:
            W = W[(shape.points[:, 2] >= 0)] if (shape.points[:, 2] >= 0).sum() >= 3 else W
        self.lo, self.hi = W.min(0), W.max(0)
        bottom = self.lo[2] if not shape.buried else max(self.lo[2], 0.0)
        h = self.hi[2] - bottom
        self.height = float(h)
        self.size = float(np.linalg.norm((self.hi - self.lo)[:2]))
        low = W[W[:, 2] <= bottom + max(0.15 * h, 0.02)]
        hull = convex_hull(low[:, :2])
        centre = low[:, :2].mean(0)
        ring = centre + (hull - centre) * 0.85
        mids = (ring + np.roll(ring, -1, 0)) / 2
        pts = np.concatenate([ring, mids])
        if len(pts) > 24:
            pts = pts[np.linspace(0, len(pts) - 1, 24).astype(int)]
        pts = np.concatenate([pts, centre[None]])
        self.feet = np.column_stack([pts, np.full(len(pts), bottom)])
        # A plant or a rock stands on its middle: its leaves and edges may hang over a slope.
        core = np.concatenate([centre + (hull - centre) * 0.35, centre[None]])
        self.core = np.column_stack([core, np.full(len(core), bottom)])
        self.bottom = float(bottom)


def cross2(a, b) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def convex_hull(P: np.ndarray) -> np.ndarray:
    P = np.unique(np.round(P, 4), axis=0)
    if len(P) < 3:
        return P if len(P) else np.zeros((1, 2))
    P = P[np.lexsort((P[:, 1], P[:, 0]))]

    def half(points):
        out = []
        for p in points:
            while len(out) >= 2 and cross2(out[-1] - out[-2], p - out[-2]) <= 0:
                out.pop()
            out.append(p)
        return out
    lower, upper = half(P), half(P[::-1])
    return np.array(lower[:-1] + upper[:-1])


# --- the ground ---

class Ground:
    """The level's drawn ground: the floor's height under any point and the walls in any box."""

    def __init__(self, tris: np.ndarray, owners=None):
        """TRIS (N, 3, 3); OWNERS (N,) names the thing each face belongs to ("" for the land),
        so a thing is never its own ground."""
        tris = np.asarray(tris, np.float64)
        owners = np.asarray(owners if owners is not None else [""] * len(tris), object)
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        length = np.linalg.norm(n, axis=1)
        keep = length > 1e-9
        tris, n, owners = tris[keep], n[keep] / length[keep, None], owners[keep]
        # Either winding: the game's faces are not all wound one way (it draws both sides).
        n = np.where(n[:, 2:3] < 0, -n, n)
        up = n[:, 2] > FLOOR_NZ
        self.floors, self.floor_n, self.floor_owner = tris[up], n[up], owners[up]
        walls = np.abs(n[:, 2]) < WALL_NZ
        self.walls, self.wall_owner = tris[walls], owners[walls]
        self.floor_cells = buckets(self.floors)
        self.wall_cells = buckets(self.walls)

    def floor(self, P: np.ndarray, top: np.ndarray, bottom: np.ndarray, exclude: str = ""):
        """For each point P (N, 2): the highest floor between BOTTOM and TOP (N,), its height and
        normal (NaN where there is none)."""
        P = np.asarray(P, np.float64)
        z = np.full(len(P), np.nan)
        nrm = np.full((len(P), 3), np.nan)
        keys = np.floor(P / CELL).astype(int)
        order = {}
        for i, k in enumerate(map(tuple, keys)):
            order.setdefault(k, []).append(i)
        for k, idx in order.items():
            cand = self.floor_cells.get(k)
            if cand is None:
                continue
            idx = np.array(idx)
            if exclude:
                own = self.floor_owner[cand]
                cand = cand[(own != exclude) & (own != "m:" + exclude)]
                if len(cand) == 0:
                    continue
            T = self.floors[cand]
            q = P[idx]
            a, b, c = T[:, 0], T[:, 1], T[:, 2]
            v0, v1 = b[:, :2] - a[:, :2], c[:, :2] - a[:, :2]
            den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            w = q[:, None, :] - a[None, :, :2]
            u = (w[..., 0] * v1[:, 1] - v1[:, 0] * w[..., 1]) / den
            v = (v0[:, 0] * w[..., 1] - w[..., 0] * v0[:, 1]) / den
            zz = a[:, 2] + u * (b[:, 2] - a[:, 2]) + v * (c[:, 2] - a[:, 2])
            ok = (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
            ok &= (zz <= top[idx, None]) & (zz >= bottom[idx, None])
            zz = np.where(ok, zz, -np.inf)
            best = zz.argmax(1)
            hit = np.isfinite(zz[np.arange(len(idx)), best])
            z[idx[hit]] = zz[np.arange(len(idx)), best][hit]
            nrm[idx[hit]] = self.floor_n[cand][best[hit]]
        return z, nrm

    def walls_in(self, centre: np.ndarray, half: np.ndarray, basis: np.ndarray, exclude: str = "",
                 mobys: bool = False) -> int:
        """How many wall faces cross the box at CENTRE, HALF its size along BASIS's columns (MOBYS:
        other mobys' sides count too)."""
        lo = centre - np.abs(basis) @ half
        hi = centre + np.abs(basis) @ half
        cand = set()
        for i in range(int(math.floor(lo[0] / CELL)), int(math.floor(hi[0] / CELL)) + 1):
            for j in range(int(math.floor(lo[1] / CELL)), int(math.floor(hi[1] / CELL)) + 1):
                cand.update(self.wall_cells.get((i, j), []))
        # Other mobys are no walls where they stand now (crates against crates, in stacks); a thing
        # placed now must not go into one.
        cand = [c for c in cand if self.wall_owner[c] not in (exclude, "m:" + exclude)
                and (mobys or not str(self.wall_owner[c]).startswith("m:"))]
        if not cand:
            return 0
        T = self.walls[sorted(cand)]
        # Into the box's own frame, then the separating-axis test against a box at the origin.
        local = (T - centre) @ basis
        return int(tri_box(local, half).sum())


def buckets(tris: np.ndarray) -> dict:
    out: dict = {}
    if len(tris) == 0:
        return out
    lo = np.floor(tris[:, :, :2].min(1) / CELL).astype(int)
    hi = np.floor(tris[:, :, :2].max(1) / CELL).astype(int)
    for t in range(len(tris)):
        for i in range(lo[t, 0], hi[t, 0] + 1):
            for j in range(lo[t, 1], hi[t, 1] + 1):
                out.setdefault((i, j), []).append(t)
    return {k: np.array(v) for k, v in out.items()}


def tri_box(T: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Which triangles T (N, 3, 3) cross the box of half size H at the origin (separating axes)."""
    e = np.eye(3)
    f = [T[:, 1] - T[:, 0], T[:, 2] - T[:, 1], T[:, 0] - T[:, 2]]
    axes = [np.broadcast_to(e[k], (len(T), 3)) for k in range(3)]
    axes.append(np.cross(f[0], f[1]))
    axes += [np.cross(np.broadcast_to(e[k], (len(T), 3)), fe) for k in range(3) for fe in f]
    hit = np.ones(len(T), bool)
    for a in axes:
        p = np.einsum("nij,nj->ni", T, a)
        r = np.abs(a) @ h
        hit &= ~((p.min(1) > r + 1e-9) | (p.max(1) < -r - 1e-9))
    return hit


def level_ground(project: Path, text: str) -> Ground:
    """The level's drawn ground: its terrain, its static models, its ties (rocks, platforms,
    buildings) and its still mobys (crates stand on crates), as the scene places them."""
    ids = {}
    tris, owners = [], []
    current = None

    def add(entry):
        if entry[3] and not ground_moby(project, entry[0]):
            return
        T = tris_of(project, entry)
        tris.append(T)
        owner = "" if not entry[3] else ("m:" if "/mobys/" in entry[0] else "") + entry[2]
        owners.append(np.full(len(T), owner, object))

    for line in text.splitlines():
        m = RESOURCE.match(line)
        if m:
            ids[m.group(2)] = m.group(1)
            continue
        m = NODE.match(line)
        if m:
            if current is not None:
                add(current)
            current = None
            name, parent, inst = m.groups()
            if inst and inst in ids and ((parent == "Game" and name == "Terrain")
                                         or parent in ("Game/StaticAssets", "Game/Ties", "Game/Mobys")):
                current = [ids[inst], None, name, parent in ("Game/Ties", "Game/Mobys")]
            continue
        if current is not None and line.startswith("transform = "):
            current[1] = read_matrix(TRANSFORM.match(line).group(1))
    if current is not None:
        add(current)
    if not tris:
        return Ground(np.zeros((0, 3, 3)))
    return Ground(np.concatenate(tris), np.concatenate(owners))


_cache: dict = {}


def ground_moby(project: Path, res: str) -> bool:
    """Whether a placed object is ground for others: ties are; of the mobys only the still ones
    (crates on crates, a lamp's base), not the actors nor the floating bolts and targets."""
    if "/mobys/" not in res:
        return True
    cls = CLASS.search(res)
    if cls and int(cls.group(1)) in HOVER:
        return False
    found = model_of(project, res)
    return found is not None and not animated(found[0])


def tris_of(project, entry) -> np.ndarray:
    res, xform = entry[0], entry[1]
    found = model_of(project, res)
    if found is None:
        return np.zeros((0, 3, 3))
    path, inner = found
    key = str(path)
    if key not in _cache:
        _cache[key] = glb_triangles(path)
    T = _cache[key] @ inner[:3, :3].T + inner[:3, 3]
    if xform is not None:
        T = T @ xform[0].T + xform[1]
    return T


# --- setting things down ---

def settle(ground: Ground, shape: Shape, basis: np.ndarray, origin: np.ndarray, hard: bool, water=None, name="",
           move=True):
    """Where a thing goes: (basis, origin, what was done, problem or None). A thing floating on
    WATER (a height: its underside within a little of it) stays."""
    pose = shape.pose(basis)
    # Ground up to a little over its underside counts (it may be sunk), not a floor above it.
    # A made thing may be sunk almost whole; a plant only partly (its top may sit under a ledge).
    reach = 0.9 * pose.height if hard else min(DROP, max(0.3, 0.6 * pose.height))

    def look(b, o, core=False, extra=0.0):
        """Its feet, the ground under each (NaN: none within LIFT below, an edge) and its normal.
        EXTRA widens the window both ways (a spot found to the side may be higher or lower)."""
        ps = shape.pose(b)
        F = (ps.core if core else ps.feet) + o
        g, n = ground.floor(F[:, :2], F[:, 2] + reach + extra, F[:, 2] - LIFT - extra, name)
        return F, g, n

    F, g, n = look(basis, origin)
    if np.isnan(g).all():
        return basis, origin, None, None                     # nothing under it: water, sky, flying
    if water is not None and abs(F[:, 2].min() - water) < 0.4:
        return basis, origin, None, None                     # afloat
    if not hard:
        return settle_soft(ground, shape, basis, origin, lambda b, o: look(b, o, True), pose.height, pose.size)
    return settle_hard(ground, shape, basis, origin, look, pose.size, pose.height, name, move)


def settle_soft(ground, shape, basis, origin, look, height, size):
    """A plant or a rock: lowered when its middle floats (the whole middle, or its centre), until
    it touches. Sunk is fine (rocks are set into the ground, plants grow out of it), so it is never
    raised; on a slope that would bury one side it is turned with the slope first."""
    if size > BIG:
        return basis, origin, None, None          # islets, towers: part of the land, set by hand
    F, g, n = look(basis, origin)
    ok = ~np.isnan(g)
    if not ok[-1]:
        return basis, origin, None, None          # its centre over an edge: hangs over it on purpose
    gap = F[ok, 2] - g[ok]
    lift = max(gap.min(), gap[-1])
    if lift <= FLOAT_OK:
        return basis, origin, None, None
    if np.ptp(gap) > SOFT_SINK * max(height, 0.2):
        up = np.nanmean(n[ok], 0)
        up /= np.linalg.norm(up)
        turned = tilt(basis, up)
        F2, g2, n2 = look(turned, origin)
        ok2 = ~np.isnan(g2)
        if ok2[-1]:
            gap2 = F2[ok2, 2] - g2[ok2]
            if np.ptp(gap2) < np.ptp(gap) and max(gap2.min(), gap2[-1]) > 0:
                basis, gap = turned, gap2
                lift = max(gap.min(), gap[-1])
    return basis, origin + np.array([0, 0, -lift]), f"lowered {lift:.2f}", None


def settle_hard(ground, shape, basis, origin, look, size, height, name="", move=True):
    """A made thing (a crate, a lamp): no foot may float, and the high side may sink only a
    little (SINK of its height). Else the nearest spot within SEARCH where it can stand. A thing
    being placed now (MOVE) must stand level and have room above it (not inside a block)."""
    sink = max((NEW_SINK if move else HARD_SINK) * height, 0.05)

    def fit(o, extra=0.0):
        """O moved to stand at once, or None when it cannot stand there."""
        F, g, n = look(basis, o, extra=extra)
        if np.isnan(g).any():
            return None
        gap = F[:, 2] - g
        if gap.max() <= FLOAT_OK and -gap.min() <= sink:
            o2 = o                                    # stands as it is
        elif gap.max() - gap.min() <= sink:
            o2 = o + np.array([0, 0, -gap.max()])     # every foot down onto the ground
        else:
            return None
        if move:
            top = np.full(len(F), g.max() + (1 + HEADROOM) * height)
            above, _ = ground.floor(F[:, :2], top, g + 0.05 * height + 0.02, name)
            if not np.isnan(above).all():
                return None                           # something over it: a ceiling, inside a block
        return None if wall_inside(ground, shape, basis, o2, name, float(g.max()), move) else o2

    first = fit(origin)
    if first is not None:
        dz = first[2] - origin[2]
        return basis, first, (f"{'lowered' if dz < 0 else 'raised'} {abs(dz):.2f}" if dz else None), None
    for r in np.arange(0.5, SEARCH + 0.01, 0.5) if move else ():
        steps = int(8 + r * 4)
        for k in range(steps):
            a = 2 * math.pi * k / steps
            got = fit(origin + np.array([math.cos(a) * r, math.sin(a) * r, 0.0]), r)
            if got is not None:
                return basis, got, f"moved {r:.1f} to level ground", None
    F, g, n = look(basis, origin)
    if np.isnan(g).any():
        why = "part of it has no ground under it"
    elif np.ptp(g) > sink:
        why = f"the ground under it is not level ({np.ptp(g):.2f} units)"
    else:
        why = "it stands in a wall"
    return basis, origin, None, f"{why}, and no good spot within {SEARCH:g} units" if move else why


def wall_inside(ground, shape, basis, origin, name="", floor_top=None, mobys=False) -> bool:
    """A wall through the middle of the thing's box over the ground under it (FLOOR_TOP: the
    highest ground under its feet; the slope it is set into is no wall). Its outer part is left
    out: touching a wall, or a small detail, is fine."""
    ps = shape.pose(basis)
    lo, hi = ps.lo + origin, ps.hi + origin
    lo[2] = max(ps.bottom + origin[2] + 0.15 * ps.height, (floor_top if floor_top is not None else -1e9) + 0.1 * ps.height)
    if lo[2] >= hi[2] - 0.05 * ps.height:
        return False
    centre = (lo + hi) / 2
    half = (hi - lo) / 2 * np.array([0.6, 0.6, 1.0])
    return ground.walls_in(centre, half, np.eye(3), name, mobys) > 0


def tilt(basis: np.ndarray, up: np.ndarray) -> np.ndarray:
    """BASIS turned so its up follows UP, by at most MAX_TILT."""
    z = np.array([0.0, 0.0, 1.0])
    axis = np.cross(z, up)
    s = np.linalg.norm(axis)
    if s < 1e-6:
        return basis
    angle = min(math.asin(min(s, 1.0)), math.radians(MAX_TILT))
    k = axis / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    R = np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * K @ K
    return R @ basis


# --- a whole scene ---

def scene_path(project: Path) -> Path:
    found = sorted(Path(project).glob("levels/level_*/level_*.tscn"))
    if not found:
        raise FileNotFoundError(f"{project}: no level scene (levels/level_NN/level_NN.tscn)")
    return found[0]


def place_scene(project: Path, text: str, names=None, fix=True, hard_classes=(), soft_classes=(), hover=HOVER,
                water=None, move=False):
    """Every thing of the scene TEXT (or those whose names match NAMES, fnmatch patterns) set
    down. MOVE lets a hard thing that cannot stand where it is go to the nearest good spot
    (for things just placed); without it such a thing is only listed. Returns (new text,
    report): report is a list of {node, kind, class, done, problem, position}."""
    import fnmatch
    project = Path(project)
    ground = level_ground(project, text)
    ids, shapes = {}, {}
    lines = text.split("\n")
    report = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = RESOURCE.match(line)
        if m:
            ids[m.group(2)] = m.group(1)
        node = NODE.match(line)
        i += 1
        if not node or node.group(2) not in KINDS or not node.group(3):
            continue
        name, parent, inst = node.groups()
        if names and not any(fnmatch.fnmatch(name, p) for p in names):
            continue
        kind = KINDS[parent]
        res = ids.get(inst, "")
        cls = CLASS.search(res)
        cls = int(cls.group(1)) if cls else -1
        if kind == "moby" and cls in hover:
            continue
        j = i
        while j < len(lines) and not lines[j].startswith("[") and not lines[j].startswith("transform = "):
            j += 1
        if j >= len(lines) or not lines[j].startswith("transform = "):
            continue
        if res not in shapes:
            found = model_of(project, res)
            if found is None:
                shapes[res] = None
            else:
                path, inner = found
                T = glb_triangles(path) @ inner[:3, :3].T + inner[:3, 3]
                shapes[res] = Shape(T, kind == "moby") if len(T) else None
                if shapes[res] is not None:
                    shapes[res].actor = kind == "moby" and animated(path)
        shape = shapes[res]
        if shape is None or (shape.actor and cls not in hard_classes) or (kind == "moby" and shape.flat):
            continue
        hard = (kind == "moby" or cls in hard_classes) and cls not in soft_classes
        basis, origin = read_matrix(TRANSFORM.match(lines[j]).group(1))
        b2, o2, done, problem = settle(ground, shape, basis, origin, hard, water, name, move)
        if done or problem:
            report.append({"node": name, "kind": kind, "class": cls, "done": done, "problem": problem,
                           "position": [round(float(v), 3) for v in o2]})
        if fix and done:
            lines[j] = "transform = " + write_matrix(b2, o2)
    return "\n".join(lines), report


def main(project: Path, check: bool = False, names=None, water=None, move=False) -> int:
    """openrac_editor.py place: sets the project's things down (or only lists them, CHECK).
    Things standing on things (crates on crates) settle over a few rounds."""
    path = scene_path(project)
    text = original = path.read_text(encoding="utf-8")
    done: dict = {}
    problems: dict = {}
    for _ in range(1 if check else 3):
        text, report = place_scene(project, text, names=names, fix=not check, water=water, move=move)
        moved = [r for r in report if r["done"]]
        for r in moved:
            done[r["node"]] = r["done"]
        problems = {r["node"]: r["problem"] for r in report if r["problem"]}
        if not moved:
            break
    for node, what in done.items():
        print(f"openrac_editor: {node}: {what if not check else 'would be ' + what}")
    for node, why in problems.items():
        print(f"openrac-problem: {node}: {why}")
    if not check and text != original:
        path.write_text(text, encoding="utf-8", newline="\n")
    print(f"openrac_editor: {'would set' if check else 'set'} {len(done)} thing(s) down; "
          f"{len(problems)} cannot stand where they are")
    return 1 if problems or (check and done) else 0
