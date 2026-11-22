# SPDX-License-Identifier: MIT
# Copyright (c) 2026 the OpenRAC contributors
"""Shared maps: byte patches, scene changes, and an export and import with no game data inside."""
import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import zipfile

import shared_map

SCENE = """[gd_scene format=3]

[ext_resource type="Script" path="res://rc1/level.gd" id="level"]
[ext_resource type="PackedScene" path="res://levels/level_02/mobys/moby_13.tscn" id="moby_13"]

[node name="Level_02" type="Node3D"]
script = ExtResource("level")
metadata/rc1_level = 2

[node name="Game" type="Node3D" parent="."]

[node name="Mobys" type="Node3D" parent="Game"]

[node name="Bolt_0010" parent="Game/Mobys" instance=ExtResource("moby_13")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 137.00002, 233, 31)
metadata/rc1_spawn_id = 114
metadata/rc1_pvar = [7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7]

[node name="Bolt_0011" parent="Game/Mobys" instance=ExtResource("moby_13")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 139, 229, 31)
metadata/rc1_spawn_id = 115
metadata/rc1_pvar = [9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9]
"""


def edited_scene() -> str:
    """SCENE as Godot saves it after an edit: uids and unique ids added, floats its own way."""
    text = SCENE.replace('id="moby_13"]', 'uid="uid://b1" id="moby_13"]')
    text = text.replace('[node name="Game" type="Node3D" parent="."]', '[node name="Game" type="Node3D" parent="." unique_id=55]')
    text = text.replace("137.00002, 233, 31", "150.5, 233, 35").replace("139, 229, 31", "139.0000001, 229, 31")
    text = text.replace('[ext_resource type="PackedScene" path="res://levels/level_02/mobys/moby_13.tscn"',
                        '[ext_resource type="PackedScene" path="res://models/rock.glb" id="r1"]\n'
                        '[ext_resource type="PackedScene" path="res://levels/level_02/mobys/moby_13.tscn"')
    copy = SCENE.split('[node name="Bolt_0011"')[1].replace("139, 229, 31", "160, 227, 40")
    return (text + '\n[node name="Bolt_0011_2"' + copy +
            '\n[node name="Rock" parent="Game" instance=ExtResource("r1")]\ntransform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 2, 3)\n')


class Patches(unittest.TestCase):
    def test_round_trip_holds_only_new_bytes(self):
        base = os.urandom(200_000)
        target = base[:1000] + b"creator's own bytes" + base[1000:150_000] + base[150_100:]
        patch = shared_map.make_patch(base, target)
        self.assertEqual(shared_map.apply_patch(base, patch), target)
        self.assertLess(len(patch), 200)
        self.assertIn(b"creator's own bytes", patch)

    def test_refuses_another_base_and_a_broken_patch(self):
        base, target = os.urandom(5000), os.urandom(5000)
        patch = shared_map.make_patch(base, target)
        with self.assertRaises(shared_map.MapError):
            shared_map.apply_patch(os.urandom(5000), patch)
        with self.assertRaises(shared_map.MapError):
            shared_map.apply_patch(base, patch[:-10])
        self.assertEqual(shared_map.apply_patch(b"", shared_map.make_patch(b"", b"abc")), b"abc")


