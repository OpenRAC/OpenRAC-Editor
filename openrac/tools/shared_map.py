# SPDX-License-Identifier: MIT
# Copyright (c) 2026 the OpenRAC contributors
"""Share a map made in OpenRAC Editor as one file, and import a shared map on another disc.

    python3 openrac/tools/openrac_editor.py map export PROJECT OUT.openrac-map [--author NAME] [--credit TEXT]
    python3 openrac/tools/openrac_editor.py map import MAP.openrac-map PROJECTS [--planet OVERRIDES]

A map file never holds the game's own data. It holds what the creator changed:

- each level scene (.tscn) as the nodes added, removed or changed, against a fresh extraction of
  the same level (a copied object is "a copy of node X" plus what differs);
- each other changed file as a byte patch against the player's own copy (only new bytes inside);
- each file of the game used elsewhere (a texture or model of another level) as a reference:
  level, path and sha256, rebuilt from the player's disc at import;
- the creator's own files (their GLB models, PNG textures, cutscenes) as they are.

Import extracts the level from the player's disc, makes the same changes there, and writes the
project beside the player's levels as level_NN-<map>, so their own level_NN is not touched. With
--planet it is then added to the mods as a new planet, so it plays without the editor.
"""

from __future__ import annotations
import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import carry
import planet

FORMAT = "openrac-map"
VERSION = 1
SUFFIX = ".openrac-map"
MANIFEST = "map.json"
AUTHOR = "map/author"  # project.godot [openrac], the editor's Made By field
TOOLS = Path(__file__).resolve().parent

# What is never part of a map: Godot's caches and ids, the editor's own files (shipped with every
# project), backups and reports.
SKIPPED_DIRS = {".godot", ".openrac", ".openrac-backups", "addons", "rc1"}
SKIPPED_SUFFIXES = (".uid", ".tmp")
SKIPPED_NAMES = {"carried.json", ".DS_Store", "Thumbs.db"}
# Sane limits, so a broken or hostile file cannot fill the disk.
MOST_FILES = 20000
MOST_BYTES = 2 << 30


