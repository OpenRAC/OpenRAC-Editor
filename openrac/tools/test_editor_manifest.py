"""openrac/editor.json: the steps the launcher lists once OpenRAC Editor is downloaded
(OpenRAC's launcher/core/src/editor_install.rs reads it, in launcher/actions.json's form)."""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import json
from pathlib import Path
import re
import unittest

from openrac_root import API, ROOT

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MANIFEST = json.loads((HERE.parent / "editor.json").read_text())
# The placeholders OpenRAC's launcher fills (launcher/core/src/actions.rs PLACEHOLDERS).
PLACEHOLDERS = {"root", "dir", "python", "docker", "disc", "boot", "artifact", "serial", "key", "data", "level", "path",
                "option", "editor", "editor_tools", "projects", "levels", "packed", "states"}
REQUIREMENTS = {"disc", "inputs", "toolchains", "python", "editor", "docker", "artifact"}


class EditorManifest(unittest.TestCase):
    def test_its_form(self):
        self.assertEqual(MANIFEST["format"], 1)
        self.assertEqual(MANIFEST["extractorApi"], API["version"], "written against this OpenRAC's extractor")
        self.assertEqual(set(MANIFEST["program"]), {"linux", "windows", "macos"})

    def test_every_action_is_one_the_launcher_runs(self):
        keys = {f"{p.parent.name}/{v}" for p in (ROOT / "games").glob("*/game.json")
                for v in json.loads(p.read_text())["versions"]}
        for key, actions in MANIFEST["versions"].items():
            self.assertIn(key, keys)
            ids = [a["id"] for a in actions]
            self.assertEqual(len(ids), len(set(ids)), key)
            for action in actions:
                at = f"{key} {action['id']}"
                self.assertTrue(action["id"].startswith("editor-"), at)
                self.assertEqual(action["area"], "editor", at)
                self.assertLessEqual(set(action.get("requires", [])), REQUIREMENTS, at)
                if action.get("state") != "planned":
                    self.assertIn("editor", action["requires"], at)
                texts = [action.get("program", ""), *action.get("args", []), action.get("artifact", ""),
                         action.get("output", ""), *(n["path"] for n in action.get("needs", []))]
                for text in texts:
                    self.assertLessEqual(set(re.findall(r"\{(\w+)\}", text)), PLACEHOLDERS, at)
                    self.assertNotIn("assets/", text, f"{at}: the launcher places the folders ({{projects}}...)")
                self.assertTrue((REPO / action["docs"]).is_file(), at)
                for arg in action.get("args", []):
                    if arg.startswith("{editor_tools}/"):
                        self.assertTrue((HERE / arg.split("/", 1)[1]).is_file(), at)


if __name__ == "__main__":
    unittest.main()
