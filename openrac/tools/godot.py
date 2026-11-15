"""Write extracted levels as a Godot 4 project that the editor opens and edits.

Each level is a scene, levels/level_NN/level_NN.tscn:

    Level_NN              rc1/level.gd: fly camera when the scene is run
      WorldEnvironment    the sky, baked into sky.png, and flat ambient light
      Sun                 a directional light for the editor and the fly camera
      Game                game axes (Z up) rotated into Godot's (Y up)
        Terrain           terrain.glb, one node per fragment (editable children)
        Collision         collision.glb, hidden: the baked collision mesh by surface type
        Ties/Tie_NNNN     instances of ties/tie_<class>.glb
        Shrubs/Shrub_NNNN instances of shrubs/shrub_<class>.glb
        Mobys/<Class>_NNNN   instances of mobys/moby_<class>.tscn: the class's
                          mesh (or a box) and a label naming the class

Placements are node transforms in game units; fields the game stores
per instance are node metadata (rc1_*), so a packer can write them back.

Going Commando's, Up Your Arsenal's and Deadlocked's levels (wads.py) are written the same way,
with the same rc1_* metadata names, plus rc1_game on the level node. Their scenes are for looking
and making texture and model packs: the level packer reads Ratchet & Clank's only, which the
level node says (rc1_packable, and its description in the Scene dock).
"""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import colorsys
import hashlib
import json
import shutil
from pathlib import Path

import effects
import collision_scene
from formats import FormatError, unpack
from lighting import light_bank, light_shrub_instance, light_tie_instance
import gamedata
import games
import levelwrite
from levelwrite import (COLLISION_ALPHA, GAME_TO_GODOT, MIN_SCALE, PANORAMA, STORED_W, Raw, Scene,  # noqa: F401
                        apply, chunk_terrain, collision_glb, number, problem_summary, transform, value)
from mesh import Mesh
from moby_class import MobyClass, moby_classes
import moby_collision_scene
from mobys import LEVEL_NAMES, moby_logic, node_name, moby_class_names, moby_instances, readable_names
from shrubs import shrub_class_normals, shrub_classes, shrub_instances
from sky import level_sky
from terrain import terrain
from ties import tie_classes, tie_instances, tie_slot_normals

# What every level project gets from the editor: its scripts and shaders, its docks, its format.
PROJECT_FILES = Path(__file__).resolve().parents[1] / "project"
SCRIPTS = PROJECT_FILES / "rc1"
# The editor plugins every project enables: the Collision dock (addons/openrac_collision) and the
# Assets dock (addons/openrac_library). Editor tooling only.
ADDON = PROJECT_FILES / "addons" / "openrac_collision"
ADDON_CFG = "res://addons/openrac_collision/plugin.cfg"
LIBRARY_ADDON = PROJECT_FILES / "addons" / "openrac_library"
LIBRARY_ADDON_CFG = "res://addons/openrac_library/plugin.cfg"
ADDONS = ((ADDON, ADDON_CFG), (LIBRARY_ADDON, LIBRARY_ADDON_CFG))
# level.json's schema: what a Godot level of this extractor holds. Raised when the scene changes in
# a way the packer or a newer extractor tells apart (3: the editable collision); extract.py writes a
# level of an older schema again, after backing it up with its edits (and carry.py carries them
# over). It is in project_format.json, which the launcher reads too, to say a project is outdated.
PROJECT_FORMAT_FILE = PROJECT_FILES / "project_format.json"
PROJECT_FORMAT = json.loads(PROJECT_FORMAT_FILE.read_text())
SCHEMA = PROJECT_FORMAT["schema"]
NANOTECH_CRATE = 501  # Ratchet & Clank's nanotech crate: its ball is drawn by code (effects.py)
ELECTRIC_GATE = 838  # Rilgar's electric gate: its beams are drawn by code (effects.py)
# project.godot's [rendering] buffer for instance shader parameters: each tie's and shrub's light
# row and each moby's light (rc1/lit_object.gdshader, rc1/lit_moby.gdshader) take room in it.

PROJECT = """config_version=5

[application]

config/name="{name}"
run/main_scene="{main}"

[importer_defaults]

scene={{
"meshes/create_shadow_meshes": false,
"meshes/ensure_tangents": false,
"meshes/force_disable_compression": true,
"meshes/generate_lods": false,
"import_script/path": "res://rc1/import_lit.gd"
}}
texture={{
"compress/mode": 0,
"detect_3d/compress_to": 0,
"mipmaps/generate": true
}}

[rendering]

limits/global_shader_variables/buffer_size=1048576

[editor_plugins]

enabled=PackedStringArray({addons})
"""