class Scenes(unittest.TestCase):
    def test_changes_hold_edits_only_and_apply_back(self):
        changes = shared_map.scene_changes(SCENE, edited_scene())
        text = json.dumps(changes)
        # The copy names what it copies; the game's pvar words stay out.
        self.assertNotIn("[9, 9, 9", text)
        self.assertNotIn("[7, 7, 7", text)
        added = {e["key"]: e for e in changes["added"]}
        self.assertEqual(added["node:Game/Mobys/Bolt_0011_2"]["base"], "node:Game/Mobys/Bolt_0011")
        # Godot's own ids and its float spelling are not edits.
        self.assertEqual([e["key"] for e in changes["changed"]], ["node:Game/Mobys/Bolt_0010"])
        problems = []
        made = shared_map.apply_scene_changes(SCENE, changes, problems)
        self.assertEqual(problems, [])
        again = shared_map.scene_changes(SCENE, made)
        self.assertEqual(again["removed"], [])
        self.assertEqual(sorted(e["key"] for e in again["added"]), sorted(added))
        self.assertIn('path="res://models/rock.glb"', made)
        self.assertIn("160, 227, 40", made)
        self.assertIn("[9, 9, 9", made.split('name="Bolt_0011_2"')[1])

    def test_removed_node_and_a_change_for_a_missing_node(self):
        text = SCENE.split('[node name="Bolt_0011"')[0]
        changes = shared_map.scene_changes(SCENE, text)
        self.assertEqual(changes["removed"], ["node:Game/Mobys/Bolt_0011"])
        changes["changed"].append({"key": "node:Game/Nothing", "set": {"visible": "false"}})
        problems = []
        made = shared_map.apply_scene_changes(SCENE, changes, problems)
        self.assertNotIn("Bolt_0011", made)
        self.assertEqual(len(problems), 1)


def project(root: Path, level: int, files: dict) -> Path:
    folder = root / f"level_{level:02}"
    for rel, data in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(data if isinstance(data, bytes) else data.encode())
    return folder


def disc_files(level: int) -> dict:
    name = f"levels/level_{level:02}"
    return {"project.godot": 'config_version=5\n\n[application]\n\nconfig/name="Level"\n',
            f"{name}/level.json": json.dumps({"schema": 3, "game": "rac1", "version": "pal"}),
            f"{name}/level_{level:02}.tscn": SCENE.replace("level_02", f"level_{level:02}"),
            f"{name}/textures/terrain_0001.png": b"\x89PNG game texture %d" % level + os.urandom(64),
            f"{name}/collision/hero_000.glb": b"glTF game collision" + os.urandom(4000),
            f"{name}/ties/tie_7.glb": b"glTF game tie %d" % level + os.urandom(3000)}


