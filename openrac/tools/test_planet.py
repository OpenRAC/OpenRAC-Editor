"""planet.py: a level project added as a new planet (the launcher's "Add as a new planet"), on
hand-built projects with the packer stood in for (no disc), and on the PAL disc when
OPENRAC_RAC1_PAL_DISC names it."""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import overrides
import planet
from openrac_root import ROOT
from test_overrides import glb as model

HERE = Path(__file__).resolve().parent

PROJECT = """; Engine configuration file.
config_version=5

[application]

config/name="Ratchet & Clank: Aridia (Europe)"

[editor_plugins]

enabled=PackedStringArray("res://addons/openrac_collision/plugin.cfg")
"""

# A level scene as the extractor writes it: Ratchet's start and the ship that flies back.
SCENE = """[gd_scene format=3]

[ext_resource type="PackedScene" path="res://levels/level_02/mobys/moby_0.tscn" id="moby_0"]
[ext_resource type="PackedScene" path="res://levels/level_02/gameplay/ship.tscn" id="gameplay_ship"]

[node name="Level" type="Node3D"]

[node name="Game" type="Node3D" parent="."]

[node name="Mobys" type="Node3D" parent="Game"]

{heroes}
[node name="LevelSettings" type="Node3D" parent="Game"]

{ship}
"""

HERO = """[node name="Ratchet_{n:04}" parent="Game/Mobys" instance=ExtResource("moby_0")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 210.5, 170.25, 25)
"""

SHIP = """[node name="Ship" parent="Game/LevelSettings" instance=ExtResource("gameplay_ship")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 205, 154, 26)
"""


def glb() -> bytes:
    """A terrain: test_overrides' model without its skin."""
    doc, binary = overrides.glb_parts(model())
    doc.pop("skins", None)
    for node in doc["nodes"]:
        node.pop("skin", None)
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            primitive["attributes"].pop("JOINTS_0", None)
            primitive["attributes"].pop("WEIGHTS_0", None)
    return overrides.glb_join(doc, binary)


def level_project(root: Path, level: int = 2, heroes: int = 1, ship: bool = True) -> Path:
    """One level's project, as `godot --split` writes it."""
    project = root / f"level_{level:02}"
    folder = project / "levels" / f"level_{level:02}"
    folder.mkdir(parents=True)
    (project / "project.godot").write_text(PROJECT, encoding="utf-8")
    scene = SCENE.format(heroes="\n".join(HERO.format(n=n) for n in range(heroes)), ship=SHIP if ship else "")
    (folder / f"level_{level:02}.tscn").write_text(scene.replace("level_02", f"level_{level:02}"), encoding="utf-8")
    (folder / "terrain.glb").write_bytes(glb())
    return project


def fake_pack(refuse: str = ""):
    """pack.pack's stand-in: one level's gameplay file and the report pack.py writes."""
    def pack(_disc, _survey, project, out, _names):
        level = planet.level_of(project)
        prefix = f"level_{level:02}"
        (out / prefix).mkdir()
        (out / prefix / "gameplay_pal.wad").write_bytes(bytes(2048))
        (out / "patches.tsv").write_text(f"1000\t1\t{prefix}/gameplay_pal.wad\n")
        (out / "packed.json").write_text(json.dumps({"version": "pal"}))
        (out / "changes.txt").write_text(f"level {level:02}\n  Ratchet_0000: moved\n" + refuse)
        return 1, 1
    return pack


PAL = SimpleNamespace(version=SimpleNamespace(key="pal"))