class LevelWriter(levelwrite.LevelWriter):
    """The level as a Godot project's scene: levelwrite's models and textures, with the game's
    gameplay data as node metadata (gamedata.py), the editable collision and the class scenes."""

    schema = SCHEMA
    # The collision as tiles to edit (collision_scene.py) where the level packs back.
    editable_collision = True
    # Zero joint scales become MIN_SCALE in the models (scale()).
    godot_scales = True

    def write(self, lod: int) -> dict:
        level, fmt = self.level, self.fmt
        self.dir.mkdir(parents=True)
        script = self.scene.resource("Script", "res://rc1/level.gd", "level")
        if fmt.packable:
            self.scene.node(f"Level_{level.id:02}", kind="Node3D", script=script, metadata__rc1_level=level.id)
        else:
            # A later game's level: shown and exported, not packed back yet (pack.py).
            self.scene.node(f"Level_{level.id:02}", kind="Node3D", script=script, metadata__rc1_level=level.id,
                            metadata__rc1_game=fmt.game, metadata__rc1_packable=False,
                            editor_description=f"{fmt.title}, level {level.id} (early access).\n"
                            "Static scenery, mobys and textures to look at and to make texture and model "
                            "packs from. Edits here are not packed back into the game yet: the level "
                            "packer reads Ratchet & Clank's levels only.")
        problems = level.problems if fmt.lenient else None
        self.write_environment(level_sky(level, fmt))
        self.scene.node("Game", ".", "Node3D", transform=transform(GAME_TO_GODOT))
        # The terrain lit as the game lights it at level load: Ratchet & Clank's (lighting) and Going
        # Commando's (world_lighting), as the port's writer does; the other games' go out unlit.
        lit = self.normals is not None and (fmt.lighting or fmt.world_lighting)
        lights = (light_bank(level.gameplay), self.normals) if lit else None
        self.write_terrain(terrain(level.block(unpack("<I", level.index, 0x08)[0]), lod, lights, problems)
                           + chunk_terrain(level, lod, lights, problems), lod)
        ties = tie_classes(level)
        tie_placements = tie_instances(level.instances, ties, fmt, problems)
        shrubs = shrub_classes(level)
        shrub_placements = shrub_instances(level.instances, shrubs, fmt, problems)
        if lights is not None:
            # Ties and shrubs lit as the game lights them at level load (as the port's writer does):
            # each instance's 64 (tie) or 24 (shrub) colours, one per light slot of its class.
            tie_normals = tie_slot_normals(level) if fmt.lighting else None
            for p in tie_placements if tie_normals is not None else ():
                p["colours"] = light_tie_instance(tie_normals[p["class_id"]], p["matrix"], p["ambient"],
                                                  p["directional_lights"], lights[0])
            if self.godot_scales:
                self.write_light_bank(lights[0])
            shrub_normals = shrub_class_normals(level)
            for p in shrub_placements:
                p["colours"] = light_shrub_instance(shrub_normals[p["class_id"]], p["matrix"], p["colour"],
                                                    p["directional_lights"], lights[0])
        self.write_objects("tie", ties, tie_placements)
        self.write_objects("shrub", shrubs, shrub_placements)
        if fmt.packable:  # the gameplay data, packed back by pack.py (gamedata.py)
            self.moby_extra = gamedata.moby_metadata(level.gameplay)
        self.write_mobys(moby_instances(level.gameplay, fmt), moby_classes(level))
        if fmt.packable:
            self.stats["gameplay"] = gamedata.write_scene(self.scene, self.dir, self.res, level.gameplay)
        collision, = unpack("<I", level.index, 0x14)
        if collision:  # Deadlocked's multiplayer menu has none
            self.write_collision(level.block(collision), [block for _, _, block in level.chunks if block])
        return self.finish()

    def write_nanotech(self) -> None:
        """The nanotech ball's tables and effect textures (effects.py) for rc1/nanotech.gd:
        effects/nanotech.json, effects/fx_<n>.png. Nothing when the level's program has none."""
        tables = effects.nanotech(self.level)
        if tables is None:
            return
        spark = effects.spark_texture(self.level)
        pictures = {"ball": (f"fx_{effects.BALL_TEXTURE}.png", effects.effect_texture(self.level, effects.BALL_TEXTURE)),
                    "ring": (f"fx_{effects.RING_TEXTURE}.png", effects.effect_texture(self.level, effects.RING_TEXTURE)),
                    "spark": (f"part_{spark}.png", None if spark is None else effects.particle_texture(self.level, spark))}
        if any(texture is None for _, texture in pictures.values()):
            return
        out = self.dir / "effects"
        out.mkdir(exist_ok=True)
        for file, texture in pictures.values():
            (out / file).write_bytes(texture.png())
        tables["textures"] = {key: file for key, (file, _) in pictures.items()}
        (out / "nanotech.json").write_text(json.dumps(tables) + "\n")

    def write_electric_gate(self) -> None:
        """The electric gate's beams (effects.py) for rc1/electric_gate.gd: effects/electric_gate.json
        and its effect texture, effects/fx_<n>.png. Nothing when the level's program has none."""
        gate = effects.electric_gate(self.level)
        texture = None if gate is None else effects.effect_texture(self.level, gate["texture"])
        if texture is None:
            return
        out = self.dir / "effects"
        out.mkdir(exist_ok=True)
        gate["picture"] = f"fx_{gate['texture']}.png"
        (out / gate["picture"]).write_bytes(texture.png())
        (out / "electric_gate.json").write_text(json.dumps(gate) + "\n")

    def write_mobys(self, placements: list[dict], classes: dict[int, MobyClass | None]) -> None:
        """A scene per moby class the level loads, mobys/moby_<class>.tscn,
        and an instance of it per placed moby.

        A class scene shows the class's high-LOD mesh (mobys/moby_<class>.glb,
        with its skeleton and animations) at the class scale, or a box for a
        class without one, and a label.
        Moby meshes are counted on their own, not in the level geometry
        totals and bounds.

        rotation_order XYZ makes the Inspector show the game's own Euler
        angles (R = Rz * Ry * Rx); the transform itself is exact either way.
        """
        # The class names and update functions are Ratchet & Clank's (moby_classes.tsv, games/rac1/decomp):
        # another game's class numbers mean other classes.
        names = moby_class_names() if self.fmt.class_names else {}
        readable = readable_names() if self.fmt.class_names else {}
        logic = moby_logic(self.level.id) if self.fmt.class_names else {}
        placed = {p["class_id"] for p in placements}
        scenes, models = {}, {}
        # Each class's collision (moby_collision_scene.py) where the level packs back: the class
        # blobs in the core (a gadget's is compressed and not packed, so not shown).
        starts = ({unpack("<i", e, 4)[0]: unpack("<i", e)[0] for e in self.level.table(0x18, 32)}
                  if self.editable_collision else {})
        collision = {"classes": 0, "primitives": 0, "triangles": 0}
        written, files = {}, {}
        for class_id in sorted(set(classes) | placed):
            moby, model = classes.get(class_id), None
            if moby is not None and moby.mesh is not None:
                models[class_id] = moby
                model = (self.moby_glb(f"mobys/moby_{class_id}.glb", moby), moby.scale,
                         moby.mesh.bounds()[1][2] * moby.scale)
            path = f"mobys/moby_{class_id}.tscn"
            label = readable.get(class_id) or names.get(class_id)
            write_marker(self.dir / path, class_id, f"{class_id} {label}" if label else str(class_id),
                         model, names.get(class_id), logic.get(class_id),
                         self.class_collision(starts.get(class_id, 0), class_id, collision, files))
            # Every class scene, with class data or not: an untouched project is then seen as one
            # without reading the disc (moby_collision_scene.unchanged_scenes).
            written[class_id] = path
            if class_id in placed:
                scenes[class_id] = self.scene.resource("PackedScene", f"{self.res}/{path}", f"moby_{class_id}")
        self.scene.node("Mobys", "Game", "Node3D")
        if self.godot_scales and (self.dir / "light_bank.json").is_file():
            # The classes' glow colours (rc1/import_lit.gd gives a class with one its glow pass).
            glow = {str(c): [m.glow_rgba & 0xFF, (m.glow_rgba >> 8) & 0xFF, (m.glow_rgba >> 16) & 0xFF]
                    for c, m in models.items() if m.glow_rgba}
            (self.dir / "moby_glow.json").write_text(json.dumps(glow) + "\n")
        if self.godot_scales and self.fmt.class_names and NANOTECH_CRATE in placed:
            self.write_nanotech()
        if self.godot_scales and self.fmt.class_names and ELECTRIC_GATE in placed:
            self.write_electric_gate()
        for p in placements:
            fields = {f"metadata__rc1_{k}": v for k, v in {"index": p["index"], **p["fields"]}.items()}
            fields.update(self.moby_extra.get(p["index"], {}))
            self.scene.node(node_name(p["class_id"], p["index"], names), "Game/Mobys", instance=scenes[p["class_id"]],
                            rotation_order=0, transform=transform(p["matrix"]), **fields)
        # A class's metal packets are a mesh of their own in its model, and Godot shows both.
        def meshes(m: MobyClass) -> list[Mesh]:
            return [m.mesh] + ([m.metal] if m.metal is not None else [])
        shown = [mesh for p in placements if p["class_id"] in models for mesh in meshes(models[p["class_id"]])]
        animated = [m.skeleton for m in models.values() if m.skeleton is not None]
        self.stats["mobys"] = {"classes": len(set(classes) | placed),
                               "named_classes": sum(c in names for c in set(classes) | placed),
                               "instances": len(placements), "model_classes": len(models),
                               "class_triangles": sum(mesh.triangles for m in models.values() for mesh in meshes(m)),
                               "textures": len({key for m in models.values() for mesh in meshes(m) for key in mesh.faces}),
                               "mesh_instances": len(shown), "triangles": sum(m.triangles for m in shown),
                               "animated_classes": len(animated),
                               "bones": sum(len(s.joints) + sum(j.post_scale for j in s.joints) for s in animated),
                               "animations": sum(len(s.animations) for s in animated)}
        if self.editable_collision:
            self.stats["mobys"]["collision"] = collision
            moby_collision_scene.write_manifest(self.dir, written, files)

    def class_collision(self, start: int, class_id: int, totals: dict, files: dict):
        """What adds a class's Collision node to its class scene (write_marker's COLLISION), or None
        for a class without data in the core."""
        if start <= 0:
            return None
        core = self.level.core
        try:
            _at, blob = moby_collision_scene.class_collision(core, start)
            joints = moby_collision_scene.joint_positions(core, start)
        except FormatError as e:
            self.level.problems.append(f"moby class {class_id}: collision not extracted ({e})")
            return None
        scale, = unpack("<f", core, start + 0x24)

        def add(scene) -> None:
            stats = moby_collision_scene.add_class_collision_nodes(scene, blob, joints, scale, class_id, self.dir, self.res)
            totals["classes"] += 1 if blob is not None else 0
            totals["primitives"] += stats["primitives"]
            totals["triangles"] += stats["triangles"]
            files.update(stats["files"])
        return add

    def write_editable_collision(self, block: bytes) -> None:
        """Ratchet & Clank's levels: tiles and hero walls the packer reads back (collision_scene.py)."""
        manifest = collision_scene.export(self.dir, block)
        self.stats["collision"]["hero_triangles"] = sum(h["triangles"] for h in manifest["hero_walls"].values())
        collision_scene.add_collision_nodes(self.scene, self.res, manifest)


