"""The editor's MCP server as an AI assistant uses it (docs/MCP.md): a headless editor on a
hand-built level project (no disc), driven over HTTP with its token. It lists the level's tools,
reads the guides and prompts, adds and moves a moby, writes a cutscene with shots, an actor and a
line, checks it, undoes, and follows a job to its end:

    OPENRAC_GODOT=/path/to/godot python3 -m unittest test_editor_mcp -v
"""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import planet
from test_editor_e2e import LEVEL
from test_library_dock import level_project

GODOT = os.environ.get("OPENRAC_GODOT")
TOKEN = "test-token-0123456789"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Editor:
    """A headless editor on one level project, its MCP server on a port of its own."""

    def __init__(self, root: Path):
        self.project = level_project(root / "levels", "rac1", "ntsc", 5, "Ratchet & Clank: Rilgar (USA)",
                                     {11: ("vendor", 0.25), 12: ("robot", 0.5)}, [1000])
        (self.project / "levels" / "level_05" / "level_05.tscn").write_text(LEVEL)
        self.port = free_port()
        folder = root / "config" / "openrac_editor"
        folder.mkdir(parents=True)
        (folder / "editor_settings-4.7.tres").write_text(
            '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
            f'openrac/mcp/port = {self.port}\nopenrac/mcp/token = "{TOKEN}"\n')
        self.log = open(root / "editor.log", "w+")
        self.process = subprocess.Popen(
            [GODOT, "--headless", "--editor", "--path", str(self.project), "res://levels/level_05/level_05.tscn"],
            stdout=self.log, stderr=subprocess.STDOUT, env=dict(os.environ, XDG_CONFIG_HOME=str(root / "config")))
        self.next_id = 0

    def rpc(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        self.next_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/mcp", body, {
            "Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read())

    def wait_ready(self) -> list:
        """The tool list, once the plugin has registered its tools (the server starts first)."""
        until = time.monotonic() + 90
        while time.monotonic() < until:
            if self.process.poll() is not None:
                raise AssertionError("the editor stopped:\n" + self.text()[-4000:])
            try:
                tools = self.rpc("tools/list", timeout=5)["result"]["tools"]
                # The level's scene opens after the plugin's tools are in.
                if any(t["name"] == "moby_add" for t in tools) and self.call("project_info")["scene"]:
                    return tools
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(0.5)
        raise AssertionError("no level tools:\n" + self.text()[-4000:])

    def call(self, name: str, arguments: dict | None = None) -> dict:
        """A tool's answer: its JSON (or the whole result when it is not text), failing on an error."""
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, timeout=120)
        assert "result" in result, result
        result = result["result"]
        assert not result.get("isError"), f"{name}: {result}"
        content = result["content"][0]
        if content["type"] != "text":
            return result
        try:
            return json.loads(content["text"])
        except json.JSONDecodeError:
            return {"text": content["text"]}

    def error(self, name: str, arguments: dict | None = None) -> str:
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})["result"]
        assert result.get("isError"), f"{name} should fail: {result}"
        return result["content"][0]["text"]

    def text(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()


@unittest.skipUnless(GODOT, "set OPENRAC_GODOT")
class EditorMCP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.editor = Editor(Path(cls.temp.name))
        cls.tools = cls.editor.wait_ready()

    @classmethod
    def tearDownClass(cls):
        log = cls.editor.text()
        cls.editor.close()
        cls.temp.cleanup()
        errors = [line for line in log.splitlines() if "SCRIPT ERROR" in line or "Parse Error" in line]
        assert not errors, "\n".join(errors[:20])

    def test_1_lists_the_level_tools_resources_and_prompts(self):
        names = {t["name"] for t in self.tools}
        for name in ("project_info", "level_check", "planet_set", "planet_add", "class_list", "class_search",
                     "moby_list", "moby_add", "moby_set", "moby_remove", "ground_at", "cutscene_write",
                     "cutscene_add", "cutscene_check", "cutscene_frame", "job_wait", "batch", "undo"):
            self.assertIn(name, names)
        prompts = {p["name"] for p in self.editor.rpc("prompts/list")["result"]["prompts"]}
        self.assertIn("cutscene_dialogue", prompts)
        text = self.editor.rpc("prompts/get", {"name": "place_enemies", "arguments": {"what": "two robots",
                                                                                      "where": "by the vendor"}})
        self.assertIn("two robots", text["result"]["messages"][0]["content"]["text"])
        uris = {r["uri"] for r in self.editor.rpc("resources/list")["result"]["resources"]}
        self.assertIn("openrac://classes/names", uris)
        self.assertIn("openrac://level/summary", uris)
        summary = self.editor.rpc("resources/read", {"uri": "openrac://level/summary"})["result"]["contents"][0]
        self.assertIn("project", json.loads(summary["text"]))

    def test_2_reads_the_level(self):
        info = self.editor.call("project_info")
        self.assertEqual(info["level"], 5)
        classes = {c["class"]: c for c in self.editor.call("class_list")["classes"]}
        self.assertEqual(classes[11]["placed"], 1)
        found = self.editor.call("class_search", {"words": "vendor guy"})
        self.assertTrue(any(c["class"] == 12 and c["comes_as"] == "own" for c in found["results"]), found)
        mobys = self.editor.call("moby_list", {"near": [1, 2, 0]})["mobys"]
        self.assertEqual(mobys[0]["class"], 11)
        self.assertEqual([round(v, 3) for v in mobys[0]["position"]], [1, 2, 0])

    def test_3_adds_moves_and_undoes_a_moby(self):
        added = self.editor.call("moby_add", {"class": 12, "position": [10, 20, 3], "yaw": 90})
        if "job" in added:
            added = self.editor.call("job_wait", {"id": added["job"], "seconds": 60})["result"]
        path = added["node"]
        moby = next(m for m in self.editor.call("moby_list", {"class": 12})["mobys"] if m["path"] == path)
        self.assertEqual([round(v, 3) for v in moby["position"]], [10, 20, 3])
        self.assertAlmostEqual(moby["yaw"], 90, places=2)

        self.editor.call("moby_set", {"node": path, "position": [11, 21, 3], "yaw": 180})
        moby = next(m for m in self.editor.call("moby_list", {"class": 12})["mobys"] if m["path"] == path)
        self.assertEqual([round(v, 3) for v in moby["position"]], [11, 21, 3])

        self.editor.call("undo")
        moby = next(m for m in self.editor.call("moby_list", {"class": 12})["mobys"] if m["path"] == path)
        self.assertEqual([round(v, 3) for v in moby["position"]], [10, 20, 3])
        self.assertIn("40 characters", self.editor.error("planet_set", {"name": "x" * 41}))

    def test_3b_removes_each_moby_once_and_puts_them_back_in_place(self):
        added = []
        for x in (30, 31):
            answer = self.editor.call("moby_add", {"class": 11, "position": [x, 0, 0]})
            if "job" in answer:
                answer = self.editor.call("job_wait", {"id": answer["job"], "seconds": 60})["result"]
            added.append(answer["node"])
        before = [m["path"] for m in self.editor.call("moby_list", {"limit": 512})["mobys"]]
        # Named twice, and the later one first.
        gone = self.editor.call("moby_remove", {"nodes": [added[1], added[0], added[1]]})
        self.assertEqual(len(gone["deleted"]), 2, gone)
        self.editor.call("undo")
        self.assertEqual([m["path"] for m in self.editor.call("moby_list", {"limit": 512})["mobys"]], before)
        self.assertNotIn("already has a parent", self.editor.text())
        self.assertNotIn("Invalid new child index", self.editor.text())
        self.editor.call("moby_remove", {"nodes": added})

    def test_3c_takes_the_planet_id_the_first_add_wrote(self):
        # planet.py writes the id into project.godot while the editor is open.
        self.editor.call("planet_set", {"name": "Museum"})
        planet.set_setting(self.editor.project, planet.ID, "museum.1234abcd")
        self.assertEqual(self.editor.call("planet_get")["id"], "museum.1234abcd")
        # Naming it again keeps that id on disk.
        self.editor.call("planet_set", {"name": "Museum Two"})
        self.assertEqual(planet.settings(self.editor.project).get(planet.ID), "museum.1234abcd")

    def test_3d_an_add_while_another_comes_in_waits(self):
        flag = ('for p in editor.get_base_control().find_children("*", "", true, false):\n'
                '\tif p.get_script() != null and String(p.get_script().resource_path).ends_with("openrac_library/dock.gd"):\n'
                '\t\tp.plugin.waiting_for_import = %s\n\t\treturn true\nreturn false')
        self.assertTrue(self.editor.call("run_gdscript", {"code": flag % "true"})["result"])
        answer = self.editor.call("moby_add", {"class": 11, "position": [40, 0, 0]})
        self.assertEqual(answer.get("state"), "running", answer)
        self.assertTrue(self.editor.call("run_gdscript", {"code": flag % "false"})["result"])
        done = self.editor.call("job_wait", {"id": answer["job"], "seconds": 30})
        self.assertEqual(done["state"], "done", done)
        self.editor.call("moby_remove", {"nodes": [done["result"]["node"]]})

    def test_4_writes_and_checks_a_cutscene(self):
        doc = {
            "length": 300,
            "trigger": {"kind": "volume", "sphere": {"position": [1, 2, 0], "radius": 6}, "once": True},
            "hero": {"mode": "held"},
            "camera": {"keys": [
                {"t": 0, "eye": [8, 8, 4], "look_at": [1, 2, 1], "fov": 51, "cut": True},
                {"t": 120, "eye": [7, 7, 4], "look_at": [1, 2, 1], "ease": "smooth"},
                {"t": 121, "eye": [3, 0, 1.6], "look_at": {"actor": "robot"}, "cut": True},
                {"t": 300, "eye": [3, 0.5, 1.6], "look_at": {"actor": "robot"}}]},
            "actors": [{"id": "robot", "source": {"class": 12},
                        "track": [{"t": 0, "position": [2, 2, 0], "rotation": [0, 0, 0]}],
                        "anims": [{"t": 0, "seq": 0, "loop": True}]}],
            "subtitles": [{"t": 30, "end": 150, "text": {"en": "Hello there."}, "actor": "robot"}],
        }
        written = self.editor.call("cutscene_write", {"name": "Meeting", "document": doc})
        self.assertEqual(written["name"], "Meeting")
        self.assertIn("Meeting", [c["name"] for c in self.editor.call("cutscene_list")["cutscenes"]])
        back = self.editor.call("cutscene_get", {"name": "Meeting"})["document"]
        self.assertEqual(back["length"], 300)
        # The editor keeps the camera as the game plays it: keys sampled along each shot, a cut where
        # each shot starts.
        self.assertEqual([k["t"] for k in back["camera"]["keys"] if k.get("cut")], [0, 121])
        # Looking at an actor: towards where its track has it (2, 2, 0 and a unit up), from (3, 0, 1.6).
        turn = next(k for k in back["camera"]["keys"] if k["t"] == 121)["rotation"]
        self.assertAlmostEqual(turn[2], math.atan2(2, -1), delta=0.05)
        self.assertEqual(back["subtitles"][0]["text"]["en"], "Hello there.")

        self.editor.call("cutscene_add", {"name": "Meeting", "subtitles": [
            {"t": 160, "end": 290, "text": {"en": "Good bye."}, "actor": "robot"}]})
        back = self.editor.call("cutscene_get", {"name": "Meeting"})["document"]
        self.assertEqual(len(back["subtitles"]), 2)
        self.assertIn("problems", self.editor.call("cutscene_check", {"name": "Meeting"}))

        self.editor.call("undo")
        self.editor.call("undo")
        self.assertNotIn("Meeting", [c["name"] for c in self.editor.call("cutscene_list")["cutscenes"]])

    def test_5_batch_and_jobs(self):
        result = self.editor.call("batch", {"calls": [{"name": "planet_set", "arguments": {"name": "New Rilgar"}},
                                                      {"name": "planet_get"}]})
        self.assertIn("New Rilgar", json.dumps(result))
        # Without a disc and the launcher's folders, Check cannot run: it says why, at once.
        info = self.editor.call("project_info")
        self.assertFalse(info["can_check"])
        self.assertTrue(info["why_not"])
        self.assertIn(info["why_not"][:20], self.editor.error("level_check"))

        # A headless editor draws nothing, so a picture through a cutscene's camera fails at once and
        # says so (an editor with a window answers with a job that ends with the picture).
        self.editor.call("cutscene_write", {"name": "Look", "document": {"length": 60, "camera": {"keys": [
            {"t": 0, "eye": [8, 8, 4], "look_at": [1, 2, 1], "cut": True}]}}})
        self.assertIn("headless", self.editor.error("cutscene_frame", {"name": "Look", "t": 10}))
        self.editor.call("undo")
        missing = self.editor.error("job_status", {"id": "nope"})
        self.assertIn("nope", missing)
        # The tools that start a job answer {"job": id}: that name works too.
        self.assertIn("nope", self.editor.error("job_wait", {"job": "nope"}))
        self.assertIn("id", self.editor.error("job_wait", {}))

    def test_6_the_plugin_bridge(self):
        # ai/openrac's stdio bridge finds the editor by its mcp.json and passes messages through.
        bridge = Path(__file__).resolve().parents[2] / "ai" / "openrac" / "servers" / "editor_bridge.py"
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "project_info"}}]
        run = subprocess.run([sys.executable, str(bridge)], input="".join(json.dumps(m) + "\n" for m in messages),
                             capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, XDG_CONFIG_HOME=str(Path(self.temp.name) / "config")))
        answers = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual([a["id"] for a in answers], [1, 2], run.stderr)
        self.assertEqual(json.loads(answers[1]["result"]["content"][0]["text"])["level"], 5)


if __name__ == "__main__":
    unittest.main()
