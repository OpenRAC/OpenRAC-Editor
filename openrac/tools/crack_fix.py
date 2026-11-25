"""Mends the cracks map_check.py finds in a drawn mesh (a level's own terrain.glb): each vertex
a little off another face's edge is moved onto it, and that face is split at it, so both sides
share the vertex and no line of sky shows between them; small holes are filled.

Works on a glb whose primitives are indexed triangles in one buffer (what the editor and the
level generators write). The new vertices take their attributes (normal, colour, texture place)
from the edge they lie on, so nothing changes in the picture but the cracks."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import map_check as mc  # noqa: F401,I001  (first: it puts the release's numpy on the path)
import numpy as np


CLOSE = 0.05          # open edges' vertices this close are one (a sliver left of a crack)

TYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class Glb:
    """A glb read into numpy: each primitive's attributes and indices, to change and write back."""

    def __init__(self, path: Path):
        data = Path(path).read_bytes()
        if data[:4] != b"glTF":
            raise ValueError(f"{path} is not a glb")
        length = struct.unpack("<I", data[12:16])[0]
        self.doc = json.loads(data[20:20 + length])
        at = 20 + length
        self.blob = data[at + 8:at + 8 + struct.unpack("<I", data[at:at + 4])[0]] if at < len(data) else b""
        if len(self.doc.get("buffers", [])) > 1 or any("uri" in b for b in self.doc.get("buffers", [])):
            raise ValueError(f"{path}: only one embedded buffer is supported")

    def read(self, i: int) -> np.ndarray:
        a = self.doc["accessors"][i]
        if "sparse" in a or "bufferView" not in a:
            raise ValueError("sparse accessors are not supported")
        v = self.doc["bufferViews"][a["bufferView"]]
        n, dt = WIDTH[a["type"]], np.dtype(TYPES[a["componentType"]])
        off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = v.get("byteStride", 0) or n * dt.itemsize
        raw = np.frombuffer(self.blob, np.uint8, (a["count"] - 1) * stride + n * dt.itemsize, off) if a["count"] else \
            np.zeros(0, np.uint8)
        if a["count"] == 0:
            return np.zeros((0, n), dt)
        rows = np.lib.stride_tricks.as_strided(raw, (a["count"], n * dt.itemsize), (stride, 1))
        return rows.copy().view(dt).reshape(a["count"], n)

    def primitives(self):
        """(mesh, primitive index, primitive) of every triangle primitive with indices."""
        for m, mesh in enumerate(self.doc.get("meshes", [])):
            for p, prim in enumerate(mesh["primitives"]):
                if prim.get("mode", 4) == 4 and "indices" in prim and "targets" not in prim:
                    yield m, p, prim

    def write(self, path: Path, changed: dict) -> None:
        """Writes the glb with CHANGED's primitives ({(mesh, prim): (attributes {name: array},
        indices)}) in place of theirs; every other part as it was, the buffer packed again."""
        doc = json.loads(json.dumps(self.doc))
        out = bytearray()
        views, accessors = [], []

        def put(data: bytes, target: int | None = None) -> int:
            while len(out) % 4:
                out.append(0)
            view = {"buffer": 0, "byteOffset": len(out), "byteLength": len(data)}
            if target is not None:
                view["target"] = target
            out.extend(data)
            views.append(view)
            return len(views) - 1

        def accessor(old: dict, array: np.ndarray, target: int) -> int:
            a = {k: v for k, v in old.items() if k not in ("bufferView", "byteOffset", "count", "min", "max", "sparse")}
            array = np.ascontiguousarray(array.astype(TYPES[old["componentType"]]))
            a["bufferView"] = put(array.tobytes(), target)
            a["count"] = int(len(array))
            if "min" in old and len(array):
                a["min"] = array.reshape(len(array), -1).min(0).tolist()
                a["max"] = array.reshape(len(array), -1).max(0).tolist()
            accessors.append(a)
            return len(accessors) - 1

        # Every accessor a primitive uses is written again (changed or as it was); images keep
        # their bytes.
        for m, mesh in enumerate(doc.get("meshes", [])):
            for p, prim in enumerate(mesh["primitives"]):
                attrs, indices = changed.get((m, p), (None, None))
                for name, i in list(prim["attributes"].items()):
                    array = attrs[name] if attrs is not None else self.read(i)
                    prim["attributes"][name] = accessor(self.doc["accessors"][i], array, 34962)
                if "indices" in prim:
                    array = indices if indices is not None else self.read(prim["indices"]).reshape(-1)
                    prim["indices"] = accessor(self.doc["accessors"][prim["indices"]], array.reshape(-1, 1), 34963)
                for target in prim.get("targets", []):
                    for name, i in list(target.items()):
                        target[name] = accessor(self.doc["accessors"][i], self.read(i), 34962)
        for image in doc.get("images", []):
            if "bufferView" in image:
                v = self.doc["bufferViews"][image["bufferView"]]
                at = v.get("byteOffset", 0)
                image["bufferView"] = put(self.blob[at:at + v["byteLength"]])
        for skin in doc.get("skins", []):
            if "inverseBindMatrices" in skin:
                i = skin["inverseBindMatrices"]
                skin["inverseBindMatrices"] = accessor(self.doc["accessors"][i], self.read(i), None)
        if doc.get("animations"):
            raise ValueError("animated glbs are not mended")
        doc["accessors"], doc["bufferViews"] = accessors, views
        while len(out) % 4:
            out.append(0)
        doc["buffers"] = [{"byteLength": len(out)}]
        head = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        head += b" " * (-len(head) % 4)
        total = 12 + 8 + len(head) + 8 + len(out)
        Path(path).write_bytes(b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I", len(head)) + b"JSON" + head
                               + struct.pack("<I", len(out)) + b"BIN\x00" + bytes(out))