def write_marker(path: Path, class_id: int, label: str, model: tuple[str, float, float] | None = None,
                 name: str | None = None, logic: dict | None = None, collision=None) -> None:
    """A moby class's scene: its model, or else a box coloured by class
    number, and a label that faces the camera and fades out beyond 60 units.

    model is (GLB res:// path, class scale, top of the scaled mesh). The
    GLB is in model units, so the Model node carries the class scale.
    COLLISION, when given, adds the class's hidden Collision node (moby_collision_scene.py).
    """
    scene = Scene()
    # What the class is and what runs it, where the Inspector and a hover in the scene tree show
    # them: the function the game calls for each of its mobys every frame, in the decompilation.
    about, facts = [f"Moby class {class_id}" + (f": {name}" if name else "")], {}
    if name:
        facts["metadata__rc1_class_name"] = name
    if logic:
        facts["metadata__rc1_update"] = logic["function"]
        about.append(f"Updated every frame by {logic['function']}")
        if logic["file"]:
            facts["metadata__rc1_source"] = logic["file"]
            about.append(("C: " if logic["decompiled"] else "Still assembly, in ") + logic["file"])
    elif name is not None or model is not None:
        about.append("No update function in this level")
    if model is not None:
        # Lights the model as the game lights the moby, from its rc1_colour and rc1_light (moby.gd).
        facts["script"] = scene.resource("Script", "res://rc1/moby.gd", "light")
    scene.node(f"Moby_{class_id}", kind="Node3D", metadata__rc1_class=class_id,
               editor_description="\n".join(about), **facts)
    if model is None:
        r, g, b = colorsys.hsv_to_rgb(class_id * 0.618034 % 1.0, 0.65, 0.95)
        colour = scene.subresource("StandardMaterial3D", "colour",
                                   albedo_color=Raw(f"Color({number(r)}, {number(g)}, {number(b)}, 1)"))
        box = scene.subresource("BoxMesh", "box", material=colour, size=Raw("Vector3(0.5, 0.5, 0.5)"))
        scene.node("Marker", ".", "MeshInstance3D",
                   transform=Raw("Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0.25)"), mesh=box)
        height = 0.9
    else:
        glb, scale, top = model
        scene.node("Model", ".", instance=scene.resource("PackedScene", glb, "model"),
                   transform=transform([scale, 0, 0, 0, 0, scale, 0, 0, 0, 0, scale, 0, 0, 0, 0, 1]))
        height = max(top, 0.0) + 0.4
    scene.node("Label", ".", "Label3D", transform=transform([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, height, 1]),
               visibility_range_end=60.0, billboard=1, pixel_size=0.006, text=label, font_size=48)
    if collision is not None:
        collision(scene)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scene.text())


