# SPDX-License-Identifier: MIT
# Copyright (c) 2026 the OpenRAC contributors
"""Terrain tools: work on a level's terrain mesh (terrain.glb), not only a height map.

    python3 terrain_tools.py info  TERRAIN [--near X Y R]
    python3 terrain_tools.py bake-light TERRAIN [--sun AZ EL] [--region X0 Y0 X1 Y1] [--ao 24]
                             [--bounce 1] [--sun-samples 8 --sun-soft 1.5]
    python3 terrain_tools.py uv    TERRAIN --tile 3 [brush]
    python3 terrain_tools.py paint TERRAIN --texture textures/terrain_0003.png [brush] [filters]
    python3 terrain_tools.py sculpt TERRAIN --mode raise|lower|smooth|roughen|flatten --strength S [brush]
    python3 terrain_tools.py rock-add TERRAIN --at X Y Z --radius R --height H [--undercut U --bulge B ...]
    python3 terrain_tools.py build TERRAIN [--step 2] [--budget N] [--collision OUT.glb]
    python3 terrain_tools.py undo  TERRAIN

A brush is --at X Y Z --radius R (a sphere; without it, the whole terrain). Coordinates are the
game's: x and y across, z up. Each change first copies TERRAIN to .terrain-backups/ next to it
(undo takes the newest back). Vertex colours run from 0 to 2 (1 is normal light), as the game's.

A rock recipe (terrain-rocks.json next to TERRAIN) lists rock shapes; build turns them into a
closed mesh with overhangs (naive surface nets over a density field) and puts it in TERRAIN as the
node "Rocks", textured by slope (grass on top, rock on the walls), UV-mapped and ready for a light
bake.
"""

import argparse
import json
import math
import shutil
import struct
import sys
import time
from pathlib import Path

# The release brings numpy for the launcher's Python in vendor/ (build/package.py).
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
try:
    import numpy as np
except ImportError:
    raise SystemExit("The terrain tools could not load their numpy: download OpenRAC Editor again "
                     "from the launcher (it comes with it)")

BACKUPS = ".terrain-backups"
RECIPE = "terrain-rocks.json"
ROCKS_NODE = "Rocks"

# ---------------------------------------------------------------- glb in and out

_CT = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


class Prim:
    """One primitive: a material and its triangles. Arrays are per vertex; faces index them."""

    def __init__(self, material, attrs, faces):
        self.material = material      # index into Terrain.materials
        self.attrs = attrs            # name -> float array (N, k)
        self.faces = faces            # (F, 3) int

    @property
    def pos(self):
        return self.attrs["POSITION"]