class ExportImport(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        # What the disc gives: the same files every time a level is read.
        self.disc = {n: disc_files(n) for n in (2, 5)}

        def extract(_disc, levels, into):
            for n in levels:
                project(Path(into), n, self.disc[n])
            return Path(into)

        disc = mock.MagicMock()
        disc.return_value.__enter__.return_value.version.key = "pal"
        self.patches = [mock.patch.object(shared_map, "extract_levels", side_effect=extract),
                        mock.patch("disc.Disc", disc)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def creator(self) -> Path:
        projects = self.root / "creator"
        for n in (2, 5):
            project(projects, n, self.disc[n])
        level = projects / "level_02"
        scene = level / "levels/level_02/level_02.tscn"
        scene.write_text(edited_scene(), encoding="utf-8")
        hero = level / "levels/level_02/collision/hero_000.glb"
        hero.write_bytes(hero.read_bytes()[:2000] + b"moved face" + hero.read_bytes()[2010:])
        (level / "models").mkdir()
        (level / "models/rock.glb").write_bytes(b"glTF the creator's rock")
        (level / "models/rock.glb.import").write_text("[remap]\nimporter=\"scene\"\n")
        # A texture of another level, copied in by hand: named, never carried.
        shutil.copy(projects / "level_05/levels/level_05/textures/terrain_0001.png", level / "models/borrowed.png")
        (level / "levels/level_02/textures/terrain_0001.png.import").write_text("[remap]\n")
        (level / ".godot").mkdir()
        (level / ".godot/cache.bin").write_bytes(b"cache")
        import planet
        planet.set_setting(level, planet.NAME, "Test World")
        return level

    def test_export_has_no_game_bytes_and_import_gives_the_same_project(self):
        level = self.creator()
        done = shared_map.export(level, self.root / "out" / "test.openrac-map", author="Me",
                                 disc_path=self.root / "disc.iso")
        self.assertEqual(sorted(done["own"]), ["models/rock.glb", "models/rock.glb.import"])
        with zipfile.ZipFile(done["out"]) as archive:
            inside = b"".join(archive.read(n) for n in archive.namelist())
            manifest = json.loads(archive.read("map.json"))
        for n in (2, 5):
            for rel, data in self.disc[n].items():
                if isinstance(data, bytes) and len(data) > 60:
                    self.assertNotIn(data[-60:], inside, rel)
        self.assertNotIn(b"cache", inside)
        self.assertEqual(manifest["author"], "Me")
        kinds = {e["path"]: e["kind"] for e in manifest["files"]}
        self.assertEqual(kinds["models/borrowed.png"], "game")
        self.assertEqual(kinds["levels/level_02/collision/hero_000.glb"], "patch")
        self.assertEqual(kinds["levels/level_02/level_02.tscn"], "scene")
        # The player: their own levels, from their own disc.
        player = self.root / "player"
        for n in (2, 5):
            project(player, n, self.disc[n])
        got = shared_map.import_map(done["out"], player, disc_path=self.root / "disc.iso")
        self.assertEqual(got["problems"], [])
        made = got["project"]
        self.assertEqual(made.name, f"level_02-{manifest['id']}")
        for rel in ("models/rock.glb", "models/borrowed.png", "levels/level_02/collision/hero_000.glb"):
            self.assertEqual((made / rel).read_bytes(), (level / rel).read_bytes(), rel)
        self.assertEqual(shared_map.scene_changes((level / "levels/level_02/level_02.tscn").read_text(),
                                                  (made / "levels/level_02/level_02.tscn").read_text())["removed"], [])
        self.assertEqual((player / "level_02/levels/level_02/level_02.tscn").read_text(), self.disc[2]["levels/level_02/level_02.tscn"])
        import planet
        self.assertEqual(planet.settings(made)[planet.ID], manifest["id"])
        self.assertEqual(planet.settings(made)[shared_map.AUTHOR], "Me")
        # Adding it again keeps the earlier copy in the backups.
        again = shared_map.import_map(done["out"], player, disc_path=self.root / "disc.iso")
        self.assertTrue(again["backup"].is_dir())

    def test_import_refuses_another_disc_and_paths_out_of_the_project(self):
        level = self.creator()
        done = shared_map.export(level, self.root / "test.openrac-map", disc_path=self.root / "disc.iso")
        player = self.root / "player"
        project(player, 2, self.disc[2])
        Disc = __import__("disc").Disc
        Disc.return_value.__enter__.return_value.version.key = "ntsc"
        with self.assertRaisesRegex(shared_map.MapError, "PAL disc"):
            shared_map.import_map(done["out"], player, disc_path=self.root / "disc.iso")
        Disc.return_value.__enter__.return_value.version.key = "pal"
        bad = self.root / "bad.openrac-map"
        with zipfile.ZipFile(done["out"]) as source, zipfile.ZipFile(bad, "w") as out:
            manifest = json.loads(source.read("map.json"))
            manifest["files"] = [{"path": "../evil.txt", "kind": "file", "data": "files/x", "sha256": ""}]
            out.writestr("map.json", json.dumps(manifest))
        with self.assertRaisesRegex(shared_map.MapError, "outside"):
            shared_map.import_map(bad, player, disc_path=self.root / "disc.iso")
        self.assertFalse((self.root / "evil.txt").exists())

    def test_source_of_copied_models(self):
        self.assertEqual(shared_map.source_of("library/rac1/pal/level_05/ties/tie_7.glb", self.root),
                         (5, "levels/level_05/ties/tie_7.glb"))
        self.assertIsNone(shared_map.source_of("library/skins/rac1/pal/level_05/mobys/m.glb", self.root))


if __name__ == "__main__":
    unittest.main()
