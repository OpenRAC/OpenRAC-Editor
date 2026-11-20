"""The AI voices' processes end to end, with no model and no network: the worker (voices/worker.py)
speaking JSON lines, a model's server stood in by a small local HTTP server that answers as
ZONOS3, fish-speech and an OpenAI-style server do, and voice_lab.py (what the Voices dock runs)
from a disc's workspace to the pack in the launcher's mods. Runs on every system the editor
ships for (Python only).

    python3 -m unittest test_voices_e2e
"""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import array
import base64
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import voice_lab
from voices import client, lab
from voices.workspace import Workspace, read_wav, write_json, write_wav

HERE = Path(__file__).resolve().parent


def tone_wav(seconds: float, rate: int, pitch: float = 330.0, silence: float = 0.3) -> bytes:
    """A WAV file: SILENCE seconds of quiet, a tone, quiet again (as a model's take often is)."""
    quiet = [0] * int(silence * rate)
    sound = [int(12000 * math.sin(2 * math.pi * pitch * i / rate)) for i in range(int(seconds * rate))]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(array.array("h", quiet + sound + quiet).tobytes())
    return buffer.getvalue()


class Server:
    """A model's server: answers each POST with a WAV and keeps what it was sent."""

    def __init__(self, status: int = 200, rate: int = 44100):
        self.bodies: list[tuple[str, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.bodies.append((self.path, body))
                data = tone_wav(1.0, rate) if status == 200 else b'{"detail": "no such voice"}'
                self.send_response(status)
                self.send_header("Content-Type", "audio/wav" if status == 200 else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class WorkerProtocol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "ref.wav").write_bytes(tone_wav(2.0, 16000, 200.0, 0.0))
        self.worker = client.Worker(client.current_python(), env={**os.environ, "PYTHONUTF8": "1"})

    def tearDown(self):
        self.worker.close()
        self.tmp.cleanup()

    def test_hello_and_requests_it_refuses(self):
        hello = self.worker.request("hello", timeout=60)
        self.assertEqual(hello["python"], sys.version.split()[0])
        with self.assertRaisesRegex(client.WorkerError, "no request"):
            self.worker.request("dance", timeout=60)
        with self.assertRaisesRegex(client.WorkerError, "no model is loaded"):
            self.worker.request("speak", timeout=60, text="hi", reference="x.wav", out="y.wav")
        with self.assertRaisesRegex(client.WorkerError, "no backend"):
            self.worker.request("load", timeout=60, backend="tape")
        # A broken line does not stop it.
        self.worker.process.stdin.write("{not json\n")
        self.worker.process.stdin.flush()
        self.assertIn("python", self.worker.request("hello", timeout=60))

    def speak_through(self, backend: str, path: str, **extra) -> tuple[dict, dict, Path]:
        server = Server()
        try:
            self.worker.request("load", timeout=60, backend=backend, repo="some/model", device="cpu", url=server.url)
            out = self.root / f"{backend}.wav"
            reply = self.worker.request("speak", timeout=60, text="Hello, Clank! Ça va?", language="fr",
                                        reference=str(self.root / "ref.wav"), reference_text="Ref words.",
                                        out=str(out), rate=22050, seed=5, loudness=0.05, **extra)
        finally:
            server.close()
        self.assertEqual(len(server.bodies), 1)
        self.assertEqual(server.bodies[0][0], path)
        return reply, server.bodies[0][1], out

    def test_zonos3_server(self):
        reply, body, out = self.speak_through("zonos3", "/v2/generate")
        self.assertEqual((body["language"], body["format"], body["seed"]), ("fr_fr", "wav", 5))
        self.assertEqual(base64.b64decode(body["speaker_audio_base64"]), (self.root / "ref.wav").read_bytes())
        pcm, rate, channels = read_wav(out)
        self.assertEqual((rate, channels, reply["rate"]), (22050, 1, 22050))
        # Trimmed to the words and a short pad, and as loud as asked.
        self.assertLess(reply["seconds"], 1.0 + 2 * 0.06 + 0.01)
        self.assertGreater(reply["seconds"], 0.99)
        samples = lab.samples_of(pcm)
        self.assertAlmostEqual(lab.level(samples), 0.05, delta=0.01)

    def test_fish_server(self):
        _, body, _ = self.speak_through("fish", "/v1/tts")
        self.assertEqual(body["references"][0]["text"], "Ref words.")
        self.assertEqual(body["text"], "Hello, Clank! Ça va?")

    def test_openai_style_server(self):
        _, body, _ = self.speak_through("openai", "/v1/audio/speech")
        self.assertTrue(body["ref_audio"].startswith("data:audio/wav;base64,"))
        self.assertEqual((body["model"], body["response_format"], body["ref_text"]), ("some/model", "wav", "Ref words."))

    def test_a_server_that_refuses_or_is_not_there_says_so(self):
        server = Server(status=422)
        try:
            self.worker.request("load", timeout=60, backend="zonos3", device="cpu", url=server.url)
            with self.assertRaisesRegex(client.WorkerError, "refused the line \\(422\\).*no such voice"):
                self.worker.request("speak", timeout=60, text="hi", reference=str(self.root / "ref.wav"),
                                    out=str(self.root / "x.wav"))
        finally:
            server.close()
        self.worker.request("load", timeout=60, backend="fish", device="cpu", url=server.url)
        with self.assertRaisesRegex(client.WorkerError, "does not answer.*start it first"):
            self.worker.request("speak", timeout=60, text="hi", reference=str(self.root / "ref.wav"),
                                out=str(self.root / "x.wav"))
        self.assertFalse((self.root / "x.wav").exists())

    def test_a_worker_that_hangs_or_dies_is_told(self):
        self.worker.process.kill()
        self.worker.process.wait()
        with self.assertRaisesRegex(client.WorkerError, "stopped"):
            self.worker.request("hello", timeout=30)


class Lab(unittest.TestCase):
    """voice_lab.py on a workspace made by hand, speaking through a ZONOS3 server stand-in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Spaces, and letters beyond ASCII where the system's file names can hold them.
        try:
            "ü".encode(sys.getfilesystemencoding())
            folder = "data folder ü"
        except UnicodeEncodeError:
            folder = "data folder"
        self.data = Path(self.tmp.name) / folder
        self.voices = self.data / "voices"
        self.space = Workspace.of(self.voices, "rac2", "ntsc")
        self.key = "0f0f0f0f0f0f0f0f"
        lines = {"format": 1, "game": "rac2", "version": "ntsc", "disc": str(self.data / "disc.iso"), "clips": [
            {"key": self.key, "lba": 10, "rate": 22050, "seconds": 2.0, "group": "Level speech", "name": "L9_bang_e",
             "base": "L9_bang", "levels": [9], "language": "en", "lines": [
                 {"id": f"{self.key}:0", "start": 0.0, "end": 2.0, "text": "Watch out, Ratchet!", "text_source": "listened",
                  "voice": "en-0", "character": None, "character_source": ""}]},
            {"key": "1e1e1e1e1e1e1e1e", "lba": 20, "rate": 22050, "seconds": 3.0, "group": "Level speech",
             "name": "L9_ok_e", "base": "L9_ok", "levels": [9], "language": "en", "lines": [
                 {"id": "1e1e1e1e1e1e1e1e:0", "start": 0.0, "end": 3.0, "text": "On my way.", "text_source": "listened",
                  "voice": "en-0", "character": None, "character_source": ""}]}]}
        self.space.save(lines)
        for item in lines["clips"]:
            pcm, rate, _ = self.read(tone_wav(item["seconds"], 22050, 150.0, 0.0))
            write_wav(self.space.snippet(item["lines"][0]["id"]), pcm, rate)
        self.server = Server(rate=24000)
        self.messages = []

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    @staticmethod
    def read(data: bytes) -> tuple[bytes, int, int]:
        with wave.open(io.BytesIO(data)) as file:
            return file.readframes(file.getnframes()), file.getframerate(), file.getnchannels()

    def session(self) -> voice_lab.Session:
        return voice_lab.Session(self.voices, self.data / "overrides", "rac2", "ntsc", progress=self.messages.append)

    def test_from_naming_to_the_pack(self):
        session = self.session()
        try:
            self.assertEqual(session.summary()["lines"], 2)
            self.assertFalse(session.summary()["listened"] is False)
            unnamed = session.speak("zonos3-mini", f"{self.key}:0", server=self.server.url)
            self.assertEqual(unnamed["takes"], [])
            self.assertIn("Name who says this line", unnamed["failed"][0]["error"])
            session.name(voice="en-0", character="Angela Cross")
            session.text(f"{self.key}:0", "Watch out, Clank!")
            rows = session.lines(character="Angela Cross")["lines"]
            self.assertEqual([r["text"] for r in rows], ["Watch out, Clank!", "On my way."])
            self.assertTrue(rows[0]["edited"])
            made = session.speak("zonos3-mini", [f"{self.key}:0", "1e1e1e1e1e1e1e1e:0"], server=self.server.url)
            self.assertEqual(made["failed"], [])
            self.assertEqual([t["text"] for t in made["takes"]], ["Watch out, Clank!", "On my way."])
            self.assertEqual(self.server.bodies[0][1]["text"], "Watch out, Clank!")
            # The reference is the character's lines from the disc, joined.
            reference = base64.b64decode(self.server.bodies[0][1]["speaker_audio_base64"])
            self.assertGreater(len(reference), 22050 * 2 * 4)
            take = read_wav(Path(made["takes"][0]["file"]))
            self.assertEqual(take[1], 22050)                         # the clip's own rate
            self.assertEqual(session.lines()["lines"][0]["chosen"], 1)
            again = session.speak("zonos3-mini", f"{self.key}:0", server=self.server.url, seed=3)
            self.assertEqual(again["takes"][0]["take"], 2)
            session.choose(f"{self.key}:0", 1)
            session.choose("1e1e1e1e1e1e1e1e:0", "none")
            import overrides
            if "rac2" in getattr(overrides, "AUDIO_GAMES", ()):
                packed = session.pack()
                self.assertEqual((packed["clips"], packed["lines"]), (1, 1))
                folder = self.data / "overrides" / "packs" / packed["pack"]
                manifest, used, ignored, problems = overrides.check(folder)
                self.assertEqual(used, [f"audio/{self.key}.wav"])
                self.assertEqual(manifest["game"], "rac2")
            else:
                # An OpenRAC whose game does not play Going Commando's sound packs yet: said, never put in.
                with self.assertRaisesRegex(RuntimeError, "not one the game takes"):
                    session.pack()
                self.assertFalse((self.data / "overrides" / "packs").exists()
                                 and any((self.data / "overrides" / "packs").iterdir()))
            # The say command: new words for an editor cutscene, in the named voice.
            out = self.data / "level" / "cutscenes" / "voices" / "intro_line_en.wav"
            said = session.say("zonos3-mini", "Angela Cross", "en", "Welcome to Endako.", str(out),
                               server=self.server.url)
            self.assertEqual(Path(said["file"]), out)
            self.assertTrue(out.is_file())
            self.assertEqual([p.name for p in out.parent.iterdir()], [out.name])     # no takes left behind
            with self.assertRaisesRegex(RuntimeError, "name their voice first"):
                session.say("zonos3-mini", "Nobody", "en", "Hi.", str(out), server=self.server.url)
            with self.assertRaisesRegex(RuntimeError, "does not speak"):
                session.say("voxtral-tts", "Angela Cross", "ja", "Hi.", str(out), server=self.server.url)
        finally:
            session.close()
        stages = {m["stage"] for m in self.messages}
        self.assertTrue({"load", "speak", "pack"} <= stages, stages)

    def test_a_model_not_installed_or_a_disc_not_read(self):
        session = self.session()
        session.name(voice="en-0", character="Angela Cross")
        with self.assertRaisesRegex(RuntimeError, "not installed yet"):
            session.speak("chatterbox-multilingual", f"{self.key}:0")
        with self.assertRaisesRegex(RuntimeError, "runs in its own server"):
            session.install("zonos3-mini")
        empty = voice_lab.Session(self.data / "elsewhere", None, None, None, progress=self.messages.append)
        with self.assertRaisesRegex(RuntimeError, "read the disc first"):
            empty.summary()
        with self.assertRaisesRegex(RuntimeError, "Open the editor from the launcher"):
            voice_lab.Session(self.voices, None, "rac2", "ntsc").pack()
        forgot = session.forget()
        self.assertEqual(forgot["forgot"], "rac2-ntsc")
        self.assertFalse(self.space.root.exists())

    def test_the_command_line_and_the_docks_process(self):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        base = [sys.executable, str(HERE / "voice_lab.py"), "--data", str(self.data), "--game", "rac2", "--version", "ntsc"]
        done = subprocess.run(base + ["summary"], capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout.strip().splitlines()[-1][len(voice_lab.RESULT):])
        self.assertEqual(result["result"]["lines"], 2)
        done = subprocess.run(base + ["choose", "--line", "../x"], capture_output=True, text=True, encoding="utf-8",
                              env=env, timeout=120)
        self.assertEqual(done.returncode, 1)
        self.assertFalse(json.loads(done.stdout.strip().splitlines()[-1][len(voice_lab.RESULT):])["ok"])
        # The dock's process: one command a line, each answered with its id, until quit.
        commands = [{"id": 1, "command": "summary"}, "not json", {"id": 2, "command": "explode"},
                    {"id": 3, "command": "name", "args": {"voice": "en-0", "character": "Mr. Fizzwidget"}},
                    {"id": 4, "command": "speak", "args": {"model": "zonos3-mini", "line": f"{self.key}:0",
                                                           "server": self.server.url}},
                    {"id": 5, "command": "quit"}, {"id": 6, "command": "summary"}]
        text = "\n".join(c if isinstance(c, str) else json.dumps(c) for c in commands) + "\n"
        done = subprocess.run(base + ["serve"], input=text, capture_output=True, text=True, encoding="utf-8",
                              env=env, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        answers = [json.loads(line[len(voice_lab.RESULT):]) for line in done.stdout.splitlines()
                   if line.startswith(voice_lab.RESULT)]
        self.assertEqual([(a["id"], a["ok"]) for a in answers],
                         [(1, True), (None, False), (2, False), (3, True), (4, True), (5, True)])
        self.assertEqual(answers[4]["result"]["takes"][0]["character"], "Mr. Fizzwidget")
        progress = [json.loads(line[len(voice_lab.PROGRESS):]) for line in done.stdout.splitlines()
                    if line.startswith(voice_lab.PROGRESS)]
        self.assertTrue(progress and all(p["id"] == 4 for p in progress))

    def test_models_and_hardware_for_the_dock(self):
        session = self.session()
        with mock.patch("voices.hardware.detect", return_value={"os": "linux", "cards": [], "best": None,
                                                                 "device": "cpu", "machine": "x86_64",
                                                                 "cpu_threads": 4, "ram_gb": 8.0}):
            found = session.models(languages="en,fr")
        self.assertEqual(found["preset"]["id"], "cpu")
        self.assertFalse(found["listen"])
        self.assertTrue(all(r["installed"] for r in found["models"] if r["kind"] == "server"))
        self.assertFalse(any(r["installed"] for r in found["models"] if r["kind"] == "python"))
        self.assertEqual(sum(r["recommended"] for r in found["models"]), 1)
        json.dumps(found)


class Pack(unittest.TestCase):
    def test_a_scene_line_is_placed_in_its_recording_read_from_the_disc(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = Workspace(Path(tmp) / "rac1-pal")
            key = "abababababababab"
            data = {"game": "rac1", "version": "pal", "disc": "x.iso", "clips": [
                {"key": key, "lba": 1, "rate": 16000, "seconds": 4.0, "group": "Scenes", "name": "s", "levels": [0],
                 "language": "en", "lines": [{"id": f"{key}:0", "start": 0.5, "end": 1.5},
                                             {"id": f"{key}:1", "start": 2.0, "end": 3.5}]}]}
            folder = space.takes(f"{key}:1")
            pcm, rate, _ = Lab.read(tone_wav(1.0, 16000, 500.0, 0.0))
            write_wav(folder / "1.wav", pcm, rate)
            write_json(folder / "1.json", {})
            write_json(space.characters_file, {"chosen": {f"{key}:1": 1}})
            base = array.array("h", [500] * 64000)
            with mock.patch.object(lab, "clip_audio", return_value=(base, 16000)):
                summary = lab.build_pack(space, data, Path(tmp) / "out")
            self.assertEqual(summary["lines"], 1)
            out, rate, _ = read_wav(Path(tmp) / "out" / "audio" / f"{key}.wav")
            samples = lab.samples_of(out)
            self.assertEqual(len(samples), 64000)
            self.assertEqual(samples[16000], 500)           # the first line: the disc's
            self.assertEqual(samples[52000], 0)             # the old second line's tail: silenced
            self.assertEqual(samples[60000], 500)           # after it: the disc's
            self.assertNotEqual(samples[2 * 16000 + 100], 500)


if __name__ == "__main__":
    unittest.main()