class Terrain:
    """A glb read whole: nodes with meshes of primitives, materials and images kept as they were."""

    def __init__(self, path):
        self.path = Path(path)
        data = self.path.read_bytes()
        if data[:4] != b"glTF":
            raise SystemExit(f"{path}: not a .glb")
        length = struct.unpack("<I", data[12:16])[0]
        j = json.loads(data[20:20 + length])
        blob = data[20 + length + 8:] if len(data) > 20 + length else b""
        self.json = j

        def acc(i):
            a = j["accessors"][i]
            v = j["bufferViews"][a["bufferView"]]
            n = _NC[a["type"]]
            dt = _CT[a["componentType"]]
            off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
            stride = v.get("byteStride", 0)
            size = np.dtype(dt).itemsize * n
            if stride and stride != size:
                raw = np.frombuffer(blob, np.uint8, a["count"] * stride, off).reshape(a["count"], stride)
                out = raw[:, :size].copy().view(dt).reshape(a["count"], n)
            else:
                out = np.frombuffer(blob, dt, a["count"] * n, off).reshape(a["count"], n)
            if a.get("normalized") and dt != np.float32:
                out = out.astype(np.float64) / np.iinfo(dt).max
            return out.astype(np.float64)

        self.materials = j.get("materials", [])
        self.textures = j.get("textures", [])
        self.images = j.get("images", [])
        # Pictures stored in the glb itself: their bytes are kept and written back on save.
        self.image_bytes = {}
        for i, im in enumerate(self.images):
            if "bufferView" in im:
                v = j["bufferViews"][im["bufferView"]]
                self.image_bytes[i] = bytes(blob[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]])
        self.samplers = j.get("samplers", [])
        self.nodes = []   # [name, translation, [Prim]]
        for node in j.get("nodes", []):
            if "mesh" not in node:
                continue
            prims = []
            for p in j["meshes"][node["mesh"]]["primitives"]:
                attrs = {k: acc(v) for k, v in p["attributes"].items()}
                faces = acc(p["indices"]).astype(np.int64).reshape(-1, 3)
                if "COLOR_0" not in attrs:
                    attrs["COLOR_0"] = np.ones((len(attrs["POSITION"]), 4))
                elif attrs["COLOR_0"].shape[1] == 3:
                    attrs["COLOR_0"] = np.concatenate([attrs["COLOR_0"], np.ones((len(attrs["COLOR_0"]), 1))], 1)
                prims.append(Prim(p.get("material", 0), attrs, faces))
            self.nodes.append([node.get("name", f"Node_{len(self.nodes)}"), node.get("translation"), prims])

    # Materials by picture: paint names a texture file; a new one gets a material of its own.
    def texture_uri(self, material):
        m = self.materials[material]
        t = m.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if t is None:
            return None
        return self.images[self.textures[t["index"]]["source"]].get("uri")

    def material_for(self, uri):
        for i in range(len(self.materials)):
            if self.texture_uri(i) == uri:
                return i
        self.images.append({"uri": uri})
        if not self.samplers:
            self.samplers.append({"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497})
        self.textures.append({"source": len(self.images) - 1, "sampler": 0})
        self.materials.append({"name": Path(uri).stem, "doubleSided": True, "pbrMetallicRoughness": {
            "baseColorTexture": {"index": len(self.textures) - 1}, "metallicFactor": 0, "roughnessFactor": 1}})
        return len(self.materials) - 1

    def prims(self):
        for name, tr, prims in self.nodes:
            off = np.array(tr if tr else [0, 0, 0], np.float64)
            for p in prims:
                yield name, off, p

    def save(self, path=None):
        path = Path(path or self.path)
        blob = bytearray()
        views, accessors, meshes, nodes = [], [], [], []

        def put(arr, kind, comp, target):
            arr = np.ascontiguousarray(arr.astype(_CT[comp]))
            while len(blob) % 4:
                blob.append(0)
            views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes, "target": target})
            blob.extend(arr.tobytes())
            a = {"bufferView": len(views) - 1, "componentType": comp, "count": int(arr.shape[0]), "type": kind}
            if kind == "VEC3" and comp == 5126:
                a["min"] = arr.min(0).tolist()
                a["max"] = arr.max(0).tolist()
            accessors.append(a)
            return len(accessors) - 1

        kinds = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}
        for name, tr, prims in self.nodes:
            out = []
            for p in prims:
                if len(p.faces) == 0:
                    continue
                attrs = {}
                for k, v in p.attrs.items():
                    if k == "COLOR_0":
                        v = np.clip(v, 0, 2)
                    attrs[k] = put(v, kinds[v.shape[1]], 5126, 34962)
                idx = put(p.faces.reshape(-1, 1), "SCALAR", 5125, 34963)
                out.append({"attributes": attrs, "indices": idx, "material": int(p.material)})
            if not out:
                continue
            meshes.append({"primitives": out})
            node = {"name": name, "mesh": len(meshes) - 1}
            if tr:
                node["translation"] = list(tr)
            nodes.append(node)
        images = []
        for i, im in enumerate(self.images):
            im = dict(im)
            if i in getattr(self, "image_bytes", {}):
                while len(blob) % 4:
                    blob.append(0)
                data = self.image_bytes[i]
                views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
                blob.extend(data)
                im["bufferView"] = len(views) - 1
            images.append(im)
        j = dict(self.json)
        j.update({"scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes, "meshes": meshes,
                  "materials": self.materials, "textures": self.textures, "images": images,
                  "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}]})
        if self.samplers:
            j["samplers"] = self.samplers
        js = json.dumps(j, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        while len(blob) % 4:
            blob.append(0)
        total = 12 + 8 + len(js) + 8 + len(blob)
        out = struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(js), 0x4E4F534A) + js
        out += struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
        tmp = path.with_suffix(".glb.tmp")
        tmp.write_bytes(out)
        tmp.replace(path)


def backup(path):
    path = Path(path)
    folder = path.parent / BACKUPS
    folder.mkdir(exist_ok=True)
    dest = folder / f"{path.stem}-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000:06}.glb"
    shutil.copyfile(path, dest)
    old = sorted(folder.glob(f"{path.stem}-*.glb"))
    for extra in old[:-20]:            # the last 20 changes
        extra.unlink()
    return dest


def undo(path):
    path = Path(path)
    old = sorted((path.parent / BACKUPS).glob(f"{path.stem}-*.glb"))
    if not old:
        raise SystemExit("no terrain backup to go back to")
    shutil.copyfile(old[-1], path)
    old[-1].unlink()
    return {"restored": old[-1].name, "left": len(old) - 1}


# ---------------------------------------------------------------- brushes and filters

def face_data(p, off):
    P = p.pos + off
    a, b, c = P[p.faces[:, 0]], P[p.faces[:, 1]], P[p.faces[:, 2]]
    n = np.cross(b - a, c - a)
    area = np.linalg.norm(n, axis=1) / 2
    n = n / np.maximum(2 * area, 1e-12)[:, None]
    return (a + b + c) / 3, n, area


def brush_weight(points, args):
    """Falloff 1 in the middle to 0 at the edge (smooth); 1 everywhere without a brush."""
    if getattr(args, "at", None) is None:
        return np.ones(len(points))
    d = np.linalg.norm(points - np.array(args.at), axis=1) / args.radius
    t = np.clip(1 - d, 0, 1)
    hard = getattr(args, "hardness", 0.5)
    t = np.clip(t / max(1 - hard, 1e-3), 0, 1)
    return t * t * (3 - 2 * t)


def face_filter(centre, n, args):
    keep = np.ones(len(centre), bool)
    up = n[:, 2]
    slope = np.degrees(np.arccos(np.clip(up, -1, 1)))     # 0 flat, 90 wall, 180 ceiling
    if getattr(args, "slope_min", None) is not None:
        keep &= slope >= args.slope_min
    if getattr(args, "slope_max", None) is not None:
        keep &= slope <= args.slope_max
    if getattr(args, "z_min", None) is not None:
        keep &= centre[:, 2] >= args.z_min
    if getattr(args, "z_max", None) is not None:
        keep &= centre[:, 2] <= args.z_max
    return keep


# ---------------------------------------------------------------- info

def info(t, near=None):
    A, N, E, C, U, T = [], [], [], [], [], {}
    for name, off, p in t.prims():
        centre, n, area = face_data(p, off)
        keep = area > 1e-6
        if near is not None:
            keep &= np.hypot(centre[:, 0] - near[0], centre[:, 1] - near[1]) < near[2]
        P = p.pos
        E.append(np.linalg.norm(P[p.faces[:, 1]] - P[p.faces[:, 0]], axis=1)[keep])
        A.append(area[keep]); N.append(n[keep])
        col = p.attrs["COLOR_0"][:, :3]
        C.append(col[p.faces].mean(1)[keep])
        uv = p.attrs.get("TEXCOORD_0")
        if uv is not None:
            d1 = uv[p.faces[:, 1]] - uv[p.faces[:, 0]]
            d2 = uv[p.faces[:, 2]] - uv[p.faces[:, 0]]
            uar = np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / 2
            U.append(np.sqrt(uar[keep] / np.maximum(area[keep], 1e-9)))
        uri = t.texture_uri(p.material) or f"material {p.material}"
        T[uri] = T.get(uri, 0) + float(area[keep].sum())
    if not A or not sum(len(a) for a in A):
        return {"triangles": 0}
    A = np.concatenate(A); N = np.concatenate(N); E = np.concatenate(E); C = np.concatenate(C)
    lum = C.mean(1)
    nz = N[:, 2]
    total = float(A.sum())
    out = {
        "triangles": int(len(A)), "area_m2": round(total), "triangles_per_100m2": round(len(A) / total * 100, 1),
        "median_edge": round(float(np.median(E)), 2),
        "flat": round(float(A[nz > 0.9].sum() / total), 2), "slope": round(float(A[(nz <= 0.9) & (nz >= 0.3)].sum() / total), 2),
        "wall": round(float(A[np.abs(nz) < 0.3].sum() / total), 2), "overhang": round(float(A[nz < -0.3].sum() / total), 2),
        "light_mean": round(float(lum.mean()), 2), "light_spread": round(float(lum.std()), 2),
        "in_shadow": round(float(A[lum < 0.4].sum() / total), 2),
        "textures": len(T), "top_textures": sorted(((k, round(v / total, 3)) for k, v in T.items()), key=lambda x: -x[1])[:10],
        "nodes": [n for n, _, _ in t.nodes],
    }
    if U:
        u = np.concatenate(U)
        out["texture_tile_units"] = round(float(1 / max(np.median(u), 1e-9)), 1)
    out["advice"] = advice(out)
    return out


def advice(s):
    """Plain hints against Pokitaru's own numbers (see docs/TERRAIN.md)."""
    tips = []
    if s.get("triangles_per_100m2", 99) < 25:
        tips.append("sparse: Pokitaru has about 54 triangles per 100 m2 on its land")
    if s.get("flat", 0) > 0.3:
        tips.append("much flat ground: Pokitaru has 17%")
    if s.get("overhang", 1) < 0.05:
        tips.append("no overhangs: add rocks with an undercut (rock-add --undercut) and build")
    if s.get("light_spread", 1) < 0.3:
        tips.append("flat light: run bake-light")
    if s.get("texture_tile_units", 0) > 5:
        tips.append("blurry textures: uv --tile 3")
    return tips


# ---------------------------------------------------------------- UVs: box projection per face

def box_uv(t, args):
    tile = args.tile
    changed = 0
    for name, off, p in t.prims():
        centre, n, area = face_data(p, off)
        sel = (brush_weight(centre, args) > 0) & face_filter(centre, n, args)
        if not sel.any():
            continue
        axis = np.argmax(np.abs(n), axis=1)
        # A vertex shared by faces of different axes is split, so each face keeps its own mapping.
        faces = p.faces.copy()
        keymap = {}
        new_index = []
        nv = len(p.pos)
        for f in np.nonzero(sel)[0]:
            for k in range(3):
                v = faces[f, k]
                key = (v, int(axis[f]))
                idx = keymap.get(key)
                if idx is None:
                    if (v, -1) not in keymap:
                        keymap[(v, -1)] = True
                        idx = v
                    else:
                        idx = nv + len(new_index)
                        new_index.append(v)
                    keymap[key] = idx
                faces[f, k] = idx
        if new_index:
            for k in list(p.attrs):
                p.attrs[k] = np.concatenate([p.attrs[k], p.attrs[k][new_index]])
        p.faces = faces
        P = p.pos + off
        uv = p.attrs.setdefault("TEXCOORD_0", np.zeros((len(P), 2)))
        for ax in range(3):
            fs = sel & (axis == ax)
            vs = np.unique(faces[fs].reshape(-1))
            if not len(vs):
                continue
            q = P[vs]
            if ax == 2:      # floors and ceilings: x, y
                uv[vs] = np.stack([q[:, 0], -q[:, 1]], 1) / tile
            elif ax == 0:    # walls facing x: y across, z up (pictures stand upright)
                uv[vs] = np.stack([q[:, 1], -q[:, 2]], 1) / tile
            else:
                uv[vs] = np.stack([q[:, 0], -q[:, 2]], 1) / tile
        changed += int(sel.sum())
    return {"faces": changed, "tile": tile}


# ---------------------------------------------------------------- paint: a texture per face

def paint(t, args):
    uri = args.texture
    target = t.material_for(uri)
    rng = np.random.default_rng(args.seed)
    changed = 0
    for name, off, prims in t.nodes:
        add = []
        for p in prims:
            if p.material == target:
                continue
            centre, n, area = face_data(p, np.array(off or [0, 0, 0], float))
            w = brush_weight(centre, args) * args.strength
            sel = face_filter(centre, n, args) & (rng.random(len(w)) < w)
            if args.noise:
                sel &= value_noise3(centre / args.noise, args.seed) > 0.5 - args.coverage / 2
            if not sel.any():
                continue
            moved = p.faces[sel]
            p.faces = p.faces[~sel]
            used, inverse = np.unique(moved.reshape(-1), return_inverse=True)
            add.append(Prim(target, {k: v[used] for k, v in p.attrs.items()}, inverse.reshape(-1, 3)))
            changed += int(sel.sum())
        prims.extend(add)
        merge_same_material(prims)
    return {"faces": changed, "texture": uri}


def merge_same_material(prims):
    by = {}
    for p in list(prims):
        if p.material in by:
            q = by[p.material]
            base = len(q.pos)
            keys = set(q.attrs) & set(p.attrs)
            q.attrs = {k: np.concatenate([q.attrs[k], p.attrs[k]]) for k in keys}
            q.faces = np.concatenate([q.faces, p.faces + base])
            prims.remove(p)
        else:
            by[p.material] = p
    for p in list(prims):
        if len(p.faces) == 0:
            prims.remove(p)


# ---------------------------------------------------------------- sculpt

def weld(prims, off):
    """Vertices of all primitives at the same place, as one index (for smoothing across seams)."""
    allp = np.concatenate([p.pos + off for p in prims])
    key = np.round(allp * 64).astype(np.int64)
    _, ids, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.reshape(-1)
    return allp[ids], inverse


def sculpt(t, args):
    moved = 0
    for name, tr, prims in t.nodes:
        off = np.array(tr or [0, 0, 0], float)
        if not prims:
            continue
        U, inverse = weld(prims, off)
        w = brush_weight(U, args) * args.strength
        if not (w > 0).any():
            continue
        # Normals and neighbours on the welded mesh.
        starts = np.cumsum([0] + [len(p.pos) for p in prims])
        F = np.concatenate([inverse[p.faces + s] for p, s in zip(prims, starts[:-1])])
        a, b, c = U[F[:, 0]], U[F[:, 1]], U[F[:, 2]]
        fn = np.cross(b - a, c - a)
        VN = np.zeros_like(U)
        for k in range(3):
            np.add.at(VN, F[:, k], fn)
        VN /= np.maximum(np.linalg.norm(VN, axis=1), 1e-9)[:, None]
        if args.mode in ("raise", "lower"):
            d = VN * (w * (1 if args.mode == "raise" else -1))[:, None]
        elif args.mode == "smooth":
            S = np.zeros_like(U); cnt = np.zeros(len(U))
            for i, j in ((0, 1), (1, 2), (2, 0), (1, 0), (2, 1), (0, 2)):
                np.add.at(S, F[:, i], U[F[:, j]]); np.add.at(cnt, F[:, i], 1)
            avg = S / np.maximum(cnt, 1)[:, None]
            d = (avg - U) * np.clip(w, 0, 1)[:, None]
        elif args.mode == "roughen":
            nz = value_noise3(U / max(args.radius / 6 if args.at else 4.0, 0.5), args.seed) - 0.5
            d = VN * (nz * 2 * w)[:, None]
        elif args.mode == "flatten":
            z = args.at[2] if args.at else float(np.median(U[:, 2]))
            d = np.zeros_like(U); d[:, 2] = (z - U[:, 2]) * np.clip(w, 0, 1)
        else:
            raise SystemExit(f"unknown mode {args.mode}")
        U2 = U + d
        moved += int((np.linalg.norm(d, axis=1) > 1e-4).sum())
        for p, s in zip(prims, starts[:-1]):
            p.attrs["POSITION"] = U2[inverse[s:s + len(p.pos)]] - off
            recompute_normals(p)
    return {"vertices": moved, "mode": args.mode}


def recompute_normals(p):
    P = p.pos
    a, b, c = P[p.faces[:, 0]], P[p.faces[:, 1]], P[p.faces[:, 2]]
    fn = np.cross(b - a, c - a)
    VN = np.zeros_like(P)
    for k in range(3):
        np.add.at(VN, p.faces[:, k], fn)
    p.attrs["NORMAL"] = VN / np.maximum(np.linalg.norm(VN, axis=1), 1e-9)[:, None]


# ---------------------------------------------------------------- 3D value noise

def value_noise3(q, seed=0, octaves=1):
    q = np.asarray(q, np.float64)
    out = np.zeros(len(q))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        s = q * (2 ** o)
        i = np.floor(s).astype(np.int64)
        f = s - i
        f = f * f * (3 - 2 * f)

        def h(dx, dy, dz):
            x = (i[:, 0] + dx) * 73856093 ^ (i[:, 1] + dy) * 19349663 ^ (i[:, 2] + dz) * 83492791 ^ (seed + o) * 2654435761
            x = (x ^ (x >> 13)) * 1274126177
            return ((x ^ (x >> 16)) & 0xFFFF) / 65535.0
        v = 0
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    wx = f[:, 0] if dx else 1 - f[:, 0]
                    wy = f[:, 1] if dy else 1 - f[:, 1]
                    wz = f[:, 2] if dz else 1 - f[:, 2]
                    v = v + h(dx, dy, dz) * wx * wy * wz
        out += v * amp
        total += amp
        amp *= 0.5
    return out / total


# ---------------------------------------------------------------- light bake

def splat_depth(points, M, lo, cell, shape, values=None):
    """Depth map (smallest depth along the view) of POINTS seen through rotation M; with VALUES (one
    row per point), also the map of the nearest point's values."""
    q = points @ M.T
    ij = np.floor((q[:, :2] - lo) / cell).astype(np.int64)
    ok = (ij[:, 0] >= 0) & (ij[:, 1] >= 0) & (ij[:, 0] < shape[0]) & (ij[:, 1] < shape[1])
    depth = np.full(shape, np.inf)
    flat = ij[ok, 0] * shape[1] + ij[ok, 1]
    np.minimum.at(depth.reshape(-1), flat, q[ok, 2])
    if values is None:
        return depth
    seen = np.zeros((shape[0] * shape[1], values.shape[1]))
    front = q[ok, 2] <= depth.reshape(-1)[flat]
    seen[flat[front]] = values[ok][front]
    return depth, seen


def view_matrix(d):
    """Rotation whose third row is the view direction D (looking along +D)."""
    d = d / np.linalg.norm(d)
    up = np.array([0, 0, 1.0]) if abs(d[2]) < 0.95 else np.array([1.0, 0, 0])
    x = np.cross(up, d); x /= np.linalg.norm(x)
    y = np.cross(d, x)
    return np.stack([x, y, d])


def surface_points(tris, spacing, rng, with_faces=False):
    """Points spread over triangles (about one per SPACING squared), for depth maps; WITH_FACES:
    also the triangle each comes from."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    area = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
    count = np.maximum(1, np.round(area / (spacing * spacing) * 2)).astype(np.int64)
    count = np.minimum(count, 400)
    idx = np.repeat(np.arange(len(tris)), count)
    r1 = np.sqrt(rng.random(len(idx))); r2 = rng.random(len(idx))
    pts = a[idx] * (1 - r1)[:, None] + b[idx] * (r1 * (1 - r2))[:, None] + c[idx] * (r1 * r2)[:, None]
    out = np.concatenate([pts, a, b, c])
    if with_faces:
        every = np.arange(len(tris))
        return out, np.concatenate([idx, every, every, every])
    return out


def occluded(points, normals, occ_pts, d, region, cell, bias, values=None):
    """1 where something in OCC_PTS stands between POINTS and the far end of direction -D
    (light coming along D), soft over a 3x3 neighbourhood. With VALUES (one row per point of
    OCC_PTS): also the values of what stands in the way, averaged over the cells it covers."""
    M = view_matrix(d)
    corners = np.array([[region[0], region[1], region[4]], [region[2], region[1], region[4]],
                        [region[0], region[3], region[4]], [region[2], region[3], region[4]],
                        [region[0], region[1], region[5]], [region[2], region[1], region[5]],
                        [region[0], region[3], region[5]], [region[2], region[3], region[5]]]) @ M.T
    lo = corners[:, :2].min(0) - cell * 2
    hi = corners[:, :2].max(0) + cell * 2
    shape = tuple(np.ceil((hi - lo) / cell).astype(int) + 1)
    if shape[0] * shape[1] > 30_000_000:
        raise SystemExit("bake region too big for this cell size: give --region or a larger --cell")
    if values is None:
        depth = splat_depth(occ_pts, M, lo, cell, shape)
    else:
        depth, seen = splat_depth(occ_pts, M, lo, cell, shape, values)
        gathered = np.zeros((len(points), values.shape[1]))
    q = (points + normals * bias) @ M.T
    # Slope-scaled bias: a face the light grazes changes depth fast from cell to cell, and its
    # neighbour cells would shade it (blotches on roofs and slopes).
    c = np.clip(normals @ -(d / np.linalg.norm(d)), 0.15, 1.0)
    slack = bias + cell * 1.5 * np.sqrt(1 - c * c) / c
    f = (q[:, :2] - lo) / cell
    i0 = np.floor(f).astype(np.int64)
    shade = np.zeros(len(points))
    n = 0
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            ii = np.clip(i0[:, 0] + dx, 0, shape[0] - 1)
            jj = np.clip(i0[:, 1] + dy, 0, shape[1] - 1)
            hit = depth[ii, jj] < q[:, 2] - slack
            shade += hit
            if values is not None:
                gathered += seen[ii * shape[1] + jj] * hit[:, None]
            n += 1
    if values is not None:
        return shade / n, gathered / np.maximum(shade, 1)[:, None]
    return shade / n


def hemisphere(count, seed=3):
    rng = np.random.default_rng(seed)
    out = []
    golden = math.pi * (3 - math.sqrt(5))
    for k in range(count):
        z = 1 - (k + 0.5) / count          # cosine-ish: more near the top
        z = math.sqrt(z)
        r = math.sqrt(max(0, 1 - z * z))
        a = k * golden + rng.uniform(0, 0.2)
        out.append([r * math.cos(a), r * math.sin(a), z])
    return np.array(out)


def all_prims(ts):
    for t in ts:
        yield from t.prims()


def bake_light(t, args, others=()):
    """Sun with shadows, sky occlusion, bounce and point lights into the vertex colours of T and
    OTHERS (models baked with the terrain: each shades the others). A primitive's KEEP mask (a
    numpy bool per vertex, set by the caller) marks glowing vertices left as they are."""
    ts = [t] + list(others)
    rng = np.random.default_rng(1)
    sun_az, sun_el = args.sun
    sun = np.array([math.cos(math.radians(sun_el)) * math.cos(math.radians(sun_az)),
                    math.cos(math.radians(sun_el)) * math.sin(math.radians(sun_az)), math.sin(math.radians(sun_el))])
    tris, tri_albedo = [], []
    verts, norms = [], []
    for name, off, p in all_prims(ts):
        P = p.pos + off
        tris.append(P[p.faces])
        recompute_normals(p) if "NORMAL" not in p.attrs else None
        verts.append(P); norms.append(p.attrs["NORMAL"])
        # How much light a face sends on (for the bounce): the caller's albedo per vertex, else
        # a mid grey times the colour there now.
        alb = getattr(p, "albedo", None)
        if alb is None:
            alb = 0.45 * np.clip(p.attrs["COLOR_0"][:, :3], 0, 2) if "COLOR_0" in p.attrs else np.full((len(P), 3), 0.45)
        tri_albedo.append(np.asarray(alb, np.float64)[p.faces].mean(1))
    tris = np.concatenate(tris)
    tri_albedo = np.clip(np.concatenate(tri_albedo), 0, 0.95)
    V = np.concatenate(verts); N = np.concatenate(norms)
    if args.region:
        x0, y0, x1, y1 = args.region
    else:
        x0, y0 = np.percentile(V[:, 0], 2), np.percentile(V[:, 1], 2)
        x1, y1 = np.percentile(V[:, 0], 98), np.percentile(V[:, 1], 98)
    inside = (V[:, 0] >= x0) & (V[:, 0] <= x1) & (V[:, 1] >= y0) & (V[:, 1] <= y1)
    region = [x0, y0, x1, y1, float(V[inside, 2].min()) if inside.any() else 0, float(V[inside, 2].max()) if inside.any() else 1]
    tc = tris.mean(1)
    near = (tc[:, 0] > x0 - 60) & (tc[:, 0] < x1 + 60) & (tc[:, 1] > y0 - 60) & (tc[:, 1] < y1 + 60)
    bounce = float(getattr(args, "bounce", 0.0) or 0.0)
    if bounce > 0:
        occ, occ_face = surface_points(tris[near], args.cell * 0.7, rng, with_faces=True)
    else:
        occ = surface_points(tris[near], args.cell * 0.7, rng)
    lit_sun = np.clip(N @ sun, 0, 1)
    sun_col = np.array(args.sun_colour)
    sky_col = np.array(args.sky_colour)
    bounce_col = np.array(args.bounce_colour)
    # A soft sun: its shadow is the mean of SUN_SAMPLES directions across a disc SUN_SOFT degrees
    # wide, so a shadow's edge blurs with its distance from what casts it.
    samples = max(1, int(getattr(args, "sun_samples", 1) or 1))
    soft = math.radians(float(getattr(args, "sun_soft", 0.0) or 0.0))
    side = np.cross(sun, [0.0, 0.0, 1.0]); side /= np.linalg.norm(side)
    up = np.cross(side, sun)
    suns = []
    for k in range(samples):
        if samples == 1 or soft <= 0:
            suns.append(sun)
            break
        r = soft * math.sqrt((k + 0.5) / samples)
        a = k * math.pi * (3 - math.sqrt(5))
        v = sun + side * math.tan(r) * math.cos(a) + up * math.tan(r) * math.sin(a)
        suns.append(v / np.linalg.norm(v))
    shadow = np.zeros(len(V))
    ao = np.zeros(len(V))
    indirect = np.zeros((len(V), 3))
    if inside.any():
        for s_dir in suns:
            shadow[inside] += occluded(V[inside], N[inside], occ, -s_dir, region, args.cell, args.bias) / len(suns)
        values = None
        if bounce > 0:
            # One bounce: what each sample of the surfaces sends on (its sun and sky light times its
            # albedo), gathered from every sky direction it stands in.
            face_n = np.cross(tris[near][:, 1] - tris[near][:, 0], tris[near][:, 2] - tris[near][:, 0])
            face_n /= np.maximum(np.linalg.norm(face_n, axis=1), 1e-9)[:, None]
            on = face_n[occ_face]
            occ_shadow = occluded(occ, on, occ, -sun, region, args.cell, args.bias)
            sent = (sun_col[None] * (np.clip(on @ sun, 0, 1) * (1 - occ_shadow))[:, None]
                    + sky_col[None] * (0.35 + 0.35 * np.clip(on[:, 2], 0, 1))[:, None])
            values = sent * tri_albedo[near][occ_face]
        dirs = hemisphere(args.ao)
        weight = 0
        for d in dirs:
            vis = np.clip(N[inside] @ d, 0, 1)
            got = occluded(V[inside], N[inside], occ, -d, region, args.cell * 1.5, args.bias * 1.5, values)
            if values is not None:
                got, seen = got
                indirect[inside] += seen * (got * vis)[:, None]
            ao[inside] += got * vis
            weight += vis
        ao[inside] = ao[inside] / np.maximum(weight, 1e-6)
        indirect[inside] = indirect[inside] / np.maximum(weight, 1e-6)[:, None]
    sky_open = 1 - ao
    # Light: sun (warm) where not in shadow, sky (cool) by how open it is, and light bounced off
    # what stands round (gathered, with BOUNCE; else a warm guess from below).
    light = (sun_col[None] * (lit_sun * (1 - shadow))[:, None]
             + sky_col[None] * (sky_open * (0.55 + 0.45 * np.clip(N[:, 2], 0, 1)))[:, None])
    if bounce > 0:
        light = light + bounce * indirect
    else:
        light = light + bounce_col[None] * (np.clip(-N[:, 2], 0, 1) * 0.5 + 0.15)[:, None] * sky_open[:, None]
    light = light + getattr(args, "ambient", 0.0)       # a floor, so no wall goes black
    lights = getattr(args, "lights", None) or []
    if lights:
        light = light + point_lights(V, N, occ, lights, args.cell)
    light = np.clip(light * args.strength, 0, 2)
    k = 0
    for name, off, p in all_prims(ts):
        n = len(p.pos)
        col = p.attrs["COLOR_0"]
        new = light[k:k + n]
        if args.keep_tint:
            new = new * np.clip(col[:, :3], 0, 2)
        col = np.concatenate([new, col[:, 3:4]], 1)
        m = inside[k:k + n] if args.region else np.ones(n, bool)
        keep = getattr(p, "keep", None)
        if keep is not None:
            m = m & ~keep
        p.attrs["COLOR_0"][m] = col[m]
        k += n
    return {"vertices": int(inside.sum()), "in_shadow": round(float((shadow[inside] > 0.5).mean()), 2) if inside.any() else 0,
            "mean_ao": round(float(ao[inside].mean()), 2) if inside.any() else 0, "region": [round(v, 1) for v in region],
            "lights": len(lights)}


def bake_models(t, args):
    """bake-light with its --models (subdivided, glowing parts kept, tinted) and --lights."""
    if isinstance(getattr(args, "lights", None), str):
        args.lights = json.loads(Path(args.lights).read_text(encoding="utf-8"))
    models = []
    for m in getattr(args, "models", None) or []:
        backup(m)
        mt = Terrain(m)
        for name, off, p in mt.prims():
            if args.glow:
                p.keep = p.attrs["COLOR_0"][:, :3].max(1) > args.glow
            if args.subdivide:
                subdivide(p, args.subdivide)
            if args.model_tint:
                c = p.attrs["COLOR_0"]
                k = getattr(p, "keep", np.zeros(len(c), bool))
                c[~k, :3] = args.model_tint
        models.append(mt)
    result = bake_light(t, args, models)
    for mt in models:
        mt.save()
    result["models"] = [str(m.path) for m in models]
    return result


def point_lights(V, N, occ, lights, cell):
    """Warm pools of light from LIGHTS ({"at": [x, y, z], "colour": [r, g, b], "radius": r}): falling
    off to nothing at the radius, blocked by whatever stands between (a voxel grid of OCC)."""
    out = np.zeros((len(V), 3))
    if not lights:
        return out
    vox = max(cell * 0.8, 0.35)
    at = np.array([l["at"] for l in lights], float)
    rad = np.array([l.get("radius", 8.0) for l in lights], float)
    lo = at.min(0) - rad.max() - 1
    hi = at.max(0) + rad.max() + 1
    m = np.all((occ >= lo) & (occ <= hi), axis=1)
    key = np.floor((occ[m] - lo) / vox).astype(np.int64)
    dims = np.floor((hi - lo) / vox).astype(np.int64) + 2
    solid = np.unique((key[:, 0] * dims[1] + key[:, 1]) * dims[2] + key[:, 2])

    def blocked(q):
        k = np.floor((q - lo) / vox).astype(np.int64)
        flat = (k[:, 0] * dims[1] + k[:, 1]) * dims[2] + k[:, 2]
        i = np.searchsorted(solid, flat)
        i = np.clip(i, 0, len(solid) - 1)
        return solid[i] == flat if len(solid) else np.zeros(len(q), bool)

    for l, c, r in zip(at, lights, rad):
        d = l - V
        dist = np.linalg.norm(d, axis=1)
        near = np.nonzero((dist < r) & (dist > 1e-3))[0]
        if not len(near):
            continue
        L = d[near] / dist[near, None]
        facing = np.clip((N[near] * L).sum(1), 0, 1) * 0.8 + 0.2
        fall = (1 - dist[near] / r) ** 2
        vis = np.ones(len(near))
        start = V[near] + N[near] * vox * 1.2
        span = l - start
        length = np.linalg.norm(span, axis=1)
        steps = int(np.ceil(length.max() / vox)) if len(length) else 0
        for t in np.linspace(0.0, 1.0, max(steps, 2))[:-1]:
            if t * length.max() < vox * 0.5:
                continue
            q = start + span * t
            vis *= ~(blocked(q) & (length * (1 - t) > vox * 1.5))
        out[near] += np.array(c.get("colour", [1.6, 1.1, 0.6]))[None] * (fall * facing * vis)[:, None]
    return out


def subdivide(p, size):
    """Splits P's triangles into pieces no longer than SIZE on a side (so vertex light shows
    shadows on big walls and floors). Attributes are blended linearly."""
    F = p.faces
    P = p.pos
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    longest = np.max([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1)], 0)
    n = np.clip(np.ceil(longest / size), 1, 24).astype(int)
    keep = n == 1
    new_attrs = {k: [v] for k, v in p.attrs.items()}
    new_faces = [F[keep]]
    mask = getattr(p, "keep", None)
    new_keep = [mask] if mask is not None else None
    base = len(P)
    for level in np.unique(n[~keep]):
        sel = np.nonzero(n == level)[0]
        # Barycentric grid of the level: points (i, j) with i + j <= level.
        pts = [(i, j) for i in range(level + 1) for j in range(level + 1 - i)]
        index = {q: k for k, q in enumerate(pts)}
        w = np.array([[1 - (i + j) / level, i / level, j / level] for i, j in pts])
        tri = []
        for i in range(level):
            for j in range(level - i):
                tri.append((index[(i, j)], index[(i + 1, j)], index[(i, j + 1)]))
                if i + j < level - 1:
                    tri.append((index[(i + 1, j)], index[(i + 1, j + 1)], index[(i, j + 1)]))
        tri = np.array(tri)
        for k, v in p.attrs.items():
            corners = v[F[sel]]                               # (T, 3, k)
            new_attrs[k].append(np.einsum("pc,tck->tpk", w, corners).reshape(-1, v.shape[1]))
        offs = base + np.arange(len(sel))[:, None, None] * len(pts)
        new_faces.append((tri[None] + offs).reshape(-1, 3))
        if new_keep is not None:      # a new vertex glows when its face's first corner does
            new_keep.append(np.repeat(mask[F[sel, 0]], len(pts)))
        base += len(sel) * len(pts)
    p.attrs = {k: np.concatenate(v) for k, v in new_attrs.items()}
    if "NORMAL" in p.attrs:
        nn = p.attrs["NORMAL"]
        p.attrs["NORMAL"] = nn / np.maximum(np.linalg.norm(nn, axis=1), 1e-9)[:, None]
    p.faces = np.concatenate(new_faces)
    if new_keep is not None:
        p.keep = np.concatenate(new_keep)
    # Drop the corners only the split faces used.
    used = np.zeros(len(p.pos), bool)
    used[p.faces.ravel()] = True
    remap = np.cumsum(used) - 1
    p.attrs = {k: v[used] for k, v in p.attrs.items()}
    p.faces = remap[p.faces]
    if new_keep is not None:
        p.keep = p.keep[used]
    return int(len(p.faces))



# ---------------------------------------------------------------- rocks: density field to mesh

def rock_density(P, rock):
    """Positive inside a rock. A rock stands on z0 (its foot, under the sea), up to z1 (its top);
    its radius bulges in the middle and is cut in at the foot (the undercut), its top rounded,
    its side broken by noise and by horizontal layers like Pokitaru's."""
    x, y, z0, z1 = rock["x"], rock["y"], rock["z0"], rock["z1"]
    R = rock["radius"]
    seed = rock.get("seed", 0)
    t = np.clip((P[:, 2] - z0) / max(z1 - z0, 1e-3), -0.2, 1.2)
    under = rock.get("undercut", 0.3)
    bulge = rock.get("bulge", 0.15)
    taper = rock.get("taper", 0.2)
    # Profile: narrow foot (undercut), wide shoulder at 70%, rounded top.
    prof = (1 - under * np.clip(1 - t / 0.6, 0, 1) ** 1.5) * (1 + bulge * np.exp(-((t - 0.72) / 0.18) ** 2)) * (1 - taper * t)
    prof = prof * np.where(t > 0.9, np.sqrt(np.clip(1 - ((t - 0.9) / 0.1) ** 2, 0, 1)) * 0.5 + 0.5, 1)
    sx, sy = rock.get("stretch", [1.0, 1.0])
    ang = math.radians(rock.get("yaw", 0))
    # A large, slow warp so no rock is a cylinder: its outline wanders by WARP of its radius.
    warp = rock.get("warp", 0.35) * R
    wq = P / (R * 1.3)
    dx = P[:, 0] - x + (value_noise3(wq, seed + 11, 2) - 0.5) * 2 * warp
    dy = P[:, 1] - y + (value_noise3(wq, seed + 23, 2) - 0.5) * 2 * warp
    u = (dx * math.cos(ang) + dy * math.sin(ang)) / sx
    v = (-dx * math.sin(ang) + dy * math.cos(ang)) / sy
    r = np.hypot(u, v)
    rough = rock.get("roughness", 0.25)
    n = (value_noise3(P / (R * 0.45), seed, 3) - 0.5) * 2
    layers = np.sin(P[:, 2] / rock.get("layer", 6.0) * math.pi * 2 + n * 2) * rock.get("layering", 0.06)
    d = R * prof * (1 + rough * n + layers) - r
    # Flat-ish top: cap at z1 with a little noise, and the foot ends at z0.
    top = z1 + n * rough * R * 0.25 - P[:, 2]
    foot = P[:, 2] - z0
    return np.minimum(np.minimum(d, top), foot)