class Settings(unittest.TestCase):
    def test_written_read_and_escaped(self):
        with tempfile.TemporaryDirectory() as temp:
            project = level_project(Path(temp))
            self.assertEqual(planet.settings(project), {})
            for name in ['Nova "Harbor"', "back\\slash", "Ünïcödé world", "a=b", "[not a section]", " spaced "]:
                with self.subTest(name=name):
                    planet.set_setting(project, planet.NAME, name)
                    self.assertEqual(planet.settings(project)[planet.NAME], name)
            # One [openrac] section, the other settings as they were.
            text = (project / "project.godot").read_text(encoding="utf-8")
            self.assertEqual(text.count("[openrac]"), 1)
            self.assertIn('config/name="Ratchet & Clank: Aridia (Europe)"', text)

    def test_a_value_godot_wrote_over_several_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            project = level_project(Path(temp))
            (project / "project.godot").write_text(
                PROJECT + '\n[openrac]\n\nplanet/name="two\nlines \\"quoted\\" end\\\\"\nplanet/id="a.1"\n',
                encoding="utf-8")
            self.assertEqual(planet.settings(project), {"planet/name": 'two\nlines "quoted" end\\', "planet/id": "a.1"})
            planet.set_setting(project, planet.NAME, "one line")
            self.assertEqual(planet.settings(project), {"planet/name": "one line", "planet/id": "a.1"})
            self.assertNotIn("lines", (project / "project.godot").read_text(encoding="utf-8"))

    def test_a_section_between_others_and_keys_of_other_sections(self):
        with tempfile.TemporaryDirectory() as temp:
            project = level_project(Path(temp))
            # As Godot saves it: [openrac] with another key, then more sections, and a planet/name
            # key in another section that is not ours.
            (project / "project.godot").write_text(
                PROJECT + '\n[openrac]\n\nlibrary/folders=PackedStringArray()\n\n[rendering]\n\nplanet/name="no"\n',
                encoding="utf-8")
            planet.set_setting(project, planet.ID, "x.1")
            planet.set_setting(project, planet.NAME, "Mine")
            self.assertEqual(planet.settings(project), {"planet/id": "x.1", "planet/name": "Mine"})
            text = (project / "project.godot").read_text(encoding="utf-8")
            self.assertLess(text.index("planet/name=\"Mine\""), text.index("[rendering]"))
            self.assertIn('planet/name="no"', text)
            planet.set_setting(project, planet.NAME, "Again")
            self.assertEqual(text.count("planet/"), (project / "project.godot").read_text(encoding="utf-8").count("planet/"))

    def test_carried_to_a_level_exported_again(self):
        with tempfile.TemporaryDirectory() as temp:
            old = level_project(Path(temp) / "backup")
            new = level_project(Path(temp) / "fresh")
            planet.set_setting(old, planet.NAME, "Kept")
            planet.set_setting(old, planet.ID, "kept.1234")
            planet.carry_settings(old, new)
            self.assertEqual(planet.settings(new), {"planet/name": "Kept", "planet/id": "kept.1234"})
            # Nothing to carry from a project with none, or that is gone.
            planet.carry_settings(Path(temp) / "nowhere", new)
            planet.carry_settings(level_project(Path(temp) / "plain"), new)
            self.assertEqual(planet.settings(new)["planet/name"], "Kept")


class Names(unittest.TestCase):
    def test_slugs(self):
        for name, slug in [("My World!", "my-world"), ("   ", "planet"), ("!!!", "planet"), ("Łódź 2", "d-2"),
                           ("a" * 80, "a" * 40), ("-a-", "a"), ("UPPER lower", "upper-lower")]:
            with self.subTest(name=name):
                self.assertEqual(planet.slug(name), slug)
                self.assertRegex(planet.slug(name), r"^[a-z0-9]+(-[a-z0-9]+)*$")

    def test_a_name_is_needed_and_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            project = level_project(Path(temp))
            for bad in ["", "   ", "x" * (planet.MOST_NAME + 1), "two\nlines", "tab\there"]:
                with self.subTest(name=bad):
                    planet.set_setting(project, planet.NAME, bad)
                    with self.assertRaises(planet.PlanetError) as caught:
                        planet.planet_name(project)
                    self.assertEqual(caught.exception.code, planet.UNNAMED)
            planet.set_setting(project, planet.NAME, "  Edge  ")
            self.assertEqual(planet.planet_name(project), "Edge")
            planet.set_setting(project, planet.NAME, "x" * planet.MOST_NAME)
            self.assertEqual(len(planet.planet_name(project)), planet.MOST_NAME)

    def test_the_id_is_made_once_and_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            project = level_project(Path(temp))
            first = planet.planet_id(project, "My World")
            self.assertRegex(first, r"^my-world\.[0-9a-f]{8}$")
            self.assertEqual(planet.planet_id(project, "Renamed"), first)
            other = level_project(Path(temp) / "other")
            self.assertNotEqual(planet.planet_id(other, "My World"), first)

    def test_one_level_per_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project = level_project(Path(temp), level=7)
            self.assertEqual(planet.level_of(project), 7)
            # A stray scene beside the level's is not a level.
            (project / "levels/level_07/level_07_copy.tscn").write_text("")
            self.assertEqual(planet.level_of(project), 7)
            other = project / "levels/level_08"
            other.mkdir()
            (other / "level_08.tscn").write_text("")
            with self.assertRaises(planet.PlanetError) as caught:
                planet.level_of(project)
            self.assertEqual(caught.exception.code, planet.NO_PROJECT)
            with self.assertRaises(planet.PlanetError):
                planet.level_of(Path(temp) / "empty")