def node_worlds(doc: dict) -> dict[int, list[np.ndarray]]:
    """Each mesh's world matrices (4 x 4), by the nodes that draw it."""
    out: dict[int, list[np.ndarray]] = {}

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

    def walk(i, parent):
        node = doc["nodes"][i]
        m = parent @ local(node)
        if "mesh" in node:
            out.setdefault(node["mesh"], []).append(m)
        for c in node.get("children", []):
            walk(c, m)
    scene = doc.get("scenes", [{}])[doc.get("scene", 0)] if doc.get("scenes") else {"nodes": range(len(doc["nodes"]))}
    for i in scene.get("nodes", []):
        walk(i, np.eye(4))
    return out


def mend(path: Path, out: Path | None = None, water: float | None = None, holes: bool = True) -> dict:
    """Mends PATH's cracks (into OUT, else in place). Returns {"moved", "split", "filled", "joined"}:
    vertices moved onto an edge, faces split, holes filled, vertices joined to a close one."""
    glb = Glb(path)
    worlds = node_worlds(glb.doc)
    done = {"moved": 0, "split": 0, "filled": 0, "joined": 0}
    changed = {}
    for m, mesh in enumerate(glb.doc.get("meshes", [])):
        if len(worlds.get(m, [])) != 1:
            continue                       # a mesh drawn twice (or never) is left as it is
        world = worlds[m][0]
        inverse = np.linalg.inv(world)
        prims = [(p, prim) for mm, p, prim in glb.primitives() if mm == m]
        if not prims:
            continue
        attrs = {p: {k: glb.read(i).astype(np.float64) for k, i in prim["attributes"].items()} for p, prim in prims}
        index = {p: glb.read(prim["indices"]).reshape(-1).astype(np.int64) for p, prim in prims}
        for _round in range(10):
            # Every face of the mesh, with its primitive, in world space.
            owner = np.concatenate([np.full(len(index[p]) // 3, p) for p, _ in prims])
            corner = np.concatenate([index[p][:len(index[p]) // 3 * 3] for p, _ in prims]).reshape(-1, 3)
            world_pos = {p: attrs[p]["POSITION"] @ world[:3, :3].T + world[:3, 3] for p, _ in prims}
            tris = np.concatenate([world_pos[p][index[p][:len(index[p]) // 3 * 3]] for p, _ in prims]).reshape(-1, 3, 3)
            if np.abs(tris).max(initial=0) > mc.FAR:
                break
            verts, edges, faces, inverse_ids, good = mc.open_edges(tris, whole=True)
            if len(edges) == 0:
                break
            targets = np.concatenate([edges, mc.seam_edges(faces)])
            ids, dist, edge, along = mc.crack_points(verts, edges, where=True, targets=targets)
            bad = dist < mc.GAP
            if water is not None:
                bad &= verts[ids][:, 2] > water + 0.1
            filling = holes and (not bad.any() or _round >= 3)
            if not bad.any() and not filling:
                break
            # Corners of each welded vertex, to move them all together.
            corners_of: dict[int, list[int]] = {}
            for c, v in enumerate(inverse_ids):
                corners_of.setdefault(int(v), []).append(c)
            faces_of_edge: dict = {}
            for f, (x, y, z) in enumerate(faces):
                for key in ((x, y), (y, z), (z, x)):
                    faces_of_edge.setdefault((int(min(key)), int(max(key))), []).append(f)
            splits: dict[int, dict] = {}
            for v, e, t in zip(ids[bad], edge[bad], along[bad]):
                a, b = targets[e]
                # A vertex on the edge keeps its place, one a little off it goes onto it; the
                # split puts the new corner exactly there, so both sides share it.
                target = verts[a] + (verts[b] - verts[a]) * t
                if dist[np.searchsorted(ids, v)] < mc.T_JUNCTION:
                    target = verts[v]
                else:
                    local = (inverse @ np.append(target, 1.0))[:3]
                    for c in corners_of[int(v)]:
                        f, k = divmod(c, 3)
                        attrs[owner[f]]["POSITION"][corner[f, k]] = local
                    done["moved"] += 1
                # Every face on that edge (one on an open edge, three or more on a seam). One
                # edge of a face a round: a cut on another edge waits for the next round.
                for f in faces_of_edge.get((int(a), int(b)), []):
                    if splits.setdefault(f, {"edge": (int(a), int(b)), "cuts": []})["edge"] == (int(a), int(b)):
                        splits[f]["cuts"].append((t, int(v), target))
            # Holes once the cracks are closed (a crack's sliver is no hole).
            cracked = set(ids[bad].tolist())
            fills = [f for f in small_holes(verts, edges, faces, good)
                     if (water is None or verts[f[0]][:, 2].mean() > water + 0.1) and not cracked.intersection(f[0])] \
                if filling else []
            if filling:
                splits = {}
            if not splits and not fills:
                break
            holes = holes and not filling
            new_faces: dict[int, list] = {p: [] for p, _ in prims}
            dropped = set()
            kept = np.nonzero(good)[0]
            for f_kept, split in splits.items():
                f = int(kept[f_kept])
                p = int(owner[f])
                a_w, b_w = split["edge"]
                cuts = split["cuts"]
                # The face's corners in its own winding, the cut edge first: (a, b, c).
                ids_f = faces[f_kept].tolist()
                k = next(i for i in range(3) if {ids_f[i], ids_f[(i + 1) % 3]} == {a_w, b_w})
                a_c, b_c, c_c = corner[f, k], corner[f, (k + 1) % 3], corner[f, (k + 2) % 3]
                start = ids_f[k]
                cuts = sorted(cuts, key=lambda q: q[0] if start == a_w else 1 - q[0])
                chain = [a_c]
                for t, _v, target in cuts:
                    s = t if start == a_w else 1 - t
                    chain.append(add_vertex(attrs[p], a_c, b_c, s, (inverse @ np.append(target, 1.0))[:3]))
                chain.append(b_c)
                for i in range(len(chain) - 1):
                    new_faces[p].append((chain[i], chain[i + 1], c_c))
                dropped.add(f)
                done["split"] += 1
            for ring, p_face in fills:
                f = int(kept[p_face])
                p = int(owner[f])
                # Corners of the ring in this primitive: the ring's welded vertices, each through
                # a corner of the primitive that holds it (else a copy of the face's nearest).
                local_ids = []
                for v in ring:
                    hit = [c for c in corners_of[int(v)] if owner[c // 3] == p]
                    c = hit[0] if hit else corners_of[int(v)][0]
                    local_ids.append(corner[c // 3, c % 3] if hit else
                                     add_vertex(attrs[p], corner[f, 0], corner[f, 0], 0.0,
                                                (inverse @ np.append(verts[v], 1.0))[:3]))
                centre = add_vertex(attrs[p], local_ids[0], local_ids[len(local_ids) // 2], 0.5,
                                    (inverse @ np.append(verts[ring].mean(0), 1.0))[:3])
                for i in range(len(local_ids)):
                    new_faces[p].append((local_ids[(i + 1) % len(local_ids)], local_ids[i], centre))
                done["filled"] += 1
            # Write the faces back per primitive: the kept ones, then the new ones.
            at = 0
            for p, _ in prims:
                n = len(index[p]) // 3
                faces_p = index[p][:n * 3].reshape(-1, 3)
                keep = np.array([at + i not in dropped for i in range(n)], bool)
                extra = np.array(new_faces[p], np.int64).reshape(-1, 3)
                index[p] = np.concatenate([faces_p[keep], extra]).reshape(-1)
                at += n
        done["joined"] += join_close(prims, attrs, index, world, water)
        for p, prim in prims:
            for k in attrs[p]:
                attrs[p][k] = attrs[p][k].astype(np.float64)
            changed[(m, p)] = (attrs[p], index[p])
    if any(done.values()):
        glb.write(out or path, changed)
    return done


def join_close(prims, attrs, index, world, water) -> int:
    """Open edges' vertices closer than CLOSE to each other become one (at their middle) and the
    faces that shrink to a line go: what is left of a crack after the splits is a sliver this
    small. Changes ATTRS and INDEX in place; returns how many vertices moved."""
    inverse = np.linalg.inv(world)
    owner = np.concatenate([np.full(len(index[p]) // 3, p) for p, _ in prims])
    corner = np.concatenate([index[p][:len(index[p]) // 3 * 3] for p, _ in prims]).reshape(-1, 3)
    world_pos = {p: attrs[p]["POSITION"] @ world[:3, :3].T + world[:3, 3] for p, _ in prims}
    tris = np.concatenate([world_pos[p][index[p][:len(index[p]) // 3 * 3]] for p, _ in prims]).reshape(-1, 3, 3)
    if len(tris) == 0 or np.abs(tris).max() > mc.FAR:
        return 0
    verts, edges, _faces, inverse_ids, _good = mc.open_edges(tris, whole=True)
    ids = np.unique(edges.reshape(-1))
    if water is not None:
        ids = ids[verts[ids][:, 2] > water + 0.1]
    if len(ids) < 2:
        return 0
    parent = {int(v): int(v) for v in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    cells: dict = {}
    for v in ids:
        cells.setdefault(tuple(np.floor(verts[v] / CLOSE).astype(np.int64)), []).append(int(v))
    for key, members in cells.items():
        near = [u for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                for u in cells.get((key[0] + dx, key[1] + dy, key[2] + dz), ())]
        for v in members:
            for u in near:
                if u != v and np.linalg.norm(verts[u] - verts[v]) < CLOSE:
                    parent[find(u)] = find(v)
    # An open edge's chain that stops (its end on one open edge only) stops at a gap: its end
    # joins the nearest other open vertex within GAP.
    degree = np.bincount(edges.reshape(-1), minlength=len(verts))
    ends = [int(v) for v in ids if degree[v] == 1]
    if ends:
        others = ids[np.isin(ids, edges[np.isin(edges, ends).any(1)].reshape(-1), invert=True) | (degree[ids] == 1)]
        cell = mc.GAP
        table: dict = {}
        for u in others:
            table.setdefault(tuple(np.floor(verts[u] / cell).astype(np.int64)), []).append(int(u))
        for v in ends:
            key = np.floor(verts[v] / cell).astype(np.int64)
            near = [u for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                    for u in table.get((key[0] + dx, key[1] + dy, key[2] + dz), ()) if u != v]
            if near:
                d = np.linalg.norm(verts[near] - verts[v], axis=1)
                if d.min() < mc.GAP:
                    parent[find(near[int(d.argmin())])] = find(v)
    groups: dict = {}
    for v in parent:
        groups.setdefault(find(v), []).append(v)
    group_of = np.arange(len(verts))
    moved = 0
    target = {}
    for root, members in groups.items():
        if len(members) < 2:
            continue
        at = verts[members].mean(0)
        for v in members:
            group_of[v] = root
            target[v] = (inverse @ np.append(at, 1.0))[:3]
            moved += 1
    if not moved:
        return 0
    for c, v in enumerate(inverse_ids):
        if int(v) in target:
            f, k = divmod(c, 3)
            attrs[owner[f]]["POSITION"][corner[f, k]] = target[int(v)]
    g = group_of[inverse_ids.reshape(-1, 3)]
    flat = (g[:, 0] == g[:, 1]) | (g[:, 1] == g[:, 2]) | (g[:, 0] == g[:, 2])
    at = 0
    for p, _ in prims:
        n = len(index[p]) // 3
        index[p] = index[p][:n * 3].reshape(-1, 3)[~flat[at:at + n]].reshape(-1)
        at += n
    return moved


def edge_faces(faces: np.ndarray, good: np.ndarray) -> dict:
    """Each open edge's one face (index among the kept faces), by its sorted vertex pair."""
    out: dict = {}
    count: dict = {}
    for f, (a, b, c) in enumerate(faces):
        for x, y in ((a, b), (b, c), (c, a)):
            key = (min(x, y), max(x, y))
            out[key] = f
            count[key] = count.get(key, 0) + 1
    return {k: v for k, v in out.items() if count[k] == 1}


def add_vertex(attrs: dict, a: int, b: int, t: float, position: np.ndarray) -> int:
    """A new vertex between A and B (T of the way), at POSITION: its index."""
    for k, values in attrs.items():
        row = position if k == "POSITION" else values[a] + (values[b] - values[a]) * t
        if k == "NORMAL":
            n = np.linalg.norm(row)
            row = row / n if n > 1e-9 else values[a]
        attrs[k] = np.vstack([values, row[None]])
    return len(attrs["POSITION"]) - 1


def small_holes(verts, edges, faces, good) -> list[tuple[list[int], int]]:
    """Each small hole (map_check.hole_rings) as its vertices in order round it, and the face
    beside its first edge."""
    out = []
    face_of = edge_faces(faces, good)
    for es in mc.hole_rings(verts, edges, faces):
        ring_edges = [tuple(edges[e]) for e in es]
        nxt: dict[int, list[int]] = {}
        for a, b in ring_edges:
            nxt.setdefault(int(a), []).append(int(b))
            nxt.setdefault(int(b), []).append(int(a))
        if any(len(v) != 2 for v in nxt.values()) or len(nxt) < 3:
            continue
        start = int(ring_edges[0][0])
        ring, prev = [start], None
        while True:
            options = [x for x in nxt[ring[-1]] if x != prev]
            prev, step = ring[-1], options[0]
            if step == start:
                break
            ring.append(step)
            if len(ring) > len(nxt):
                break
        if len(ring) != len(nxt):
            continue
        # The ring's direction so the fill faces the same way as the face beside it.
        a, b = ring[0], ring[1]
        f = face_of.get((min(a, b), max(a, b)))
        if f is None:
            continue
        fv = faces[f].tolist()
        k = fv.index(a)
        if fv[(k + 1) % 3] != b:
            ring = ring[::-1]
        out.append((ring, f))
    return out