def smooth_union(a, b, k):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return a * (1 - h) + b * h + k * h * (1 - h)


def rock_bounds(rock):
    """The box a rock can reach (its bulge, noise and warp included): (lo, hi)."""
    R = rock["radius"] * max(rock.get("stretch", [1.0, 1.0]))
    reach = R * (1.15 * (1 + rock.get("roughness", 0.25) + rock.get("layering", 0.06)) + rock.get("warp", 0.35)) + 1
    top = rock["z1"] + rock.get("roughness", 0.25) * rock["radius"] * 0.25 + 1
    return (np.array([rock["x"] - reach, rock["y"] - reach, rock["z0"] - 1]),
            np.array([rock["x"] + reach, rock["y"] + reach, top]))


def recipe_density(P, recipe):
    """Positive inside the recipe's rocks. Each rock is only worked out inside its own box, so a
    recipe of many rocks over a whole level stays quick."""
    k = recipe.get("blend", 4.0)
    out = np.full(len(P), -1e3)
    for rock in recipe.get("rocks", []):
        lo, hi = rock_bounds(rock)
        m = np.all((P >= lo) & (P <= hi), axis=1)
        if m.any():
            out[m] = smooth_union(out[m], rock_density(P[m], rock), k)
    for cave in recipe.get("caves", []):
        c = np.array(cave["at"], float)
        d = cave["radius"] - np.linalg.norm((P - c) / np.array(cave.get("scale", [1, 1, 0.7])), axis=1)
        out = np.minimum(out, -d)
    return out


