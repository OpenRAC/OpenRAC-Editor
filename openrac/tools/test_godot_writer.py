"""The level editor's Godot writer (godot.LevelWriter) beside the port's: its own scene and
level.json, with none of the port writer's files."""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
from pathlib import Path
import tempfile
import unittest

import godot
import levelwrite
from test_port import square, synthetic_level, translated


class GodotWriter(unittest.TestCase):
    def test_godot_export_unchanged(self):
        """The Godot writer still writes its scene and level.json; the port
        writer's overrides do not leak into it."""
        with tempfile.TemporaryDirectory() as temp:
            writer = godot.LevelWriter(Path(temp), synthetic_level())
            writer.dir.mkdir(parents=True)
            writer.write_objects("tie", {5: square("tie_5", ("tie", 1))},
                                 [{"index": 0, "class_id": 5, "matrix": translated(1, 2, 3),
                                   "stored_w": levelwrite.STORED_W, "uid": 1}])
            writer.finish()
            self.assertTrue((writer.dir / "level_03.tscn").is_file())
            self.assertTrue((writer.dir / "level.json").is_file())
            self.assertIn("Tie_0000", (writer.dir / "level_03.tscn").read_text())


if __name__ == "__main__":
    unittest.main()