class AddPlanet(unittest.TestCase):
    def add(self, project: Path, into: Path, refuse: str = "", disc=PAL) -> dict:
        with patch("pack.pack", fake_pack(refuse)):
            return planet.add_planet(disc, {}, project, into)

    def test_added_updated_and_a_second_planet(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = level_project(root / "projects")
            planet.set_setting(project, planet.NAME, "Harbor")
            into = root / "overrides"
            done = self.add(project, into)
            self.assertEqual((done["name"], done["level"]), ("Harbor", 2))
            self.assertEqual(done["spawn"], [210.5, 170.25, 25.0])
            packs = overrides.load_state(into)["packs"]
            self.assertEqual([p["id"] for p in packs], [done["installed"]])
            manifest = json.loads((into / "packs" / done["installed"] / overrides.MANIFEST).read_text(encoding="utf-8"))
            self.assertEqual((manifest["format"], manifest["name"]), (overrides.DESTINATION_PACK_FORMAT, "Harbor"))
            self.assertEqual(manifest["export"], "destination:" + planet.settings(project)[planet.ID])
            self.assertEqual(manifest["map"]["level"], 2)

            # Added again after a rename: the same planet, renamed, still where it was in the list.
            planet.set_setting(project, planet.NAME, "Harbor II")
            again = self.add(project, into)
            packs = overrides.load_state(into)["packs"]
            self.assertEqual([p["id"] for p in packs], [done["installed"]])
            self.assertEqual(again["installed"], done["installed"])
            manifest = json.loads((into / "packs" / done["installed"] / overrides.MANIFEST).read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "Harbor II")

            # Another level project with the same name: another planet.
            other = level_project(root / "elsewhere", level=2)
            planet.set_setting(other, planet.NAME, "Harbor II")
            self.add(other, into)
            self.assertEqual(len(overrides.load_state(into)["packs"]), 2)

            # Both planets stack beside each other, with the original Aridia kept.
            out = root / "session"
            overrides.stack(into, out)
            registry = json.loads((out / "destinations.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(d["name"] for d in registry["destinations"]), ["Harbor II", "Harbor II"])
            self.assertFalse((out / "level_02").exists(), "the template planet is not replaced")

    def test_what_stops_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cases = {
                "unnamed": (level_project(root / "a"), planet.UNNAMED, ""),
                "two ratchets": (level_project(root / "b", heroes=2), planet.INVALID, "exactly one Ratchet"),
                "no ratchet": (level_project(root / "c", heroes=0), planet.INVALID, "exactly one Ratchet"),
                "no ship": (level_project(root / "d", ship=False), planet.INVALID, "Ship"),
                "refused edit": (level_project(root / "e"), planet.INCOMPLETE, "could not be packed"),
            }
            for case, (project, code, words) in cases.items():
                with self.subTest(case=case):
                    if case != "unnamed":
                        planet.set_setting(project, planet.NAME, "World")
                    into = root / f"overrides-{code}-{case}"
                    with self.assertRaises(overrides.PackError) as caught:
                        self.add(project, into, refuse="  not packed: a camera moved\n" if case == "refused edit" else "")
                    self.assertEqual(planet.code_of(caught.exception), code)
                    self.assertIn(words, str(caught.exception))
                    self.assertFalse((into / "packs").exists() and any((into / "packs").iterdir()),
                                     "nothing is added when it stops")
            # Not a project at all, and the NTSC disc (no ship destinations there yet).
            with self.assertRaises(planet.PlanetError) as caught:
                self.add(root / "nothing", root / "o1")
            self.assertEqual(caught.exception.code, planet.NO_PROJECT)
            project = level_project(root / "f")
            planet.set_setting(project, planet.NAME, "World")
            with self.assertRaises(planet.PlanetError):
                self.add(project, root / "o2", disc=SimpleNamespace(version=SimpleNamespace(key="ntsc")))

    def test_the_project_folder_forms_map_mod_reads(self):
        """map_mod.py finds the level from the level's project, the folder of projects or the level folder."""
        import map_mod
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = level_project(root / "projects")
            packed = root / "packed"
            packed.mkdir()
            fake_pack()(None, None, project, packed, {})
            for n, given in enumerate([project, root / "projects", project / "levels" / "level_02"]):
                with self.subTest(given=given):
                    manifest = map_mod.export_map(packed, given, root / f"out{n}", "W", 2, destination=True, map_id="w.1")
                    self.assertEqual(manifest["map"]["spawn"], [210.5, 170.25, 25.0])


class Launcher(unittest.TestCase):
    def test_every_code_has_a_sentence_in_the_launcher(self):
        mapping = ROOT / "launcher" / "src" / "lib" / "toolErrors.ts"
        if not mapping.is_file():
            self.skipTest("no launcher in this OpenRAC checkout")
        known = set(re.findall(r'^\s+"?([a-z0-9-]+)"?: \{', mapping.read_text(encoding="utf-8"), re.M))
        raised = {planet.UNNAMED, planet.INVALID, planet.INCOMPLETE, planet.NO_PROJECT}
        for path in HERE.glob("*.py"):
            if not path.name.startswith("test_"):
                raised |= set(re.findall(r'code="([a-z0-9-]+)"', path.read_text(encoding="utf-8")))
        self.assertLessEqual(raised, known, "codes the launcher has no sentence for")

    def test_the_action_adds_to_the_launchers_packs_folder(self):
        actions = json.loads((HERE.parent / "editor.json").read_text(encoding="utf-8"))["versions"]
        found = [(key, a) for key, listed in actions.items() for a in listed if a["id"] == "editor-new-planet"]
        self.assertEqual([key for key, _ in found], ["rac1/pal"], "ship destinations are PAL only")
        args = found[0][1]["args"]
        self.assertEqual(args[1:5], ["planet", "{disc}", "{projects}/level_{level}", "{data}/overrides"])

    @unittest.skipUnless(os.environ.get("OPENRAC_RAC1_PAL_DISC"), "set OPENRAC_RAC1_PAL_DISC")
    def test_nothing_more_is_needed_on_the_disc(self):
        """The disc's PAL Aridia packs and is added as a planet (about a minute and a half)."""
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = [sys.executable, str(HERE / "openrac_editor.py")]
            disc = os.environ["OPENRAC_RAC1_PAL_DISC"]
            subprocess.run(tools + ["godot", disc, str(root / "projects"), "--split", "--level", "2", "--anywhere"],
                           check=True, capture_output=True)
            project = root / "projects" / "level_02"
            planet.set_setting(project, planet.NAME, "Test World")
            run = subprocess.run(tools + ["planet", disc, str(project), str(root / "overrides"), "--anywhere"],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout[-2000:] + run.stderr[-2000:])
            self.assertEqual(len(overrides.load_state(root / "overrides")["packs"]), 1)


if __name__ == "__main__":
    unittest.main()