def surface_nets(field_fn, lo, hi, step):
    """Naive surface nets: one vertex per cell that the surface crosses, a quad per crossed edge."""
    nx, ny, nz = (np.ceil((np.array(hi) - lo) / step).astype(int) + 1)
    xs = lo[0] + np.arange(nx) * step
    ys = lo[1] + np.arange(ny) * step
    zs = lo[2] + np.arange(nz) * step
    F = np.empty((nx, ny, nz), np.float32)
    for k in range(nz):       # a slice at a time, to keep memory small
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        P = np.stack([X.ravel(), Y.ravel(), np.full(X.size, zs[k])], 1)
        F[:, :, k] = field_fn(P).reshape(nx, ny)
    inside = F > 0
    # Cells: corners' signs.
    c = [inside[i:nx - 1 + i, j:ny - 1 + j, kk:nz - 1 + kk] for i in (0, 1) for j in (0, 1) for kk in (0, 1)]
    any_in = np.logical_or.reduce(c)
    all_in = np.logical_and.reduce(c)
    active = any_in & ~all_in
    cell_index = -np.ones(active.shape, np.int64)
    ids = np.argwhere(active)
    cell_index[active] = np.arange(len(ids))
    # Vertex: mean of edge crossings in the cell.
    corners = np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)])
    edges = [(a, b) for a in range(8) for b in range(a + 1, 8) if np.abs(corners[a] - corners[b]).sum() == 1]
    acc = np.zeros((len(ids), 3)); cnt = np.zeros(len(ids))
    for a, b in edges:
        ca, cb = corners[a], corners[b]
        fa = F[ids[:, 0] + ca[0], ids[:, 1] + ca[1], ids[:, 2] + ca[2]]
        fb = F[ids[:, 0] + cb[0], ids[:, 1] + cb[1], ids[:, 2] + cb[2]]
        cross = (fa > 0) != (fb > 0)
        tt = np.where(cross, fa / np.where(fa - fb == 0, 1, fa - fb), 0)
        p = (ids + ca) + (cb - ca) * tt[:, None]
        acc[cross] += p[cross]; cnt[cross] += 1
    V = acc / np.maximum(cnt, 1)[:, None]
    V = lo + V * step
    # Quads: for each grid edge with a sign change, the 4 cells around it.
    faces = []
    for axis in range(3):
        a1, a2 = [ax for ax in range(3) if ax != axis]
        sl0 = [slice(None)] * 3; sl1 = [slice(None)] * 3
        sl0[axis] = slice(0, -1); sl1[axis] = slice(1, None)
        f0 = inside[tuple(sl0)]; f1 = inside[tuple(sl1)]
        change = f0 != f1
        e = np.argwhere(change)
        # The four cells sharing this edge: offsets 0/-1 along a1 and a2.
        good = (e[:, a1] > 0) & (e[:, a2] > 0) & (e[:, a1] < inside.shape[a1] - 1) & (e[:, a2] < inside.shape[a2] - 1)
        e = e[good]
        outward = f0[tuple(e.T)]   # inside at the low end: the surface faces +axis
        quad = []
        for d1, d2 in ((0, 0), (1, 0), (1, 1), (0, 1)):
            cc = e.copy()
            cc[:, a1] -= d1; cc[:, a2] -= d2
            quad.append(cell_index[cc[:, 0], cc[:, 1], cc[:, 2]])
        quad = np.stack(quad, 1)
        ok = (quad >= 0).all(1)
        quad, outward = quad[ok], outward[ok]
        # Winding so the face looks out of the rock (glTF: counter-clockwise from outside).
        flip = outward if axis == 1 else ~outward
        q = np.where(flip[:, None], quad[:, ::-1], quad)
        faces.append(np.concatenate([q[:, [0, 1, 2]], q[:, [0, 2, 3]]]))
    Fc = np.concatenate(faces) if faces else np.zeros((0, 3), np.int64)
    return V, Fc