# What a level project has of this editor's own files (openrac/project), by their digest
# (project_files): a project made by an older editor gets the new ones when it is opened.
PROJECT_STAMP = Path(".openrac") / "project_files"


def project_files() -> str:
    """A digest of the files this editor puts in every level's project (rc1/ and the addons)."""
    h = hashlib.sha256()
    for path in sorted(p for p in PROJECT_FILES.rglob("*") if p.is_file() and p.suffix != ".uid"
                       and "__pycache__" not in p.parts):
        h.update(path.relative_to(PROJECT_FILES).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()[:16]


def write_project(project: Path, levels: list[int], region: str = "PAL", game: str = "rac1") -> None:
    """project.godot and the rc1 scripts; the main scene is the first level. A project of one
    level is named after it, and the disc's region (disc.Version.region); GAME names the game
    (games.FORMATS) for the others'."""
    project.mkdir(parents=True, exist_ok=True)
    main = f"res://levels/level_{levels[0]:02}/level_{levels[0]:02}.tscn"
    if game == "rac1":
        name = (f"Ratchet & Clank: {LEVEL_NAMES[levels[0]]} ({region})" if len(levels) == 1
                else f"Ratchet & Clank levels ({region})")
    else:
        fmt = games.FORMATS[game]
        name = (f"{fmt.title}: {fmt.level_name(levels[0])} ({region}, early access)" if len(levels) == 1
                else f"{fmt.title} levels ({region}, early access)")
    (project / "project.godot").write_text(PROJECT.format(main=main, name=name, addons=", ".join(f'"{cfg}"' for _, cfg in ADDONS)))
    shutil.copytree(SCRIPTS, project / "rc1")
    install_addon(project)
    (project / PROJECT_STAMP).parent.mkdir(exist_ok=True)
    (project / PROJECT_STAMP).write_text(project_files() + "\n")


def install_addon(project: Path) -> None:
    """The editor plugins' files in a project, enabled in its project.godot (an older export's
    too: extract.py's update calls this, and a project.godot that names other plugins keeps them)."""
    # Each goes in front, so the list keeps ADDONS' order.
    for folder, cfg in reversed(ADDONS):
        shutil.copytree(folder, project / "addons" / folder.name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("*.uid"))
        settings = project / "project.godot"
        text = settings.read_text()
        if cfg in text:
            continue
        if "[editor_plugins]" not in text:
            text = text.rstrip("\n") + f'\n\n[editor_plugins]\n\nenabled=PackedStringArray("{cfg}")\n'
        elif "enabled=PackedStringArray(" in text:
            text = text.replace("enabled=PackedStringArray(", f'enabled=PackedStringArray("{cfg}", ', 1)
            text = text.replace(f'"{cfg}", )', f'"{cfg}")')
        else:
            text = text.replace("[editor_plugins]\n", f'[editor_plugins]\n\nenabled=PackedStringArray("{cfg}")\n', 1)
        settings.write_text(text)
