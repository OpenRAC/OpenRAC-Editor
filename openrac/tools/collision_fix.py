"""Collision made from what is drawn: the map's own terrain (map_check.py's "terrain" meshes) as
the hero meets it, so he never stands in the air over it, sinks into it or falls through it.

The drawn ground's top is sampled every STEP units where the hero can go (near the level's
collision now), then laid out as triangles from big to small squares: a square is kept when its
corners follow every sample in it within TOLERANCE, else it is cut in four. Flat ground takes a few
big triangles, a slope or a cliff many small ones, and the whole stays far under the level's
collision room. Each triangle keeps the surface type (footstep sound, slippery ground) of the old
collision under it.

The collision nodes that were that ground's (most of their floor nearer it than any house or tie) are taken out
of Game/Collision and one node, DrawnGround (collision/drawn_ground.glb), takes their place; walls
and other nodes (a house, the ties' blocks) stay as they are."""

from __future__ import annotations

import re

import map_check as mc  # noqa: F401,I001  (first: it puts the release's numpy on the path)
import numpy as np

import placement as pl

STEP = 1.0            # the drawn ground is sampled this often
TOLERANCE = 0.2       # a square's triangles follow every sample under them this closely (across the surface)
BIGGEST = 32          # the biggest square, in samples
NEAR = 8.0            # a collision node's floor this close to the drawn terrain may be its collision
SHARE = 0.5           # ... when at least this share of its floor is
OVER = 0.5            # an added collision floor this far over everything drawn is air
NAME = "DrawnGround"
FILE = "drawn_ground.glb"


def surface(level: mc.Level, water: float | None):
    """The drawn ground's top over the hero's reach: (x0, y0, heights (rows, columns), mask of
    the samples to keep). NaN where nothing of the map's own terrain is."""
    made = [m for m in level.drawn if m.kind == "terrain" and np.abs(m.tris).max() < mc.FAR]
    if not made:
        return None
    ground = pl.Ground(np.concatenate([m.tris for m in made]))
    old = old_collision(level)
    tris = np.concatenate([t for _, t, _ in old]) if old else np.concatenate([m.tris for m in made])
    lo = tris.reshape(-1, 3).min(0)[:2] - mc.REACH
    hi = tris.reshape(-1, 3).max(0)[:2] + mc.REACH
    xs = np.arange(lo[0], hi[0] + STEP, STEP)
    ys = np.arange(lo[1], hi[1] + STEP, STEP)
    X, Y = np.meshgrid(xs, ys)
    P = np.c_[X.ravel(), Y.ravel()]
    near = mc.reach(level, P, X.shape, STEP) if old else np.ones(len(P), bool)
    Z = np.full(len(P), np.nan)
    z, _ = ground.floor(P[near], np.full(near.sum(), 1e9), np.full(near.sum(), -1e9))
    Z[near] = z
    return float(lo[0]), float(lo[1]), Z.reshape(X.shape)


def old_collision(level: mc.Level):
    """Each node of Game/Collision: (name, triangles (N, 3, 3) in game units, type bytes (N,))."""
    import collision_scene as cs
    _ext, sub, nodes = cs.read_scene(level.text)
    out = []
    for node in cs.layer_nodes(nodes, ("Game/Collision",)):
        try:
            found = cs.node_triangles(level.project, node, sub)
        except Exception:  # noqa: BLE001 - a node the pack cannot read stays as it is
            continue
        if found:
            out.append((node["name"], np.array([p for p, _ in found], np.float64).reshape(-1, 3, 3),
                        np.array([k if k is not None else cs.DEFAULT_SURFACE for _, k in found])))
    return out