def decimate_to(V, F, budget):
    """Vertex clustering until the triangles fit BUDGET (keeps shapes, loses fine detail)."""
    cell = 0.0
    while len(F) > budget:
        cell = cell * 1.25 if cell else np.median(np.linalg.norm(V[F[:, 1]] - V[F[:, 0]], axis=1)) * 1.3
        key = np.floor(V / cell).astype(np.int64)
        _, ids, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
        inverse = inverse.reshape(-1)
        nv = np.zeros((len(ids), 3)); cnt = np.zeros(len(ids))
        np.add.at(nv, inverse, V); np.add.at(cnt, inverse, 1)
        V = nv / cnt[:, None]
        F = inverse[F]
        F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
    return V, F


def build(t, recipe, args):
    rocks = recipe.get("rocks", [])
    if not rocks:
        raise SystemExit(f"no rocks in the recipe ({RECIPE}): add some with rock-add")
    boxes = [rock_bounds(r) for r in rocks]
    lo = np.min([b[0] for b in boxes], axis=0) - args.step * 2
    hi = np.max([b[1] for b in boxes], axis=0) + args.step * 2
    V, F = surface_nets(lambda P: recipe_density(P, recipe), lo, hi, args.step)
    # Faces deep under the sea are never seen: dropped (the rocks' flat feet).
    if recipe.get("sea") is not None:
        cz = V[F].mean(1)[:, 2]
        F = F[cz > recipe["sea"] - recipe.get("keep_below_sea", 6.0)]
        used, inverse = np.unique(F.reshape(-1), return_inverse=True)
        V, F = V[used], inverse.reshape(-1, 3)
    if args.budget and len(F) > args.budget:
        V, F = decimate_to(V, F, args.budget)
    # Normals.
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    VN = np.zeros_like(V)
    for k in range(3):
        np.add.at(VN, F[:, k], fn)
    VN /= np.maximum(np.linalg.norm(VN, axis=1), 1e-9)[:, None]
    styles = recipe.get("textures", {})
    rock_uri = styles.get("wall", "textures/terrain_0017.png")
    mat = t.material_for(rock_uri)
    prim = Prim(mat, {"POSITION": V, "NORMAL": VN, "TEXCOORD_0": np.zeros((len(V), 2)),
                      "COLOR_0": np.ones((len(V), 4))}, F)
    t.nodes = [n for n in t.nodes if n[0] != ROCKS_NODE]
    t.nodes.append([ROCKS_NODE, None, [prim]])
    # Texture by slope, height and facing, then UVs.
    auto_texture(t, recipe, args)
    out = {"triangles": int(len(F)), "vertices": int(len(V)), "bounds": [lo.round(1).tolist(), hi.round(1).tolist()]}
    if args.collision:
        cV, cF = V, F
        if args.collision_budget and len(cF) > args.collision_budget:
            cV, cF = decimate_to(V, F, args.collision_budget)
        c = Terrain.__new__(Terrain)
        c.path = Path(args.collision); c.json = {"asset": {"version": "2.0"}}
        c.images, c.textures, c.samplers = [], [], []
        c.materials = [{"name": f"rc1_surface_{recipe.get('surface', 0x1F):02x}", "pbrMetallicRoughness": {"baseColorFactor": [0.5, 0.5, 0.5, 1]}}]
        c.nodes = [["RocksCollision", None, [Prim(0, {"POSITION": cV, "NORMAL": np.tile([0, 0, 1.0], (len(cV), 1))}, cF)]]]
        c.save(args.collision)
        out["collision_triangles"] = int(len(cF))
    return out


