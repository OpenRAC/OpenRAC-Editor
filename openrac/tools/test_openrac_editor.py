"""openrac_editor.py: the launcher writes the Godot projects into its own data folder again and
again, keeping every edit, and says how far along it is (progress_line, carried levels)."""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout

from formats import FormatError
import openrac_editor as oe
import progress_line


def said(out: str) -> list[dict]:
    return [p for p in (progress_line.parse(line) for line in out.splitlines()) if p is not None]


def fake_godot(schema: int, split: bool = True, gameplay_source=None):
    """An exporter shaped as godot(): a project per level (or one for all) with its level.json."""
    def export(out: Path, levels: list[int]) -> None:
        for i in levels:
            project = out / f"level_{i:02}" if split else out
            level = project / "levels" / f"level_{i:02}"
            level.mkdir(parents=True)
            (project / "project.godot").write_text("config_version=5\n")
            metadata = {"schema": schema, "level": i}
            if gameplay_source is not None:
                metadata["gameplay_source"] = gameplay_source
            (level / "level.json").write_text(json.dumps(metadata))
            (level / f"level_{i:02}.tscn").write_text("[gd_scene format=3]\n")
    return export


class SecondExportTests(unittest.TestCase):
    """The launcher exports into its own data folder again and again: each time must work."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_pal_gameplay_change_backs_up_old_edits_and_then_keeps_new_edits(self):
        dest = self.data / "levels" / "pal"
        schema = oe.GODOT_SCHEMA
        oe.export_godot(dest, [0], True, True, fake_godot(schema))
        self.scene(dest, 0).write_text("old scene with a moved crate")
        fresh = fake_godot(schema, gameplay_source="gameplay_ntsc")
        said = oe.export_godot(dest, [0], True, True, fresh, gameplay_source="gameplay_ntsc")
        self.assertIn("older extractor", said)
        backups = list((dest.parent / "pal-backups").glob("*/level_00/levels/level_00/level_00.tscn"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "old scene with a moved crate")
        self.scene(dest, 0).write_text("new 60 Hz scene with intended edits")
        said = oe.export_godot(dest, [0], True, True, fresh, gameplay_source="gameplay_ntsc")
        self.assertIn("1 level(s) kept", said)
        self.assertEqual(self.scene(dest, 0).read_text(), "new 60 Hz scene with intended edits")

    def scene(self, dest: Path, i: int, split: bool = True) -> Path:
        project = dest / f"level_{i:02}" if split else dest
        return project / "levels" / f"level_{i:02}" / f"level_{i:02}.tscn"

    def test_godot_twice_keeps_edits_and_adds_levels(self):
        dest = self.data / "levels" / "pal"
        schema = oe.GODOT_SCHEMA
        said = oe.export_godot(dest, [0, 1], True, True, fake_godot(schema))
        self.assertIn("one per level", said)
        self.scene(dest, 0).write_text("[gd_scene format=3]\n; moved a crate\n")
        said = oe.export_godot(dest, [0, 1, 2], True, True, fake_godot(schema))
        self.assertIn("2 level(s) kept", said)
        self.assertIn("1 added", said)
        self.assertIn("moved a crate", self.scene(dest, 0).read_text())
        self.assertTrue(self.scene(dest, 2).is_file())
        self.assertTrue((dest / "level_00" / "rc1" / "level.gd").is_file())     # scripts refreshed
        self.assertFalse((self.data / "levels" / "pal-backups").exists())

    def test_godot_after_a_format_bump_backs_up_and_replaces(self):
        dest = self.data / "levels" / "pal"
        old = oe.GODOT_SCHEMA - 1
        oe.export_godot(dest, [0, 1], True, True, fake_godot(old))
        self.scene(dest, 1).write_text("[gd_scene format=3]\n; my edit\n")
        said = oe.export_godot(dest, [0, 1], True, True, fake_godot(oe.GODOT_SCHEMA))
        self.assertIn("older extractor", said)
        self.assertEqual(oe.level_schema(self.scene(dest, 1).parent), oe.GODOT_SCHEMA)
        backups = list((self.data / "levels" / "pal-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertIn("my edit", (backups[0] / "level_01" / "levels" / "level_01" / "level_01.tscn").read_text())

    def test_godot_one_project_for_every_level(self):
        dest = self.data / "godot"
        oe.export_godot(dest, [0], True, False, fake_godot(oe.GODOT_SCHEMA, split=False))
        self.scene(dest, 0, split=False).write_text("; edited\n")
        oe.export_godot(dest, [0, 3], True, False, fake_godot(oe.GODOT_SCHEMA, split=False))
        self.assertIn("edited", self.scene(dest, 0, split=False).read_text())
        self.assertTrue(self.scene(dest, 3, split=False).is_file())

    def test_a_folder_that_is_not_an_export_is_refused(self):
        dest = self.data / "port"
        dest.mkdir()
        (dest / "notes.txt").write_text("mine")
        with self.assertRaisesRegex(FormatError, "choose another folder"):
            oe.ours(dest, "port")
        with self.assertRaisesRegex(FormatError, "choose another folder"):
            oe.export_godot(dest, [0], True, True, fake_godot(oe.GODOT_SCHEMA))
        godot = self.data / "levels"
        oe.export_godot(godot, [0], True, True, fake_godot(oe.GODOT_SCHEMA))
        with self.assertRaisesRegex(FormatError, "Godot projects"):
            oe.ours(godot, "port")


class ToolsSayIt(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dest = Path(self.temp.name) / "levels" / "pal"

    def tearDown(self):
        self.temp.cleanup()

    def scene(self, i: int) -> Path:
        return self.dest / f"level_{i:02}" / "levels" / f"level_{i:02}" / f"level_{i:02}.tscn"

    def test_again_backs_up_every_level_and_carries_each_with_a_count(self):
        schema = oe.GODOT_SCHEMA
        oe.export_godot(self.dest, [0, 1], True, True, fake_godot(schema))
        self.scene(1).write_text("[gd_scene format=3]\n; moved a crate\n")
        carried = []

        def carry(i, old, new, backup):
            carried.append(i)
            return {"level": i, "carried": [], "not_carried": [], "mobys": 0, "failed": False, "backup": str(backup)}
        out = io.StringIO()
        with redirect_stdout(out):
            words = oe.export_godot(self.dest, [0, 1], True, True, fake_godot(schema), carry=carry, again=True)
        self.assertIn("2 written again", words)
        self.assertEqual(carried, [0, 1])
        backups = list((self.dest.parent / "pal-backups").glob("*/level_01/levels/level_01/level_01.tscn"))
        self.assertEqual(len(backups), 1)
        self.assertIn("moved a crate", backups[0].read_text())
        steps = [(p["step"], p["done"], p["total"], p["level"]) for p in said(out.getvalue())]
        self.assertEqual(steps, [("carry", 0, 2, None), ("carry", 1, 2, 0), ("carry", 2, 2, 1)])

    def test_without_again_a_current_level_is_kept(self):
        schema = oe.GODOT_SCHEMA
        oe.export_godot(self.dest, [0], True, True, fake_godot(schema))
        out = io.StringIO()
        with redirect_stdout(out):
            words = oe.export_godot(self.dest, [0], True, True, fake_godot(schema), carry=lambda *a: {})
        self.assertIn("1 level(s) kept", words)
        self.assertEqual(said(out.getvalue()), [])


if __name__ == "__main__":
    unittest.main()


class OpenTests(unittest.TestCase):
    """`open`: a project made by an older editor gets this editor's files before the editor starts."""

    def test_a_project_gets_the_editors_files_once(self):
        import godot
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "level_00"
            godot.write_project(project, [0])
            self.assertFalse(oe.sync_project(project), "a fresh project is up to date")
            script = next((project / "rc1").rglob("*.gd"))
            script.write_text("# an older editor's\n")
            (project / godot.PROJECT_STAMP).write_text("older\n")
            self.assertTrue(oe.sync_project(project))
            self.assertNotEqual(script.read_text(), "# an older editor's\n")
            self.assertFalse(oe.sync_project(project))
            self.assertFalse(oe.sync_project(Path(tmp) / "nothing"), "no project, nothing to do")

    def test_open_starts_the_editor_on_its_own(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(oe.subprocess, "Popen") as popen:
            self.assertEqual(oe.open_editor(["/bin/editor", "--path", tmp, "-e"]), 0)
            self.assertEqual(popen.call_args.args[0], ["/bin/editor", "--path", tmp, "-e"])
            self.assertEqual(oe.open_editor([]), 2)