def squares(Z: np.ndarray) -> list[tuple[int, int, int]]:
    """The squares (row, column, size in samples) that cover Z's samples, biggest first."""
    rows, cols = Z.shape
    n = BIGGEST
    R = -(-(rows - 1) // n) * n + 1
    C = -(-(cols - 1) // n) * n + 1
    H = np.full((R, C), np.nan)
    H[:rows, :cols] = Z
    taken = np.zeros((R - 1, C - 1), bool)
    out = []
    s = n
    while s >= 1:
        win = np.lib.stride_tricks.sliding_window_view(H, (s + 1, s + 1))[::s, ::s]
        u = np.linspace(0, 1, s + 1)
        a, b, c, d = win[..., 0, 0], win[..., 0, -1], win[..., -1, 0], win[..., -1, -1]
        top = a[..., None] + (b - a)[..., None] * u
        bottom = c[..., None] + (d - c)[..., None] * u
        guess = top[..., None, :] + (bottom - top)[..., None, :] * u[:, None]
        # The distance across the surface, not up: steep rock may be off more in height.
        size = s * STEP
        gx = ((b - a) + (d - c)) / (2 * size)
        gy = ((c - a) + (d - b)) / (2 * size)
        with np.errstate(invalid="ignore"):
            error = np.abs(win - guess).max(axis=(-1, -2)) / np.sqrt(1 + gx * gx + gy * gy)
        free = ~taken[::s, ::s][:win.shape[0], :win.shape[1]]
        if s > 1:
            leaf = free & (error <= TOLERANCE)
        else:
            leaf = free & (np.isfinite(win).sum(axis=(-1, -2)) >= 3)
        for i, j in zip(*np.nonzero(leaf)):
            out.append((int(i * s), int(j * s), s))
            taken[i * s:i * s + s, j * s:j * s + s] = True
        s //= 2
    return out


def triangles(x0: float, y0: float, Z: np.ndarray, found: list[tuple[int, int, int]]) -> np.ndarray:
    """Each square as two triangles (the diagonal that follows its middle better), or as the ones
    its sampled corners allow."""
    out = []
    for i, j, s in found:
        corner = {}
        for di, dj in ((0, 0), (0, s), (s, 0), (s, s)):
            z = Z[i + di, j + dj] if i + di < Z.shape[0] and j + dj < Z.shape[1] else np.nan
            if np.isfinite(z):
                corner[(di, dj)] = (x0 + (j + dj) * STEP, y0 + (i + di) * STEP, float(z))
        if len(corner) < 3:
            continue
        a, b, c, d = (corner.get(k) for k in ((0, 0), (0, s), (s, 0), (s, s)))
        if len(corner) == 3:
            out.append([p for p in (a, b, d, c) if p is not None])
            continue
        m = Z[i + s // 2, j + s // 2] if s > 1 else np.nan
        if np.isfinite(m) and abs((a[2] + d[2]) / 2 - m) > abs((b[2] + c[2]) / 2 - m):
            out += [[a, b, c], [b, d, c]]
        else:
            out += [[a, b, d], [a, d, c]]
    return np.array(out, np.float64).reshape(-1, 3, 3)


def surface_types(new: np.ndarray, old: list) -> np.ndarray:
    """Each new triangle's type byte: the old collision floor's under its middle, else the
    default."""
    import collision_scene as cs
    kinds = np.full(len(new), cs.DEFAULT_SURFACE)
    floors = [(t, k) for _, t, k in old]
    if not floors:
        return kinds
    tris = np.concatenate([t for t, _ in floors])
    types = np.concatenate([k for _, k in floors])
    ground = pl.Ground(tris, types.astype(str))
    centre = new.mean(1)
    z, _ = ground.floor(centre[:, :2], centre[:, 2] + 3.0, centre[:, 2] - 3.0)
    keys = np.floor(centre[:, :2] / pl.CELL).astype(int)
    for n, k in enumerate(map(tuple, keys)):
        if np.isnan(z[n]):
            continue
        cand = ground.floor_cells.get(k)
        if cand is None:
            continue
        T = ground.floors[cand]
        dist = np.linalg.norm(T.mean(1) - centre[n], axis=1)
        kinds[n] = int(ground.floor_owner[cand][dist.argmin()])
    return kinds


def replaced(level: mc.Level, old: list, Z: np.ndarray, x0: float, y0: float) -> list[str]:
    """The old nodes that are the terrain's collision: most of their floor (SHARE) nearer the
    drawn terrain than any other drawn thing (a house, a tie), within NEAR of it. A disc tile
    (Tile_XX_YY) is the level's old ground and goes too when it lies over the map's terrain."""
    others = [m.tris for m in level.drawn if m.kind == "static"] + [m.tris for m in level.things if m.kind == "tie"]
    things = pl.Ground(np.concatenate(others)) if others else None
    out = []
    for name, tris, _ in old:
        if name == NAME:
            out.append(name)
            continue
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        up = np.abs(n[:, 2]) > pl.FLOOR_NZ * np.linalg.norm(n, axis=1)
        if not up.any():
            continue
        c = tris[up].mean(1)
        j = np.round((c[:, 0] - x0) / STEP).astype(int)
        i = np.round((c[:, 1] - y0) / STEP).astype(int)
        inside = (i >= 0) & (i < Z.shape[0]) & (j >= 0) & (j < Z.shape[1])
        z = np.full(len(c), np.nan)
        z[inside] = Z[i[inside], j[inside]]
        terrain = np.abs(z - c[:, 2])
        thing = np.full(len(c), np.inf)
        if things is not None:
            zt, _ = things.floor(c[:, :2], c[:, 2] + NEAR, c[:, 2] - NEAR)
            thing = np.where(np.isnan(zt), np.inf, np.abs(zt - c[:, 2]))
        ours = (terrain < NEAR) & (terrain <= thing)
        if ours.sum() >= SHARE * len(c):
            out.append(name)
    return out


def make(level: mc.Level, water: float | None = None) -> dict | None:
    """The collision for LEVEL's own terrain, written beside the scene, and the scene's text with
    it in place of the nodes it replaces: {"text", "triangles", "replaced"}; None when the level
    has no terrain of its own."""
    import collision_scene as cs
    found = surface(level, water)
    if found is None:
        return None
    x0, y0, Z = found
    new = triangles(x0, y0, Z, squares(Z))
    old = old_collision(level)
    gone = replaced(level, old, Z, x0, y0)
    kinds = surface_types(new, [o for o in old if o[0] in gone])
    by_kind: dict[int, list] = {}
    for t, k in zip(new, kinds):
        q = tuple(tuple(int(round(v * cs.SCALE)) for v in p) for p in t)
        by_kind.setdefault(int(k), []).append(q)
    file = level.dir / "collision" / FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(cs.triangles_glb(NAME, by_kind))
    res = "res://" + file.relative_to(level.project).as_posix()
    text = remove_nodes(level.text, [f"Game/Collision/{name}" for name in gone])
    text = add_node(text, res)
    return {"text": text, "triangles": len(new), "replaced": [g for g in gone if g != NAME]}


SECTION = re.compile(r'^\[node name="([^"]+)" parent="([^"]+)"', re.M)


def remove_nodes(text: str, paths: list[str]) -> str:
    """TEXT without the nodes at PATHS (relative to the scene's root) and their children."""
    if not paths:
        return text
    parts = re.split(r"(?m)^(?=\[)", text)
    out = []
    for part in parts:
        m = SECTION.match(part)
        if m:
            path = f"{m.group(2)}/{m.group(1)}"
            if any(path == p or path.startswith(p + "/") or m.group(2) == p or m.group(2).startswith(p + "/")
                   for p in paths):
                continue
        out.append(part)
    return "".join(out)


def add_node(text: str, res: str) -> str:
    """TEXT with a node DrawnGround under Game/Collision, an instance of RES."""
    rid = "drawn_ground"
    if f'id="{rid}"' not in text:
        heads = list(re.finditer(r'(?m)^\[ext_resource [^\n]*\]\n', text))
        at = heads[-1].end() if heads else text.index("\n") + 1
        text = text[:at] + f'[ext_resource type="PackedScene" path="{res}" id="{rid}"]\n' + text[at:]
    text = text.rstrip("\n") + f'\n\n[node name="{NAME}" parent="Game/Collision" instance=ExtResource("{rid}")]\n'
    return text


def follow(before: mc.Level, after_text: str) -> tuple[str, int]:
    """The collision of the ties a fix moved, moved with them: the triangles of each added
    collision node (not the disc's tiles, not DrawnGround) inside a moved tie's old box go by its
    move. A node that changed is written again whole (collision/<name>.glb, in level units) and
    the scene points at it. Returns (the scene text, how many triangles moved)."""
    import collision_scene as cs
    old = {n[0]: n[4] for n in before.nodes if n[1] == "Game/Ties"}
    new = {n[0]: n[4] for n in mc.scene_nodes(after_text) if n[1] == "Game/Ties"}
    moves = []
    for m in before.things:
        if m.kind == "tie" and m.name in new and not np.allclose(old[m.name], new[m.name], atol=1e-4):
            V = m.tris.reshape(-1, 3)
            moves.append((V.min(0) - [0.5, 0.5, 1.0], V.max(0) + [0.5, 0.5, 1.0], new[m.name] - old[m.name]))
    if not moves:
        return after_text, 0
    manifest = before.dir / "collision" / "collision.json"
    tiles = set()
    if manifest.is_file():
        import json
        tiles = set(json.loads(manifest.read_text(encoding="utf-8")).get("tiles", {}))
    _ext, sub, nodes = cs.read_scene(after_text)
    text, total = after_text, 0
    for node in cs.layer_nodes(nodes, ("Game/Collision",)):
        if node["name"] in tiles or node["name"] == NAME or node["instance"] is None:
            continue
        try:
            found = cs.node_triangles(before.project, node, sub)
        except Exception:  # noqa: BLE001 - a node the pack cannot read stays as it is
            continue
        if not found:
            continue
        tris = np.array([p for p, _ in found], np.float64).reshape(-1, 3, 3)
        kinds = [k for _, k in found]
        centre = tris.mean(1)
        moved = np.zeros(len(tris), bool)
        for lo, hi, delta in moves:
            inside = ~moved & (centre >= lo).all(1) & (centre <= hi).all(1)
            tris[inside] += delta
            moved |= inside
        if not moved.any():
            continue
        text = rewrite(before, text, node["name"], tris, kinds)
        total += int(moved.sum())
    return text, total


def added_nodes(level: mc.Level, text: str):
    """The added collision nodes of TEXT (not the disc's tiles, not DrawnGround) with their
    triangles in level units and type bytes: [(name, (N, 3, 3), [kind])]."""
    import json
    import collision_scene as cs
    manifest = level.dir / "collision" / "collision.json"
    tiles = set(json.loads(manifest.read_text(encoding="utf-8")).get("tiles", {})) if manifest.is_file() else set()
    _ext, sub, nodes = cs.read_scene(text)
    out = []
    for node in cs.layer_nodes(nodes, ("Game/Collision",)):
        if node["name"] in tiles or node["name"] == NAME or node["instance"] is None:
            continue
        try:
            found = cs.node_triangles(level.project, node, sub)
        except Exception:  # noqa: BLE001 - a node the pack cannot read stays as it is
            continue
        if found:
            out.append((node["name"], np.array([p for p, _ in found], np.float64).reshape(-1, 3, 3),
                        [k for _, k in found]))
    return out


def rewrite(level: mc.Level, text: str, name: str, tris: np.ndarray, kinds: list) -> str:
    """Writes Game/Collision's node NAME again as TRIS (collision/<name>.glb) and points TEXT at it."""
    import collision_scene as cs
    by_kind: dict[int, list] = {}
    for t, k in zip(tris, kinds):
        by_kind.setdefault(int(k), []).append(tuple(tuple(int(round(v * cs.SCALE)) for v in p) for p in t))
    file = level.dir / "collision" / f"{name.lower()}.glb"
    file.write_bytes(cs.triangles_glb(name, by_kind))
    return repoint(text, name, "res://" + file.relative_to(level.project).as_posix())


def trim(level: mc.Level) -> tuple[str, int]:
    """The added collision's floors that lie over everything drawn (OVER above the highest drawn
    surface there: a block round a rock wider than the rock, a post taller than its tree) taken
    out, so the hero never stands on air. Returns (the scene text, how many went)."""
    meshes = [m.tris for m in level.drawn if np.abs(m.tris).max() < mc.FAR] + [m.tris for m in level.things]
    if not meshes:
        return level.text, 0
    seen = pl.Ground(np.concatenate(meshes))
    text, total = level.text, 0
    for name, tris, kinds in added_nodes(level, level.text):
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        up = n[:, 2] > pl.FLOOR_NZ * np.linalg.norm(n, axis=1)
        up |= -n[:, 2] > pl.FLOOR_NZ * np.linalg.norm(n, axis=1)
        if not up.any():
            continue
        c = tris[up].mean(1)
        z, _ = seen.floor(c[:, :2], np.full(len(c), 1e9), np.full(len(c), -1e9))
        air = np.zeros(len(tris), bool)
        air[np.nonzero(up)[0]] = ~np.isnan(z) & (c[:, 2] > z + OVER)
        if not air.any():
            continue
        text = rewrite(level, text, name, tris[~air], [k for k, a in zip(kinds, air) if not a])
        total += int(air.sum())
    return text, total


def repoint(text: str, name: str, res: str) -> str:
    """TEXT with Game/Collision's node NAME an instance of RES at the level's origin (its
    triangles are written where they stand)."""
    rid = "collision_" + re.sub(r"\W", "_", name.lower())
    if f'id="{rid}"' not in text:
        heads = list(re.finditer(r'(?m)^\[ext_resource [^\n]*\]\n', text))
        at = heads[-1].end() if heads else text.index("\n") + 1
        text = text[:at] + f'[ext_resource type="PackedScene" path="{res}" id="{rid}"]\n' + text[at:]
    head = re.compile(r'(?m)^\[node name="' + re.escape(name) + r'" parent="Game/Collision"[^\n]*\]\n')
    m = head.search(text)
    if m is None:
        return text
    end = text.find("\n[", m.end() - 1)
    end = len(text) if end < 0 else end + 1
    body = re.sub(r"(?m)^transform = [^\n]*\n", "", text[m.end():end])
    line = f'[node name="{name}" parent="Game/Collision" instance=ExtResource("{rid}")]\n'
    return text[:m.start()] + line + body + text[end:]