def auto_texture(t, recipe, args):
    """Grass on the tops, the grass-lip picture just under each top edge, rock on the walls,
    creepers in patches, dark rock under the overhangs, wet rock at the sea."""
    styles = {"top": "textures/terrain_0003.png", "lip": "textures/terrain_0012.png",
              "wall": "textures/terrain_0017.png", "creeper": "textures/terrain_0011.png",
              "under": "textures/terrain_0019.png", "shore": "textures/terrain_0027.png"}
    styles.update(recipe.get("textures", {}))
    sea = recipe.get("sea", None)
    node = next(n for n in t.nodes if n[0] == ROCKS_NODE)
    p = node[2][0]
    centre, n, area = face_data(p, np.zeros(3))
    nz = n[:, 2]
    # Top edge: a face is a "lip" when a top face lies just above it.
    flat = recipe.get("top_slope", 0.75)       # a face this upright or more is a top (grass)
    lip = np.zeros(len(centre), bool)
    cell = 3.0
    # The tops that count for a lip: a cell with a few square units of top in it, not a small
    # ledge half-way up a wall.
    top_area = {}
    for q, a_ in zip(centre[nz > flat], area[nz > flat]):
        k = (int(q[0] // cell), int(q[1] // cell))
        z, total = top_area.get(k, (-1e9, 0.0))
        top_area[k] = (max(z, q[2]), total + a_)
    key = {k: z for k, (z, total) in top_area.items() if total >= recipe.get("lip_top_area", 3.0)}
    if key:
        for i in np.nonzero((nz <= flat) & (nz > -0.3))[0]:
            q = centre[i]
            best = -1e9
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    best = max(best, key.get((int(q[0] // cell) + dx, int(q[1] // cell) + dy), -1e9))
            lip[i] = 0 < best - q[2] < recipe.get("lip", 4.0)
    creep = value_noise3(centre / 24.0, 9, 2) > 1 - recipe.get("creepers", 0.42)   # creepers: the share of walls they cover
    kind = np.full(len(centre), "wall", dtype=object)
    kind[creep] = "creeper"
    kind[lip] = "lip"
    top = nz > flat
    small = recipe.get("min_top_area", 0.0)   # a ledge with less top than this per cell stays rock
    if small:
        cells = np.floor(centre[:, :2] / cell).astype(np.int64)
        tiny = np.array([top_area.get((int(a), int(b)), (0, 0.0))[1] < small for a, b in cells])
        top &= ~tiny
    kind[top] = "top"
    kind = smooth_kinds(p.faces, kind, ("top", "lip", "wall", "creeper"), recipe.get("smooth_passes", 2))
    kind[nz < -0.35] = "under"
    if sea is not None:
        kind[(centre[:, 2] < sea + 2.5) & (nz <= flat)] = "shore"
    prims = []
    for name in ("top", "lip", "wall", "creeper", "under", "shore"):
        sel = kind == name
        if not sel.any():
            continue
        used, inverse = np.unique(p.faces[sel].reshape(-1), return_inverse=True)
        prims.append(Prim(t.material_for(styles[name]), {k: v[used] for k, v in p.attrs.items()}, inverse.reshape(-1, 3)))
    node[2] = prims
    tile = recipe.get("tile", 3.0)
    tiles = recipe.get("tiles", {})            # a kind's own tile size, e.g. {"wall": 5}
    wrap = recipe.get("uv") == "wrap" and recipe.get("rocks")
    for name, q in zip([k for k in ("top", "lip", "wall", "creeper", "under", "shore") if (kind == k).any()], prims):
        uri = t.texture_uri(q.material)
        lip_tile = recipe.get("lip", 4.0) if uri == styles["lip"] else None
        if wrap and name != "top":
            wrap_uv_prim(q, recipe["rocks"], tiles.get(name, tile), lip_tile)
        else:
            box_uv_prim(q, tiles.get(name, tile), lip_tile=lip_tile)


def smooth_kinds(faces, kind, kinds, passes):
    """Each face takes the kind most of the faces round it have (over shared corners), so the
    pictures come in patches, not lone triangles of one picture in another."""
    nv = int(faces.max()) + 1
    for _ in range(passes):
        score = []
        for k in kinds:
            at_vertex = np.zeros(nv)
            np.add.at(at_vertex, faces.reshape(-1), np.repeat((kind == k).astype(float), 3))
            score.append(at_vertex[faces].sum(1))
        best = np.array(kinds, dtype=object)[np.argmax(np.stack(score, 1), 1)]
        movable = np.isin(kind, kinds)
        kind = np.where(movable, best, kind)
    return kind


def wrap_uv_prim(p, rocks, tile, lip_tile=None):
    """The picture wrapped round each face's nearest rock (along its outline, up its height), so a
    wall reads as one rock with no seams where a box projection would change its axis."""
    F = p.faces
    for k in list(p.attrs):
        p.attrs[k] = p.attrs[k][F.reshape(-1)]
    p.faces = np.arange(len(F) * 3).reshape(-1, 3)
    P = p.pos.reshape(-1, 3, 3)
    c = P.mean(1)
    rx = np.array([r["x"] for r in rocks]); ry = np.array([r["y"] for r in rocks])
    rr = np.array([r["radius"] for r in rocks])
    best = np.full(len(c), np.inf); which = np.zeros(len(c), int)
    for i in range(len(rocks)):       # rock by rock: a face is near a few of them only
        d = np.hypot(c[:, 0] - rx[i], c[:, 1] - ry[i]) / rr[i]
        m = d < best
        best[m] = d[m]; which[m] = i
    ang = np.arctan2(P[:, :, 1] - ry[which][:, None], P[:, :, 0] - rx[which][:, None])
    ang = ang[:, :1] + (ang - ang[:, :1] + np.pi) % (2 * np.pi) - np.pi     # no face spans the seam
    u = ang * rr[which][:, None] / tile
    v = -P[:, :, 2] / tile
    uv = np.stack([u, v], -1).reshape(-1, 2)
    p.attrs["TEXCOORD_0"] = uv
    if lip_tile:
        Q = p.pos
        key = {}
        for q in Q:
            k = (int(q[0] // 3), int(q[1] // 3))
            key[k] = max(key.get(k, -1e9), q[2])
        top = np.array([max(key.get((int(q[0] // 3) + dx, int(q[1] // 3) + dy), -1e9)
                            for dx in (-1, 0, 1) for dy in (-1, 0, 1)) for q in Q])
        uv[:, 1] = np.clip((top - Q[:, 2]) / lip_tile, 0.02, 0.98)


def box_uv_prim(p, tile, lip_tile=None):
    """Box projection for a whole primitive (vertices split by face axis). A lip strip maps the
    picture once from the top edge down, so the hanging grass sits on the edge."""
    class A:
        pass
    a = A(); a.at = None; a.tile = tile
    t = Terrain.__new__(Terrain)
    t.nodes = [["x", None, [p]]]
    box_uv(t, a)
    if lip_tile:
        P = p.pos
        uv = p.attrs["TEXCOORD_0"]
        top = np.zeros(len(P))
        # The top of each lip vertex's neighbourhood (3-unit cells).
        key = {}
        for q in P:
            k = (int(q[0] // 3), int(q[1] // 3))
            key[k] = max(key.get(k, -1e9), q[2])
        for i, q in enumerate(P):
            best = -1e9
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    best = max(best, key.get((int(q[0] // 3) + dx, int(q[1] // 3) + dy), -1e9))
            top[i] = best
        uv[:, 1] = np.clip((top - P[:, 2]) / lip_tile, 0.02, 0.98)


# ---------------------------------------------------------------- command line

def read_recipe(terrain):
    path = Path(terrain).parent / RECIPE
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8")), path
    return {"rocks": []}, path


def add_brush(p):
    p.add_argument("--at", type=float, nargs=3, metavar=("X", "Y", "Z"))
    p.add_argument("--then", type=float, nargs=3, action="append", metavar=("X", "Y", "Z"),
                   help="more spots of the same stroke (a drag in the editor): one change, one undo")
    p.add_argument("--radius", type=float, default=10.0)
    p.add_argument("--hardness", type=float, default=0.5)
    p.add_argument("--strength", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=0)


def add_filters(p):
    p.add_argument("--slope-min", type=float, help="degrees: 0 flat, 90 wall, 180 ceiling")
    p.add_argument("--slope-max", type=float)
    p.add_argument("--z-min", type=float)
    p.add_argument("--z-max", type=float)


def stroke(t, args, op):
    """OP at --at, then at each --then spot in turn; the counts add up."""
    spots = [args.at] + (getattr(args, "then", None) or []) if args.at is not None else [None]
    total = {}
    for spot in spots:
        args.at = spot
        for key, value in op(t, args).items():
            total[key] = total.get(key, 0) + value if isinstance(value, int) else value
    if len(spots) > 1:
        total["spots"] = len(spots)
    return total


def main(argv=None):
    parser = argparse.ArgumentParser(prog="terrain", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="op", required=True)
    s = sub.add_parser("info"); s.add_argument("terrain"); s.add_argument("--near", type=float, nargs=3, metavar=("X", "Y", "R"))
    s = sub.add_parser("undo"); s.add_argument("terrain")
    s = sub.add_parser("uv"); s.add_argument("terrain"); s.add_argument("--tile", type=float, default=3.0); add_brush(s); add_filters(s)
    s = sub.add_parser("paint"); s.add_argument("terrain"); s.add_argument("--texture", required=True,
                                                                          help="the picture, as the glb names it (textures/terrain_0003.png)")
    add_brush(s); add_filters(s)
    s.add_argument("--noise", type=float, default=0.0, help="paint in patches this many units wide")
    s.add_argument("--coverage", type=float, default=0.5, help="with --noise: the share of faces painted")
    s = sub.add_parser("sculpt"); s.add_argument("terrain"); s.add_argument("--mode", required=True,
                                                                           choices=["raise", "lower", "smooth", "roughen", "flatten"])
    add_brush(s)
    s = sub.add_parser("bake-light"); s.add_argument("terrain")
    s.add_argument("--sun", type=float, nargs=2, default=[-40.0, 50.0], metavar=("AZIMUTH", "ELEVATION"))
    s.add_argument("--region", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    s.add_argument("--ao", type=int, default=24, help="sky directions for the ambient occlusion")
    s.add_argument("--cell", type=float, default=0.75, help="shadow detail in units")
    s.add_argument("--bias", type=float, default=0.6)
    s.add_argument("--strength", type=float, default=1.0)
    s.add_argument("--ambient", type=float, default=0.0, help="light added everywhere (no black walls)")
    s.add_argument("--sun-colour", type=float, nargs=3, default=[1.15, 1.02, 0.78])
    s.add_argument("--sky-colour", type=float, nargs=3, default=[0.42, 0.5, 0.62])
    s.add_argument("--bounce-colour", type=float, nargs=3, default=[0.22, 0.2, 0.12])
    s.add_argument("--bounce", type=float, default=0.0,
                   help="light bounced once off the surfaces round, times this (0: a warm guess from below)")
    s.add_argument("--sun-samples", type=int, default=1, help="sun directions for soft shadows")
    s.add_argument("--sun-soft", type=float, default=0.0, help="the sun's width in degrees (soft shadow edges)")
    s.add_argument("--keep-tint", action="store_true", help="multiply by the colours there now instead of replacing them")
    s.add_argument("--models", nargs="*", default=[], help="model glbs baked with the terrain (each shades the others)")
    s.add_argument("--model-tint", type=float, default=0.0, help="with --models: their light times this (0: as the terrain)")
    s.add_argument("--subdivide", type=float, default=0.0, help="with --models: split their faces to this size first")
    s.add_argument("--glow", type=float, default=0.0, help="with --models: vertices brighter than this keep their colour")
    s.add_argument("--lights", help="a json list of point lights: {\"at\": [x, y, z], \"colour\": [r, g, b], \"radius\": r}")
    s = sub.add_parser("rock-add"); s.add_argument("terrain")
    s.add_argument("--at", type=float, nargs=3, required=True, metavar=("X", "Y", "Z_FOOT"))
    s.add_argument("--radius", type=float, required=True); s.add_argument("--height", type=float, required=True)
    for k, d in (("undercut", 0.3), ("bulge", 0.15), ("taper", 0.2), ("roughness", 0.25), ("layering", 0.06),
                 ("layer", 6.0), ("yaw", 0.0), ("warp", 0.35)):
        s.add_argument(f"--{k}", type=float, default=d)
    s.add_argument("--stretch", type=float, nargs=2, default=[1.0, 1.0])
    s.add_argument("--seed", type=int, default=0)
    s = sub.add_parser("rock-clear"); s.add_argument("terrain")
    s = sub.add_parser("build"); s.add_argument("terrain")
    s.add_argument("--step", type=float, default=2.0); s.add_argument("--budget", type=int, default=0)
    s.add_argument("--collision", help="also write the rocks' collision to this glb")
    s.add_argument("--collision-budget", type=int, default=0)
    args = parser.parse_args(argv)
    path = Path(args.terrain)
    if args.op == "undo":
        result = undo(path)
    elif args.op in ("rock-add", "rock-clear"):
        recipe, rpath = read_recipe(path)
        if args.op == "rock-clear":
            recipe["rocks"] = []
        else:
            recipe.setdefault("rocks", []).append({
                "x": args.at[0], "y": args.at[1], "z0": args.at[2], "z1": args.at[2] + args.height, "radius": args.radius,
                "undercut": args.undercut, "bulge": args.bulge, "taper": args.taper, "roughness": args.roughness,
                "layering": args.layering, "layer": args.layer, "yaw": args.yaw, "warp": args.warp, "stretch": args.stretch, "seed": args.seed})
        rpath.write_text(json.dumps(recipe, indent=1) + "\n", encoding="utf-8", newline="\n")
        result = {"rocks": len(recipe["rocks"]), "recipe": str(rpath)}
    else:
        t = Terrain(path)
        if args.op == "info":
            result = info(t, args.near)
        else:
            backup(path)
            if args.op in ("uv", "paint", "sculpt"):
                result = stroke(t, args, {"uv": box_uv, "paint": paint, "sculpt": sculpt}[args.op])
            elif args.op == "bake-light":
                result = bake_models(t, args)
            elif args.op == "build":
                recipe, _ = read_recipe(path)
                result = build(t, recipe, args)
            t.save()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