class MapError(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# =============================================================================
# BYTE PATCHES
# =============================================================================
# A patch turns BASE into TARGET with two kinds of steps: copy a run of BASE, or add new bytes.
# Only the added bytes are in the patch, so a patch of a game file holds the creator's bytes only.

PATCH_MAGIC = b"ORMP1"
BLOCK = 32


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_varint(data: bytes, at: int) -> tuple[int, int]:
    n, shift = 0, 0
    while True:
        if at >= len(data) or shift > 63:
            raise MapError("a patch in the map is cut short")
        byte = data[at]
        at += 1
        n |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return n, at


def _same_run(a: bytes, a_at: int, b: bytes, b_at: int) -> int:
    """How many bytes match from A[a_at] and B[b_at] on (compared in big steps, then exactly)."""
    most = min(len(a) - a_at, len(b) - b_at)
    n, step = 0, 4096
    while n < most:
        size = min(step, most - n)
        if a[a_at + n:a_at + n + size] == b[b_at + n:b_at + n + size]:
            n += size
            continue
        if size == 1:
            break
        step = max(1, size // 2)
    return n


def make_patch(base: bytes, target: bytes) -> bytes:
    index: dict[bytes, int] = {}
    for at in range(0, len(base) - BLOCK + 1, BLOCK):
        index.setdefault(base[at:at + BLOCK], at)
    ops = bytearray()
    pending = bytearray()

    def flush():
        if pending:
            ops.extend(b"\x02" + _varint(len(pending)) + pending)
            pending.clear()

    p, last_end = 0, 0
    while p < len(target):
        # Most edits keep the bytes around them where they were: try the same place first.
        guess = last_end + len(pending)
        s = guess if guess + BLOCK <= len(base) and base[guess:guess + BLOCK] == target[p:p + BLOCK] else \
            index.get(target[p:p + BLOCK]) if p + BLOCK <= len(target) else None
        if s is None:
            pending.append(target[p])
            p += 1
            continue
        length = _same_run(base, s, target, p)
        back = 0
        while back < len(pending) and s - back > 0 and base[s - back - 1] == pending[-1 - back]:
            back += 1
        if back:
            del pending[len(pending) - back:]
        flush()
        ops.extend(b"\x01" + _varint(s - back) + _varint(length + back))
        p += length
        last_end = s + length
    flush()
    return PATCH_MAGIC + _varint(len(target)) + bytes.fromhex(sha(base)) + bytes.fromhex(sha(target)) + bytes(ops)


def apply_patch(base: bytes, patch: bytes) -> bytes:
    if not patch.startswith(PATCH_MAGIC):
        raise MapError("a patch in the map is not one this editor makes")
    size, at = _read_varint(patch, len(PATCH_MAGIC))
    if size > MOST_BYTES:
        raise MapError("a patch in the map makes a file too big")
    base_sum, target_sum = patch[at:at + 32].hex(), patch[at + 32:at + 64].hex()
    at += 64
    if sha(base) != base_sum:
        raise MapError("the file a patch is for differs on this disc")
    out = bytearray()
    while at < len(patch):
        op = patch[at]
        at += 1
        if op == 1:
            start, at = _read_varint(patch, at)
            length, at = _read_varint(patch, at)
            if start + length > len(base):
                raise MapError("a patch in the map copies past its file's end")
            out += base[start:start + length]
        elif op == 2:
            length, at = _read_varint(patch, at)
            out += patch[at:at + length]
            at += length
        else:
            raise MapError("a patch in the map is broken")
        if len(out) > size:
            raise MapError("a patch in the map is broken")
    if len(out) != size or sha(bytes(out)) != target_sum:
        raise MapError("a patch in the map did not give the file it was made from")
    return bytes(out)


# =============================================================================
# SCENE CHANGES
# =============================================================================
# A scene is compared section by section: nodes by their path, sub-resources by their id, external
# resources by their file. ExtResource("id") in values is written as ExtResource("res://file"), so
# the ids of the two scenes need not agree.

EXT_HEAD = re.compile(r'^\[ext_resource (?P<attrs>.*)\]$')
SUB_HEAD = re.compile(r'^\[sub_resource [^\]]*id="(?P<id>[^"]+)"')
ATTR = re.compile(r'(\w+)="([^"]*)"')
EXT_REF = re.compile(r'ExtResource\("([^"]+)"\)')
NUMBER = re.compile(r'-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?', re.I)


def _ext(block) -> dict | None:
    found = EXT_HEAD.match(block.head)
    return dict(ATTR.findall(found.group("attrs"))) if found else None


def key_of(block) -> str | None:
    if block.head.startswith("[gd_scene") or block.head.startswith("[gd_resource"):
        return None
    ext = _ext(block)
    if ext is not None:
        return "ext:" + ext.get("path", "")
    sub = SUB_HEAD.match(block.head)
    if sub:
        return "sub:" + sub.group("id")
    path = block.path
    if path is not None:
        return "node:" + path
    return "head:" + block.head


def _by_path(text: str, ids: dict[str, str]) -> str:
    return EXT_REF.sub(lambda m: f'ExtResource("{ids.get(m.group(1), m.group(1))}")', text)


def _same_value(a: str, b: str) -> bool:
    """A and B say the same, numbers compared to six places (Godot writes floats its own way)."""
    if a == b:
        return True
    if NUMBER.sub("#", a) != NUMBER.sub("#", b):
        return False
    for x, y in zip(NUMBER.findall(a), NUMBER.findall(b)):
        fx, fy = float(x), float(y)
        if abs(fx - fy) > 1e-6 * max(1.0, abs(fx), abs(fy)):
            return False
    return True


def _without_uid(head: str) -> str:
    """HEAD without the ids Godot makes up on each machine (resource uids, node unique ids)."""
    return re.sub(r' (?:uid="[^"]*"|unique_id=\d+)', "", head)


def _sections(text: str):
    blocks = carry.read(text)
    ids = {}
    for block in blocks:
        ext = _ext(block)
        if ext is not None and "id" in ext:
            ids[ext["id"]] = ext.get("path", "")
    keyed = []
    for block in blocks:
        key = key_of(block)
        if key is None:
            continue
        head = _without_uid(_by_path(block.head, ids))
        props = {k: _by_path(v, ids) for k, v in block.props}
        keyed.append((key, head, props, list(block.extra)))
    return blocks, keyed


def _prop_changes(base: dict, now: dict) -> tuple[dict, list]:
    changed = {k: v for k, v in now.items() if k not in base or not _same_value(base[k], v)}
    gone = [k for k in base if k not in now]
    return changed, gone


def _head_kind(key: str, head: str) -> str:
    """What a section is an instance or type of: the head without its name and parent."""
    return key.split(":")[0] + ":" + re.sub(r'(name|parent|id)="[^"]*"', "", head)


def scene_changes(pristine: str, edited: str) -> dict:
    _, old = _sections(pristine)
    _, new = _sections(edited)
    old_by = {k: (h, p, e) for k, h, p, e in old}
    new_keys = {k for k, _, _, _ in new}
    kinds: dict[str, list[str]] = {}
    for k, h, _, _ in old:
        kinds.setdefault(_head_kind(k, h), []).append(k)
    changes = {"removed": [k for k, _, _, _ in old if k not in new_keys], "changed": [], "added": [], "ext": []}
    previous = None
    for key, head, props, extra in new:
        if key.startswith("ext:"):
            if key not in old_by:
                attrs = dict(ATTR.findall(EXT_HEAD.match(head).group("attrs")))
                changes["ext"].append({"path": attrs.get("path", ""), "type": attrs.get("type", "Resource")})
            previous = key
            continue
        if key in old_by:
            old_head, old_props, _ = old_by[key]
            set_, unset = _prop_changes(old_props, props)
            entry = {"key": key}
            if old_head != head:
                entry["head"] = head
            if set_:
                entry["set"] = set_
            if unset:
                entry["unset"] = unset
            if len(entry) > 1:
                changes["changed"].append(entry)
        else:
            # A copy of a section of the scene: the one of the same kind it differs least from.
            base, best = None, None
            for candidate in kinds.get(_head_kind(key, head), [])[:4000]:
                set_, unset = _prop_changes(old_by[candidate][1], props)
                cost = sum(len(v) for v in set_.values()) + len(unset)
                if best is None or cost < best[0]:
                    base, best = candidate, (cost, set_, unset)
                    if cost == 0:
                        break
            entry = {"key": key, "head": head, "after": previous}
            if base is not None and best[0] < sum(len(v) for v in props.values()):
                entry["base"] = base
                if best[1]:
                    entry["set"] = best[1]
                if best[2]:
                    entry["unset"] = best[2]
            else:
                entry["set"] = props
            if extra:
                entry["extra"] = extra
            changes["added"].append(entry)
        previous = key
    return changes


def apply_scene_changes(pristine: str, changes: dict, problems: list[str]) -> str:
    blocks, _ = _sections(pristine)
    ids: dict[str, str] = {}
    for block in blocks:
        ext = _ext(block)
        if ext is not None and "id" in ext:
            ids[ext.get("path", "")] = ext["id"]
    last_ext = max((i for i, b in enumerate(blocks) if _ext(b) is not None), default=0)
    for n, ext in enumerate(changes.get("ext", [])):
        if ext["path"] in ids:
            continue
        made = f"map_{n + 1}"
        while made in ids.values():
            made += "_"
        ids[ext["path"]] = made
        block = carry.Block(f'[ext_resource type="{ext["type"]}" path="{ext["path"]}" id="{made}"]')
        block.changed = True
        last_ext += 1
        blocks.insert(last_ext, block)

    def by_id(text: str) -> str:
        return EXT_REF.sub(lambda m: f'ExtResource("{ids.get(m.group(1), m.group(1))}")', text)

    def find(key: str):
        return next((b for b in blocks if key_of(b) == key), None)

    # A copy names the section it was made from as the level has it, which may be one the map
    # removes (a model kept only for its copies).
    pristine_by = {key_of(b): b for b in blocks if key_of(b) is not None}
    for key in changes.get("removed", []):
        gone = find(key)
        if gone is None:
            continue
        under = key[5:] + "/" if key.startswith("node:") else None
        blocks[:] = [b for b in blocks if b is not gone and not (
            under and (key_of(b) or "").startswith("node:" + under))]
    for entry in changes.get("changed", []):
        block = find(entry["key"])
        if block is None:
            problems.append(f"{entry['key'][5:]}: not in this disc's level, its change is left out")
            continue
        if "head" in entry:
            block.head = by_id(entry["head"])
        block.props = [(k, v) for k, v in block.props if k not in entry.get("unset", [])]
        for k, v in entry.get("set", {}).items():
            block.set(k, by_id(v))
        block.changed = True
    for entry in changes.get("added", []):
        if find(entry["key"]) is not None:
            problems.append(f"{entry['key'][5:]}: already in this disc's level, the map's copy is left out")
            continue
        block = carry.Block(by_id(entry["head"]))
        if "base" in entry:
            base = find(entry["base"]) or pristine_by.get(entry["base"])
            if base is None:
                problems.append(f"{entry['key'][5:]}: copies {entry['base'][5:]}, which this level lacks")
                continue
            block.props = [(k, v) for k, v in base.props if k not in entry.get("unset", [])]
        for k, v in entry.get("set", {}).items():
            block.set(k, by_id(v))
        block.extra = list(entry.get("extra", []))
        block.changed = True
        after = find(entry["after"]) if entry.get("after") else None
        if after is None:
            blocks.append(block)
        else:
            at = blocks.index(after) + 1
            blocks.insert(at, block)
    for block in blocks:
        block.blank = True
    return carry.write(blocks)


# =============================================================================
# THE PROJECT'S FILES
# =============================================================================

def project_files(project: Path) -> dict[str, Path]:
    """Every file of PROJECT that can be part of a map, by its path inside it."""
    found = {}
    for path in sorted(project.rglob("*")):
        rel = path.relative_to(project)
        if not path.is_file() or path.is_symlink() or set(rel.parts[:-1]) & SKIPPED_DIRS or rel.parts[0] in SKIPPED_DIRS:
            continue
        if path.name in SKIPPED_NAMES or path.name.endswith(SKIPPED_SUFFIXES):
            continue
        found[rel.as_posix()] = path
    return found


def level_projects(projects: Path) -> dict[int, Path]:
    """The player's level projects beside the map: level_NN folders."""
    found = {}
    for folder in projects.glob("level_*"):
        if re.fullmatch(r"level_\d\d", folder.name) and (folder / "project.godot").is_file():
            found[int(folder.name[6:])] = folder
    return found


def extract_levels(disc_path: Path, levels: list[int], into: Path) -> Path:
    """Fresh projects of LEVELS from the disc, as the launcher's Extract makes them."""
    command = [sys.executable, str(TOOLS / "openrac_editor.py"), "projects", str(disc_path), str(into),
               "--split", "--anywhere"] + [a for n in levels for a in ("--level", str(n))]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        raise MapError(f"the levels could not be read from the disc: {(done.stderr or done.stdout).strip()[-400:]}")
    return into


class GameFiles:
    """Files of the game, found by level and path in the player's projects, or read fresh from their disc."""

    def __init__(self, disc_path: Path, projects: Path | None, scratch: Path):
        self.disc_path, self.scratch = disc_path, scratch
        self.mine = level_projects(projects) if projects else {}
        self.fresh: dict[int, Path] = {}

    def fresh_level(self, level: int) -> Path:
        if level not in self.fresh:
            extract_levels(self.disc_path, [level], self.scratch / f"fresh_{level:02}")
            self.fresh[level] = self.scratch / f"fresh_{level:02}" / f"level_{level:02}"
        return self.fresh[level]

    def get(self, level: int, rel: str, wanted: str) -> bytes:
        for root in ([self.mine[level]] if level in self.mine else []) + [None]:
            root = root or self.fresh_level(level)
            path = root / rel
            if path.is_file():
                data = path.read_bytes()
                if sha(data) == wanted:
                    return data
        raise MapError(f"level {level:02} of this disc has no {rel} as the map needs it "
                       "(a different disc version or a newer editor)")

    def base(self, level: int, rel: str) -> bytes | None:
        """The file as the disc has it, for a copy made from it: the player's project first."""
        for root in ([self.mine[level]] if level in self.mine else []) + [None]:
            path = (root or self.fresh_level(level)) / rel
            if path.is_file():
                return path.read_bytes()
        return None

    def index(self, sizes: set[int], skip: Path) -> dict[str, tuple[int, str]]:
        """sha256 -> (level, path) of the game's files of these sizes in the player's projects."""
        found = {}
        for level, root in sorted(self.mine.items()):
            if root.resolve() == skip.resolve():
                continue
            for rel, path in project_files(root).items():
                if rel == "project.godot" or path.suffix == ".import":
                    continue
                if path.stat().st_size in sizes:
                    found.setdefault(sha(path.read_bytes()), (level, rel))
        return found


def source_of(rel: str, project: Path) -> tuple[int, str] | None:
    """The game file a copied model was made from, when the copy says so (library and static imports)."""
    parts = PurePosixPath(rel).parts
    # The Assets dock: library/<game>/<disc>/level_NN/<kind>/<name>.glb, from that level's own file.
    if len(parts) >= 6 and parts[0] == "library" and re.fullmatch(r"level_\d\d", parts[3]) and rel.endswith(".glb"):
        if parts[1] == "skins":
            return None
        level = int(parts[3][6:])
        return level, f"levels/{parts[3]}/" + "/".join(parts[4:])
    # Static imports: levels/level_NN/imported_static/<id>/model.glb with its source.json.
    if parts[-1] == "model.glb" and "imported_static" in parts:
        try:
            note = json.loads((project / rel).with_name("source.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        level = note.get("source_level")
        if note.get("kind") in ("ties", "shrubs", "mobys") and isinstance(level, str) and re.fullmatch(r"level_\d\d", level):
            return int(level[6:]), f"levels/{level}/{note['kind']}/{note.get('source_model', '')}"
    return None


def _safe(rel: str) -> str:
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in rel or ":" in rel:
        raise MapError(f"the map names a file outside its project: {rel}")
    return path.as_posix()


# =============================================================================
# EXPORT
# =============================================================================

def export(project: Path, out: Path, author: str = "", about: str = "", credits: list[str] | None = None,
           disc_path: Path | None = None, keep_problems: bool = False) -> dict:
    """Write PROJECT's map to OUT; what went in, for the report. A map with problems a player would
    see (map_check.py: holes, collision off what is drawn, things floating or inside each other)
    is refused, unless KEEP_PROBLEMS."""
    import openrac_editor
    from disc import Disc
    project = project.resolve()
    if not (project / "project.godot").is_file():
        raise MapError(f"{project} is not a level project")
    level = planet.level_of(project)
    name = planet.planet_name(project)
    ident = planet.planet_id(project, name)
    author = author or planet.settings(project).get(AUTHOR, "").strip()
    disc_path = disc_path or openrac_editor.remembered_disc(project)
    with Disc(disc_path) as disc:
        version = disc.version.key
    projects = project.parent
    with tempfile.TemporaryDirectory(prefix=".openrac-map-") as temp:
        scratch = Path(temp)
        game = GameFiles(disc_path, projects, scratch)
        pristine_root = game.fresh_level(level)
        name_dir = f"levels/level_{level:02}"
        try:
            mine = json.loads((project / name_dir / "level.json").read_text(encoding="utf-8"))
            fresh = json.loads((pristine_root / name_dir / "level.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise MapError("the project has no level.json: extract the level again") from None
        if mine.get("schema") != fresh.get("schema"):
            raise MapError("this project was made by an older editor: run \"Export the levels again\" first "
                           "(your edits are carried over)")
        pristine = project_files(pristine_root)
        edited = project_files(project)
        if not keep_problems:
            refuse_problems(project, level, pristine, edited)
        entries, blobs, report = [], {}, {"scenes": 0, "patches": 0, "game": 0, "own": [], "removed": 0}
        new = {rel: path for rel, path in edited.items() if rel not in pristine}
        # Settings Godot writes for files of the level itself are its own, not the creator's.
        new = {rel: path for rel, path in new.items()
               if not (rel.endswith(".import") and rel[:-7] in pristine)}
        known = game.index({p.stat().st_size for p in new.values()}, project)
        here = {sha(p.read_bytes()): rel for rel, p in pristine.items() if p.stat().st_size in
                {q.stat().st_size for q in new.values()}}
        for rel, path in edited.items():
            data = path.read_bytes()
            if rel in pristine:
                base = pristine[rel].read_bytes()
                if base == data:
                    continue
                if rel.endswith(".tscn"):
                    changes = scene_changes(base.decode("utf-8"), data.decode("utf-8"))
                    if not any(changes.values()):
                        continue
                    blob = f"scenes/{len(blobs):04}.json"
                    blobs[blob] = json.dumps(changes, indent=1).encode()
                    entries.append({"path": rel, "kind": "scene", "data": blob, "base": sha(base)})
                    report["scenes"] += 1
                else:
                    blob = f"patches/{len(blobs):04}.bin"
                    blobs[blob] = make_patch(base, data)
                    entries.append({"path": rel, "kind": "patch", "data": blob,
                                    "base": {"level": level, "path": rel, "sha256": sha(base)}})
                    report["patches"] += 1
                continue
            if rel not in new:
                continue
            digest = sha(data)
            if digest in here:
                entries.append({"path": rel, "kind": "game",
                                "from": {"level": level, "path": here[digest], "sha256": digest}})
                report["game"] += 1
                continue
            if digest in known:
                there, there_rel = known[digest]
                entries.append({"path": rel, "kind": "game", "from": {"level": there, "path": there_rel, "sha256": digest}})
                report["game"] += 1
                continue
            source = source_of(rel, project)
            if source is not None:
                base = game.base(*source)
                if base is not None:
                    blob = f"patches/{len(blobs):04}.bin"
                    blobs[blob] = make_patch(base, data)
                    entries.append({"path": rel, "kind": "patch", "data": blob,
                                    "base": {"level": source[0], "path": source[1], "sha256": sha(base)}})
                    report["patches"] += 1
                    continue
            blob = "files/" + rel
            blobs[blob] = data
            entries.append({"path": rel, "kind": "file", "data": blob, "sha256": digest})
            report["own"].append(rel)
        for rel in pristine:
            if rel not in edited and not rel.endswith(".import"):
                entries.append({"path": rel, "kind": "removed"})
                report["removed"] += 1
    manifest = {"format": FORMAT, "version": VERSION, "id": ident, "name": name, "author": author,
                "about": about, "credits": list(credits or []), "game": "rac1", "disc": version,
                "level": level, "schema": mine.get("schema"),
                "made": time.strftime("%Y-%m-%d", time.gmtime()), "files": entries}
    out = out.resolve()
    if out.suffix != SUFFIX:
        out = out.with_name(out.name + SUFFIX)
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".part")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, indent=1) + "\n")
        for blob, data in blobs.items():
            archive.writestr(blob, data)
    partial.replace(out)
    report.update(out=out, id=ident, name=name, level=level, disc=version)
    return report


# =============================================================================
# IMPORT
# =============================================================================

def read_map(path: Path) -> tuple[dict, zipfile.ZipFile]:
    try:
        archive = zipfile.ZipFile(path)
        manifest = json.loads(archive.read(MANIFEST))
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        raise MapError(f"{path.name} is not an OpenRAC map") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise MapError(f"{path.name} is not an OpenRAC map")
    if manifest.get("version") != VERSION:
        raise MapError(f"{path.name} was made by a newer OpenRAC Editor: update the editor")
    if manifest.get("game") != "rac1" or not isinstance(manifest.get("level"), int) or \
            not 0 <= manifest["level"] < 19 or not isinstance(manifest.get("files"), list):
        raise MapError(f"{path.name} is not a Ratchet & Clank map this editor can read")
    if len(manifest["files"]) > MOST_FILES or sum(i.file_size for i in archive.infolist()) > MOST_BYTES:
        raise MapError(f"{path.name} is too big")
    ident = str(manifest.get("id", ""))
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,80}", ident):
        raise MapError(f"{path.name} has no usable map id")
    return manifest, archive


def import_map(path: Path, projects: Path, disc_path: Path | None = None) -> dict:
    """Make the map's project beside the player's levels: <projects>/level_NN-<id>. What was done."""
    import openrac_editor
    from disc import Disc
    path, projects = path.resolve(), projects.resolve()
    manifest, archive = read_map(path)
    with archive:
        disc_path = disc_path or openrac_editor.remembered_disc(projects)
        with Disc(disc_path) as disc:
            version = disc.version.key
        if version != manifest.get("disc"):
            raise MapError(f"this map was made from the {str(manifest.get('disc')).upper()} disc; "
                           f"yours is {version.upper()}. Set that disc up to play it")
        level = manifest["level"]
        target = projects / f"level_{level:02}-{manifest['id']}"
        problems: list[str] = []
        with tempfile.TemporaryDirectory(prefix=".openrac-map-", dir=projects) as temp:
            scratch = Path(temp)
            game = GameFiles(disc_path, projects, scratch)
            extract_levels(disc_path, [level], scratch / "made")
            made = scratch / "made" / f"level_{level:02}"
            fresh = json.loads((made / f"levels/level_{level:02}/level.json").read_text(encoding="utf-8"))
            if fresh.get("schema") != manifest.get("schema"):
                problems.append("the map was made with another editor version: check it in the editor")
            for entry in manifest["files"]:
                rel = _safe(str(entry.get("path", "")))
                file = made / rel
                kind = entry.get("kind")
                if kind == "removed":
                    if file.is_file():
                        file.unlink()
                    continue
                if kind == "game":
                    source = entry["from"]
                    data = game.get(int(source["level"]), _safe(source["path"]), str(source["sha256"]))
                elif kind == "file":
                    data = archive.read(_safe(entry["data"]))
                    if sha(data) != entry.get("sha256"):
                        raise MapError(f"{rel} in the map is damaged")
                elif kind == "patch":
                    base = entry["base"]
                    if int(base["level"]) == level and file.is_file() and sha(file.read_bytes()) == base["sha256"]:
                        old = file.read_bytes()
                    else:
                        old = game.get(int(base["level"]), _safe(base["path"]), str(base["sha256"]))
                    data = apply_patch(old, archive.read(_safe(entry["data"])))
                elif kind == "scene":
                    if not file.is_file():
                        problems.append(f"{rel}: not in this disc's level, left out")
                        continue
                    text = file.read_text(encoding="utf-8")
                    if sha(text.encode()) != entry.get("base"):
                        problems.append(f"{rel}: this disc's level differs from the creator's, check it")
                    changes = json.loads(archive.read(_safe(entry["data"])))
                    data = apply_scene_changes(text, changes, problems).encode()
                else:
                    raise MapError(f"{rel}: the map has a kind of file this editor does not know ({kind})")
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
            planet.set_setting(made, planet.NAME, str(manifest.get("name", "")) or manifest["id"])
            planet.set_setting(made, planet.ID, manifest["id"])
            if manifest.get("author"):
                planet.set_setting(made, AUTHOR, str(manifest["author"])[:80])
            (made / ".openrac").mkdir(exist_ok=True)
            (made / ".openrac" / "map.json").write_text(json.dumps(
                {k: manifest.get(k) for k in ("id", "name", "author", "about", "credits", "made")}, indent=1) + "\n",
                encoding="utf-8", newline="\n")
            backup = None
            if target.exists():
                backup = projects.parent / f"{projects.name}-backups" / time.strftime("%Y%m%d-%H%M%S") / target.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(backup))
            shutil.move(str(made), str(target))
    return {"project": target, "id": manifest["id"], "name": manifest.get("name"), "level": level,
            "author": manifest.get("author"), "credits": manifest.get("credits", []), "problems": problems,
            "backup": backup, "disc": disc_path}


# =============================================================================
# COMMAND LINE
# =============================================================================

def needs_runtime(project: Path, level: int) -> bool:
    """Whether PROJECT's level has what only the editor-runtime mod plays: simple moves (objects
    of other levels brought for their look among them) or cutscenes."""
    import behaviours
    level_dir = project / "levels" / f"level_{level:02}"
    scene = level_dir / f"level_{level:02}.tscn"
    try:
        moves = behaviours.given(scene.read_text(encoding="utf-8"))
    except OSError:
        moves = []
    return bool(moves) or (level_dir / "cutscenes").is_dir()


def refuse_problems(project: Path, level: int, pristine: dict, edited: dict) -> None:
    """MapError listing what map_check.py finds wrong in the map; only the things the map adds or
    moves are judged, not the disc's own."""
    try:
        import map_check
    except ImportError:                           # no numpy: not checked (the release brings it)
        return
    rel = f"levels/level_{level:02}/level_{level:02}.tscn"
    changed = map_check.edited_from_scenes(pristine[rel].read_text(encoding="utf-8"),
                                           edited[rel].read_text(encoding="utf-8")) \
        if rel in pristine and rel in edited else None
    found = map_check.check_level(project, map_check.water_of(project), edited=changed)
    lines = [p.line() for ps in found["problems"].values() for p in ps]
    if lines:
        shown = "\n".join(f"  {line}" for line in lines[:200])
        more = f"\n  ... and {len(lines) - 200} more" if len(lines) > 200 else ""
        raise MapError(f"The map has {len(lines)} problem(s) a player would see (run openrac_editor.py check-map "
                       f"{project} --fix to mend what it can):\n{shown}{more}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="openrac_editor.py map", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    out = commands.add_parser("export", help="write a level project's map as one .openrac-map file")
    out.add_argument("project", type=Path, help="one level's project")
    out.add_argument("out", type=Path, help="the map file, or a folder to write <id>.openrac-map in")
    out.add_argument("--author", default="", help="who made the map")
    out.add_argument("--about", default="", help="one line about the map")
    out.add_argument("--credit", action="append", default=[], help="credit for a model or texture you used (repeat)")
    out.add_argument("--disc", type=Path, help="the disc (default: the one the levels were read from)")
    out.add_argument("--anywhere", action="store_true", help="accepted for the launcher")
    out.add_argument("--keep-problems", action="store_true",
                     help="export even when the map has problems a player would see (check-map)")
    into = commands.add_parser("import", help="make a project of a shared map beside your levels")
    into.add_argument("map", type=Path, help="the .openrac-map file")
    into.add_argument("projects", type=Path, help="your levels folder (where level_NN projects are)")
    into.add_argument("--planet", type=Path, metavar="OVERRIDES",
                      help="then add it as a new planet to these mods (<data>/overrides)")
    into.add_argument("--disc", type=Path, help="the disc (default: the one the levels were read from)")
    into.add_argument("--anywhere", action="store_true", help="accepted for the launcher")
    args = parser.parse_args(argv)
    import errors
    from formats import FormatError
    try:
        if args.command == "export":
            target = args.out
            if target.is_dir() or target.suffix != SUFFIX:
                target.mkdir(parents=True, exist_ok=True)
                name = planet.planet_name(args.project.resolve())
                target = target / (planet.planet_id(args.project.resolve(), name) + SUFFIX)
            done = export(args.project, target, args.author, args.about, args.credit, args.disc, args.keep_problems)
            print(f"Map {done['name']} (level {done['level']:02}, {done['disc'].upper()} disc): "
                  f"{done['scenes']} scene(s) changed, {done['patches']} file(s) patched, "
                  f"{done['game']} game file(s) named, {len(done['own'])} of your own file(s), "
                  f"{done['removed']} removed.")
            for rel in done["own"]:
                print(f"  your file: {rel}")
            print("No game data is inside: the player's own disc gives it back at import.")
            print(f"openrac-output: {done['out'].parent}")
            return 0
        done = import_map(args.map, args.projects, args.disc)
        print(f"Imported the map {done['name']}" + (f" by {done['author']}" if done["author"] else "") +
              f" into {done['project']}.")
        for line in done["credits"]:
            print(f"  credit: {line}")
        for line in done["problems"]:
            print(f"  check: {line}")
        if done["backup"]:
            print(f"The map's earlier copy went to {done['backup']}.")
        if args.planet:
            import overrides
            from disc import Disc
            with Disc(done["disc"]) as disc:
                try:
                    # Its maker's export checked it (check-map); a player adding it is not asked to fix it.
                    added = planet.add_planet(disc, disc.survey(), done["project"], args.planet.resolve(),
                                              keep_problems=True)
                except overrides.PackError as exc:
                    raise FormatError(str(exc), code=planet.code_of(exc)) from exc
                print(f"Added it to your mods as the planet {added['name']}: turn on Mods, board the ship "
                      "and pick it in the ship's menu.")
            if needs_runtime(done["project"], done["level"]):
                import openrac_editor
                if openrac_editor.turn_on_runtime(args.planet.resolve().parent):
                    print("Turned on the editor-runtime mod: this map's moving objects and cutscenes need it.")
                else:
                    print("  check: this map needs the editor-runtime mod: turn it on in Mods.")
        print(f"openrac-output: {done['project']}")
        return 0
    except (MapError, planet.PlanetError, FormatError, OSError, struct.error, zipfile.BadZipFile) as exc:
        errors.say(exc)
        print(f"map: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
