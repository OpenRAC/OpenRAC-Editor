"""The AI voices' parts on their own (voices/, docs/VOICES.md), with no model, no disc and no
network: the hardware probes' readers on each system's answers, the catalogue of models and its
presets, the environments' commands on each system, the disc's speech, the voices told apart, the
lines placed back into their recordings, and the pack the game plays. test_voices_e2e.py runs the
processes (the worker, voice_lab.py) end to end.

    python3 -m unittest test_voices
"""

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
import array
import hashlib
import io
import json
import math
import random
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from voices import catalogue, envs, hardware, lab, models, speakers
from voices.workspace import Workspace, read_json, read_wav, safe_name, write_json, write_wav

KEY = "0123456789abcdef"


def tone(seconds: float, rate: int = 16000, pitch: float = 220.0, loud: float = 0.3) -> array.array:
    count = int(seconds * rate)
    return array.array("h", (int(32767 * loud * math.sin(2 * math.pi * pitch * i / rate)) for i in range(count)))


def card(vram: float, backend: str = "cuda", unified: bool = False) -> dict:
    found = {"vendor": "nvidia", "name": "card", "vram_gb": vram, "backend": backend}
    if unified:
        found["unified"] = True
    return found


def computer(vram: float | None, backend: str = "cuda") -> dict:
    """hardware.detect()'s answer for a computer with one card of VRAM GB (None: no card)."""
    if vram is None:
        return {"os": "linux", "cards": [], "best": None, "device": "cpu"}
    best = card(vram, backend, unified=backend == "mps")
    return {"os": "linux", "cards": [best], "best": best, "device": hardware.device_of(best)}


class Hardware(unittest.TestCase):
    def test_nvidia_smi_answers(self):
        answer = "NVIDIA GeForce RTX 4090, 24564, 566.36\nNVIDIA GeForce GTX 1650, 4096, 566.36\n"
        cards = hardware.nvidia_cards(answer)
        self.assertEqual([(c["name"], c["vram_gb"], c["backend"]) for c in cards],
                         [("NVIDIA GeForce RTX 4090", 24.0, "cuda"), ("NVIDIA GeForce GTX 1650", 4.0, "cuda")])
        # Missing, empty, an error message, a line without memory, Windows' line endings.
        self.assertEqual(hardware.nvidia_cards(None), [])
        self.assertEqual(hardware.nvidia_cards(""), [])
        self.assertEqual(hardware.nvidia_cards("NVIDIA-SMI has failed because it couldn't communicate"), [])
        self.assertEqual(hardware.nvidia_cards("Tesla T4, [N/A], 1\r\n"), [])
        self.assertEqual(len(hardware.nvidia_cards("RTX 3060, 12288, 1\r\nRTX 3060, 12288, 1\r\n")), 2)

    def test_amd_cards_from_the_driver_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            drm = Path(tmp)
            for name, vendor, memory, product in [("card0", "0x1002", 16 << 30, "AMD Radeon RX 7800 XT"),
                                                  ("card1", "0x1002", 512 << 20, ""),       # integrated
                                                  ("card2", "0x8086", 8 << 30, "Intel Arc"),
                                                  ("card0-DP-1", "0x1002", 16 << 30, "")]:   # a connector
                device = drm / name / "device"
                device.mkdir(parents=True)
                (device / "vendor").write_text(vendor + "\n")
                (device / "mem_info_vram_total").write_text(f"{memory}\n")
                if product:
                    (device / "product_name").write_text(product)
            (drm / "card3" / "device").mkdir(parents=True)                                   # no memory file
            cards = hardware.amd_cards(drm)
            self.assertEqual([(c["name"], c["vram_gb"], c["backend"]) for c in cards],
                             [("AMD Radeon RX 7800 XT", 16.0, "rocm")])
            self.assertEqual(hardware.amd_cards(drm / "missing"), [])

    def test_windows_video_controllers(self):
        many = json.dumps([{"Name": "NVIDIA GeForce RTX 3080"}, {"Name": "AMD Radeon RX 6700 XT"},
                           {"Name": "Intel(R) UHD Graphics 770"}, {"Name": "Microsoft Basic Display Adapter"},
                           {"Name": None}, "junk"])
        cards = hardware.windows_cards(many)
        self.assertEqual([(c["vendor"], c["backend"]) for c in cards], [("amd", "none"), ("intel", "none")])
        self.assertEqual(len(hardware.windows_cards(json.dumps({"Name": "Radeon 780M"}))), 1)
        for broken in (None, "", "not json", "42"):
            self.assertEqual(hardware.windows_cards(broken), [])

    def test_apple_silicon_shares_its_memory(self):
        chip = hardware.apple_chip("arm64", "Apple M3 Pro\n", str(36 << 30))
        self.assertEqual(chip[0]["vram_gb"], 36.0)
        self.assertTrue(chip[0]["unified"])
        self.assertEqual(hardware.device_of(chip[0]), "mps")
        self.assertEqual(hardware.apple_chip("x86_64", "Intel Core i9", str(32 << 30)), [])
        self.assertEqual(hardware.apple_chip("arm64", None, None)[0]["name"], "Apple Silicon")

    def test_the_best_card_is_one_pytorch_uses(self):
        intel = {"vendor": "intel", "name": "UHD", "vram_gb": 0.0, "backend": "none"}
        small, big = card(6), card(12)
        self.assertIs(hardware.best_card([intel, small, big]), big)
        self.assertIsNone(hardware.best_card([intel]))
        self.assertEqual(hardware.device_of(None), "cpu")
        self.assertEqual(hardware.device_of(card(16, "rocm")), "cuda")   # ROCm builds call it cuda

    def test_detect_on_this_computer(self):
        found = hardware.detect()
        self.assertIn(found["device"], ("cpu", "cuda", "mps"))
        self.assertGreaterEqual(found["cpu_threads"], 1)
        self.assertGreaterEqual(found["ram_gb"], 0.0)
        json.dumps(found)

    def test_a_probe_that_is_missing_or_hangs_adds_nothing(self):
        self.assertIsNone(hardware.run(["openrac-no-such-program"]))
        with mock.patch("subprocess.run", side_effect=hardware.subprocess.TimeoutExpired("x", 1)), \
                mock.patch("shutil.which", return_value="/bin/x"):
            self.assertIsNone(hardware.run(["x"]))


class Models(unittest.TestCase):
    def setUp(self):
        self.data = models.load()

    def test_the_catalogue_is_whole(self):
        ids = [m["id"] for m in self.data["models"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(self.data["updated"].startswith("2026-11"))
        for item in self.data["models"]:
            at = item["id"]
            for field in ("name", "kind", "backend", "license", "commercial", "quality", "vram_gb", "download_gb",
                          "languages", "reference_seconds", "notes", "devices"):
                self.assertIn(field, item, at)
            self.assertIn(item["backend"], ("chatterbox", "qwen3", "voxcpm", "zonos3", "fish", "openai"), at)
            self.assertTrue(set(item["devices"]) <= {"cpu", "cuda", "mps"}, at)
            least, most = item["reference_seconds"]
            self.assertLess(0, least)
            self.assertLessEqual(least, most, at)
            if item["kind"] == "python":
                self.assertTrue(all("==" in p or "<" in p for p in item["packages"] if not p.startswith("setuptools") or "<" in p), at)
                self.assertRegex(item["python"], r"^3\.1[0-3]$", at)

    def test_every_preset_starts_on_a_model_that_speaks_every_game_language(self):
        for preset in self.data["presets"]:
            hw = {"apple": computer(16, "mps"), "cpu": computer(None)}.get(preset["id"], computer(preset.get("vram_gb", 0)))
            rows = models.ranking(self.data, hw, list(models.GAME_LANGUAGES))
            picked = [r for r in rows if r["recommended"]]
            self.assertEqual(len(picked), 1, preset["id"])
            self.assertEqual(picked[0]["missing"], [], preset["id"])
            self.assertTrue(picked[0]["fits"], preset["id"])
            self.assertEqual(picked[0]["kind"], "python", preset["id"])

    def test_presets_by_the_card(self):
        expect = [(None, "cpu"), (2, "cpu"), (4, "low"), (6, "low"), (8, "mid"), (11, "mid"), (12, "high"),
                  (16, "high"), (24, "ultra"), (48, "ultra")]
        for vram, preset in expect:
            self.assertEqual(models.preset_for(self.data, computer(vram))["id"], preset, vram)
        self.assertEqual(models.preset_for(self.data, computer(8, "mps"))["id"], "apple")
        self.assertEqual(models.preset_for(self.data, computer(64, "mps"))["id"], "apple")
        self.assertEqual(models.preset_for(self.data, computer(16, "rocm"))["id"], "high")

    def test_a_model_too_big_says_why_and_runs_on_the_processor(self):
        big = max((m for m in self.data["models"] if m["kind"] == "python" and "cpu" not in m["devices"]),
                  key=lambda m: m["vram_gb"], default=None)
        some = next(m for m in self.data["models"] if m["kind"] == "python" and "cpu" in m["devices"])
        small_card = computer(max(0.5, some["vram_gb"] - 0.1))
        ok, why = models.fits(some, small_card)
        self.assertFalse(ok)
        self.assertIn("GB", why)
        self.assertEqual(models.device_for(some, small_card), "cpu")
        # With room for the weights and the headroom, it fits on the card.
        roomy = computer(some["vram_gb"] + models.HEADROOM_GB)
        self.assertEqual(models.fits(some, roomy), (True, ""))
        self.assertEqual(models.device_for(some, roomy), "cuda")
        if big is not None:
            ok, why = models.fits(big, computer(None))
            self.assertFalse(ok)
            self.assertIn("NVIDIA", why)

    def test_a_mac_counts_on_a_share_of_its_memory(self):
        item = {"kind": "python", "name": "M", "devices": ["mps", "cpu"], "vram_gb": 6.0}
        self.assertFalse(models.fits(item, computer(8, "mps"))[0])       # 8 * 0.6 < 6
        self.assertTrue(models.fits(item, computer(16, "mps"))[0])
        self.assertEqual(models.device_for(item, computer(8, "mps")), "cpu")

    def test_server_models_run_where_their_server_is(self):
        for item in self.data["models"]:
            if item["kind"] == "server":
                self.assertTrue(item["server"]["url"].startswith("http://127.0.0.1:"))
                self.assertEqual(models.device_for(item, computer(None)), "server")
                self.assertEqual(models.fits(item, computer(None)), (True, ""))

    def test_ranking_lists_every_model_once_preset_first(self):
        hw = computer(8)
        preset = models.preset_for(self.data, hw)
        rows = models.ranking(self.data, hw, ["en", "pl"])
        self.assertEqual(sorted(r["id"] for r in rows), sorted(m["id"] for m in self.data["models"]))
        self.assertEqual([r["id"] for r in rows[:len(preset["models"])]], preset["models"])
        rest = rows[len(preset["models"]):]
        self.assertEqual([r["quality"] for r in rest], sorted((r["quality"] for r in rest), reverse=True))
        self.assertTrue(any("pl" in r["missing"] for r in rows))

    def test_a_broken_catalogue_is_refused(self):
        def changed(edit):
            data = json.loads(json.dumps(self.data))
            edit(data)
            return data
        broken = [changed(lambda d: d["models"].append(dict(d["models"][0]))),
                  changed(lambda d: d["presets"][0]["models"].append("nothing")),
                  changed(lambda d: d["models"][0].update(kind="plugin")),
                  changed(lambda d: d["models"][0].update(kind="python", packages=[])),
                  changed(lambda d: d["models"][0].update(kind="server", server={}))]
        for data in broken:
            with self.assertRaises(ValueError):
                models.check(data)
        with self.assertRaises(KeyError):
            models.model(self.data, "nothing")


class Environments(unittest.TestCase):
    def test_a_python_per_system(self):
        env = Path("envs/m")
        self.assertEqual(envs.python_in(env, "win32"), env / "Scripts" / "python.exe")
        self.assertEqual(envs.python_in(env, "linux"), env / "bin" / "python")
        self.assertEqual(envs.python_in(env, "darwin"), env / "bin" / "python")

    def test_uv_for_each_computer(self):
        cases = [("win32", "AMD64", "uv-x86_64-pc-windows-msvc.zip"), ("win32", "ARM64", "uv-aarch64-pc-windows-msvc.zip"),
                 ("darwin", "arm64", "uv-aarch64-apple-darwin.tar.gz"), ("darwin", "x86_64", "uv-x86_64-apple-darwin.tar.gz"),
                 ("linux", "x86_64", "uv-x86_64-unknown-linux-gnu.tar.gz"),
                 ("linux", "aarch64", "uv-aarch64-unknown-linux-gnu.tar.gz")]
        for system, machine, name in cases:
            self.assertEqual(envs.uv_archive(system, machine), name)

    def test_the_pytorch_build_for_the_card(self):
        self.assertEqual(envs.torch_backend(computer(8), "win32"), "auto")
        self.assertEqual(envs.torch_backend(computer(8), "linux"), "auto")
        self.assertEqual(envs.torch_backend(computer(16, "rocm"), "linux"), "auto")
        self.assertEqual(envs.torch_backend(computer(None), "linux"), "cpu")
        self.assertEqual(envs.torch_backend(computer(None), "win32"), "cpu")
        self.assertIsNone(envs.torch_backend(computer(16, "mps"), "darwin"))

    def test_install_commands_on_each_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = envs.Home(Path(tmp))
            for system in ("win32", "linux", "darwin"):
                venv, install = home.install_commands(Path("uv"), "m", "3.11", ["chatterbox-tts==0.1.7"],
                                                      envs.torch_backend(computer(8), system), system)
                self.assertEqual(venv[:2], ["uv", "venv"])
                self.assertIn("--no-project", venv)
                self.assertEqual(install[install.index("--python") + 1], str(envs.python_in(home.env("m"), system)))
                self.assertEqual("--torch-backend" in install, system != "darwin")
                self.assertIn("chatterbox-tts==0.1.7", install)
                self.assertIn("soundfile==0.14.0", install)
            # A model's own pin of a worker package wins.
            _, install = home.install_commands(Path("uv"), "m", "3.11", ["numpy<2"], None, "linux")
            self.assertEqual([a for a in install if a.startswith("numpy")], ["numpy<2"])

    def test_environment_names_stay_inside(self):
        home = envs.Home(Path("voices"))
        for bad in ("", "..", "../x", "a/b", "a\\b", "c:", ".hidden"):
            with self.assertRaises(ValueError):
                home.env(bad)
        self.assertEqual(home.env("chatterbox-multilingual"), Path("voices") / "envs" / "chatterbox-multilingual")

    def test_install_makes_a_whole_environment_or_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = envs.Home(Path(tmp))
            ran = []

            def runner(command, report):
                ran.append(command)
                if command[1] == "venv":
                    python = envs.python_in(Path(command[-1]))
                    python.parent.mkdir(parents=True)
                    python.write_text("")
                report("line")

            with mock.patch.object(home, "find_uv", return_value=Path("uv")):
                state = home.install("m", "3.11", ["x==1"], "cpu", 0.0, runner=runner)
                self.assertTrue(state["complete"])
                self.assertEqual(home.env_state("m")["packages"], ["x==1"])
                # A failure halfway leaves an environment the dock counts as missing.
                def failing(command, report):
                    runner(command, report)
                    if command[1] == "pip":
                        raise RuntimeError("Installing failed")
                with self.assertRaises(RuntimeError):
                    home.install("m", "3.11", ["x==1"], "cpu", 0.0, runner=failing)
                self.assertIsNone(home.env_state("m"))
                with self.assertRaisesRegex(RuntimeError, "free disk space"):
                    home.install("m", "3.11", ["x==1"], "cpu", 10 ** 6, runner=runner)
            self.assertTrue(home.remove("m"))
            self.assertFalse(home.remove("m"))
            self.assertFalse(home.env("m").exists())

    def test_a_broken_environment_file_is_no_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = envs.Home(Path(tmp))
            env = home.env("m")
            envs.python_in(env).parent.mkdir(parents=True)
            envs.python_in(env).write_text("")
            for text in ("", "{", "[]", '{"complete": false}'):
                (env / envs.ENV_FILE).write_text(text)
                self.assertIsNone(home.env_state("m"), text)

    def fake_release(self, name: str, program: str, body: bytes) -> bytes:
        buffer = io.BytesIO()
        if name.endswith(".zip"):
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(f"uv-x/{program}", body)
        else:
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                info = tarfile.TarInfo(f"uv-x/{program}")
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
        return buffer.getvalue()

    def test_uv_is_fetched_only_when_its_checksum_matches(self):
        import sys
        program = "uv.exe" if sys.platform == "win32" else "uv"
        archive = self.fake_release(envs.uv_archive(), program, b"#!uv")
        digest = hashlib.sha256(archive).hexdigest()

        def opener_for(sha: str):
            def opener(url, timeout=0):
                self.assertTrue(url.startswith(f"{envs.UV_RELEASES}/{envs.UV_VERSION}/"))
                return io.BytesIO(f"{sha}  {envs.uv_archive()}\n".encode() if url.endswith(".sha256") else archive)
            return opener

        with tempfile.TemporaryDirectory() as tmp:
            home = envs.Home(Path(tmp))
            got = home.fetch_uv(opener=opener_for(digest))
            self.assertEqual(got.read_bytes(), b"#!uv")
            got.unlink()
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                home.fetch_uv(opener=opener_for("0" * 64))
            self.assertFalse(home.own_uv().exists())

    def test_uv_archives_of_each_system_are_unpacked(self):
        for name, program in (("uv-x86_64-pc-windows-msvc.zip", "uv.exe"), ("uv-x.tar.gz", "uv")):
            archive = self.fake_release(name, program, b"uv!")
            digest = hashlib.sha256(archive).hexdigest()
            with tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(envs, "uv_archive", return_value=name), \
                    mock.patch.object(envs.sys, "platform", "win32" if program == "uv.exe" else "linux"):
                home = envs.Home(Path(tmp))
                opener = lambda url, timeout=0: io.BytesIO(f"{digest} x".encode() if url.endswith(".sha256") else archive)  # noqa: E731
                self.assertEqual(home.fetch_uv(opener=opener).read_bytes(), b"uv!")

    def test_workers_see_the_voices_folder_and_utf8(self):
        home = envs.Home(Path("voices"))
        with mock.patch.dict(envs.os.environ, {"PYTHONPATH": "x", "VIRTUAL_ENV": "y"}):
            worker = home.worker_env()
            uv = home.uv_env()
        self.assertEqual(worker["HF_HOME"], str(Path("voices") / "models"))
        self.assertEqual(worker["PYTHONUTF8"], "1")
        self.assertNotIn("PYTHONPATH", worker)
        self.assertNotIn("VIRTUAL_ENV", uv)
        self.assertEqual(uv["UV_PYTHON_INSTALL_DIR"], str(Path("voices") / "python"))


class FakeDisc:
    """Bytes as a disc: what catalogue.py reads of one."""

    def __init__(self, data: bytes):
        self.data = data
        self.size = len(data)

    def read(self, offset: int, size: int) -> bytes:
        return self.data[offset:offset + size]


def vag(name: str, rate: int = 22050, blocks: int = 64) -> bytes:
    head = b"VAGp" + (32).to_bytes(4, "big") + bytes(4) + (blocks * 16).to_bytes(4, "big") + rate.to_bytes(4, "big")
    head = head.ljust(0x20, b"\0") + name.encode("latin-1")[:16].ljust(16, b"\0")
    return head + bytes(blocks * 16)


class Catalogue(unittest.TestCase):
    def test_going_commandos_names(self):
        self.assertEqual(catalogue.language_of_name("L9_bang_f"), "fr")
        self.assertEqual(catalogue.language_of_name("L9_bang_E"), "en")
        self.assertIsNone(catalogue.language_of_name("L9_bang"))
        self.assertIsNone(catalogue.language_of_name("helpdesk_long_lf"))   # 16 letters: cut, tells nothing
        self.assertEqual(catalogue.base_name("L9_bang_g"), "L9_bang")
        self.assertEqual(catalogue.base_name("L9_bang"), "L9_bang")
        for name in ("rc2_l00_main_br", "L4_transition", "trans_silence", "sfx_door", "therm_hum",
                     "cityBed2", "electrolizerbed", "breakable_door"):
            self.assertTrue(catalogue.NOT_SPEECH.match(name), name)
        for name in ("L9_bang_f", "helpdesk_hi", "x:\\rc2\\sound\\sce"):
            self.assertFalse(catalogue.NOT_SPEECH.match(name), name)
        self.assertTrue(catalogue.SCENE.match("X:\\RC2\\SOUND\\SCE"))

    def test_vag_files_are_found_whole_and_never_inside_another(self):
        sector = catalogue.SECTOR
        image = bytearray(sector * 40)
        first = vag("L1_hello_f", blocks=200)            # longer than a sector: covers 3
        image[2 * sector:2 * sector + len(first)] = first
        # Inside the first file's data: a sector that looks like a header must not count.
        image[3 * sector:3 * sector + 64] = vag("inside")[:64]
        second = vag("L1_bye", rate=11025)
        image[10 * sector:10 * sector + len(second)] = second
        broken = bytearray(vag("bad"))
        broken[16:20] = (1).to_bytes(4, "big")              # 1 Hz: not a VAG file
        image[20 * sector:20 * sector + len(broken)] = broken
        disc = FakeDisc(bytes(image))
        found = catalogue.scan(disc)
        self.assertEqual([(lba, name) for lba, _, _, name in found], [(2, "L1_hello_f"), (10, "L1_bye")])
        self.assertIsNone(catalogue.vag_header(disc, 39))
        self.assertIsNone(catalogue.vag_header(disc, 1000))

    def test_a_clip_and_its_whole_line(self):
        item = catalogue.clip(KEY, 5, 22050, 16 * 22050 // 28 * 2, "Speech", "L9_bang_f", [9, 2], "fr")
        self.assertAlmostEqual(item["seconds"], 2.0, places=2)
        self.assertEqual(item["levels"], [2, 9])
        self.assertEqual(item["base"], "L9_bang")
        line = catalogue.whole_line(item)
        self.assertEqual((line["id"], line["start"], line["end"]), (f"{KEY}:0", 0.0, item["seconds"]))


class Files(unittest.TestCase):
    def test_line_ids_and_names_are_safe_on_every_system(self):
        Workspace.check_id(f"{KEY}:12")
        for bad in ("", "x", f"{KEY}", f"{KEY}:", "../../etc:1", f"{KEY}:1/2", f"{KEY.upper()}:1"):
            with self.assertRaises(ValueError):
                Workspace.check_id(bad)
        self.assertEqual(safe_name("Captain Qwark"), "Captain_Qwark")
        self.assertEqual(safe_name("..\\..//CON:"), "CON")
        self.assertEqual(safe_name("¿?"), "unnamed")
        self.assertEqual(safe_name("Mr. Fizzwidget é"), "Mr_Fizzwidget")
        self.assertLessEqual(len(safe_name("x" * 300)), 48)
        for bad in (("rac1", "../x"), ("RAC1", "pal"), ("", "pal")):
            with self.assertRaises(ValueError):
                Workspace.of(Path("v"), *bad)

    def test_files_are_written_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b.json"
            write_json(path, {"text": "Clank: «à bientôt»"})
            self.assertEqual(read_json(path, None), {"text": "Clank: «à bientôt»"})
            self.assertEqual(list(path.parent.iterdir()), [path])
            self.assertEqual(read_json(Path(tmp) / "none.json", 7), 7)
            sound = Path(tmp) / "s.wav"
            write_wav(sound, lab.pcm_of(tone(0.1)), 16000)
            pcm, rate, channels = read_wav(sound)
            self.assertEqual((rate, channels, len(pcm)), (16000, 1, 3200))

    def test_takes_are_numbered_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            space = Workspace(Path(tmp))
            line = f"{KEY}:0"
            self.assertEqual(space.next_take(line), 1)
            for number in (1, 2, 10):
                write_wav(space.takes(line) / f"{number}.wav", b"\0\0", 8000)
                write_json(space.takes(line) / f"{number}.json", {"text": str(number)})
            write_wav(space.takes(line) / "3.wav", b"\0\0", 8000)                  # no meta: half made
            write_json(space.takes(line) / "notes.json", {})
            self.assertEqual([t["take"] for t in space.take_list(line)], [1, 2, 10])
            self.assertEqual(space.next_take(line), 11)


def line(ident: str, start: float, end: float, text: str = "", **extra) -> dict:
    return {"id": ident, "start": start, "end": end, "text": text, "text_source": "", "voice": None,
            "character": None, "character_source": "", **extra}


def disc_data(clips: list[dict]) -> dict:
    return {"format": 1, "game": "rac1", "version": "ntsc", "clips": clips, "disc": "/nowhere.iso"}


def clip_of(key: str, language: str, lines: list[dict], name: str = "scene", group: str = "Scenes",
            seconds: float = 10.0, rate: int = 16000) -> dict:
    return {"key": key, "lba": 1, "rate": rate, "seconds": seconds, "group": group, "name": name, "base": name,
            "levels": [1], "language": language, "lines": lines}


def around(centre: list[float], spread: float, rng: random.Random) -> list[float]:
    return [c + rng.gauss(0, spread) for c in centre]


class Voices(unittest.TestCase):
    def setUp(self):
        rng = random.Random(4)
        self.rng = rng
        self.centres = []
        for _ in range(3):
            v = [rng.gauss(0, 1) for _ in range(192)]
            self.centres.append(speakers.normalised(v))

    def items(self, counts, spread=0.025, seconds=3.0):
        out = []
        for k, count in enumerate(counts):
            for n in range(count):
                out.append((f"{k}-{n}", around(self.centres[k], spread, self.rng), seconds))
        return out

    def test_three_actors_make_three_voices_the_biggest_first(self):
        numbers = speakers.group(self.items([5, 20, 9]))
        groups = {}
        for ident, number in numbers.items():
            groups.setdefault(number, set()).add(ident.split("-")[0])
        self.assertEqual(len(groups), 3)
        self.assertTrue(all(len(actors) == 1 for actors in groups.values()))
        self.assertEqual(groups[0], {"1"})
        self.assertEqual(groups[2], {"0"})

    def test_short_lines_join_a_voice_or_stay_out(self):
        items = self.items([6, 6])
        items.append(("short-near", around(self.centres[0], 0.02, self.rng), 0.5))
        items.append(("short-far", [self.rng.gauss(0, 1) for _ in range(192)], 0.5))
        numbers = speakers.group(items)
        self.assertEqual(numbers["short-near"], numbers["0-0"])
        self.assertEqual(numbers["short-far"], -1)
        # Only short lines: nothing to start a voice from.
        self.assertEqual(set(speakers.group([("a", self.centres[0], 0.4)]).values()), {-1})
        self.assertEqual(speakers.group([]), {})

    def test_one_actor_heard_two_ways_is_one_voice(self):
        # Two tight bunches of one voice (calm, shouting) whose centres are close.
        calm = [(f"c{n}", around(self.centres[0], 0.02, self.rng), 3.0) for n in range(6)]
        loud_centre = speakers.normalised([a + 0.35 * b for a, b in zip(self.centres[0], self.centres[1])])
        loud = [(f"l{n}", around(loud_centre, 0.02, self.rng), 3.0) for n in range(6)]
        numbers = speakers.group(calm + loud)
        self.assertEqual(len(set(numbers.values())), 1)

    def two_languages(self) -> dict:
        clips = []
        for language, offset in (("en", 0), ("fr", 1)):
            for n in range(4):
                key = f"{n + offset * 8:016x}"
                clips.append(clip_of(key, language, [line(f"{key}:0", 0, 3, "Hi"), line(f"{key}:1", 3, 6, "Bye")],
                                     name=f"scene{n}"))
        return disc_data(clips)

    def test_voices_per_language_and_names_reach_the_other_language(self):
        data = self.two_languages()
        prints = {}
        for item, ln in Workspace.lines(data):
            who = int(ln["id"].endswith(":1")) + (2 if item["language"] == "fr" else 0)
            prints[ln["id"]] = around(self.centres[who % 3] if who < 3 else speakers.normalised(
                [-c for c in self.centres[0]]), 0.02, self.rng)
        count = speakers.assign_voices(data, prints)
        self.assertEqual(count, 4)
        voices = {ln["voice"] for _, ln in Workspace.lines(data)}
        self.assertEqual({v.split("-")[0] for v in voices}, {"en", "fr"})
        first = next(ln for item, ln in Workspace.lines(data) if item["language"] == "en" and ln["id"].endswith(":0"))
        self.assertEqual(speakers.name_voice(data, first["voice"], "Clank"), 4)
        spread = speakers.spread_names(data)
        self.assertEqual([who for _, who in spread], ["Clank"])
        french = [ln for item, ln in Workspace.lines(data) if item["language"] == "fr" and ln["id"].endswith(":0")]
        self.assertEqual({ln["character"] for ln in french}, {"Clank"})
        self.assertEqual({ln["character_source"] for ln in french}, {"matched"})
        # Run again: nothing more to spread, and a matched voice is not counted as named by hand.
        self.assertEqual(speakers.spread_names(data), [])
        summary = {v["voice"]: v for v in speakers.voices(data)}
        self.assertEqual(summary[first["voice"]]["character"], "Clank")
        self.assertEqual(summary[first["voice"]]["lines"], 4)

    def test_a_line_named_by_hand_keeps_its_name(self):
        data = self.two_languages()
        for _, ln in Workspace.lines(data):
            ln["voice"] = "en-0"
        target = data["clips"][0]["lines"][1]["id"]
        speakers.name_line(data, target, "Ratchet")
        speakers.name_voice(data, "en-0", "Clank")
        self.assertEqual(Workspace.find(data, target)[1]["character"], "Ratchet")
        speakers.name_line(data, target, None)
        self.assertEqual(Workspace.find(data, target)[1]["character_source"], "")
        with self.assertRaises(KeyError):
            speakers.name_line(data, f"{KEY}:99", "x")

    def test_lines_without_a_print_or_speech_have_no_voice(self):
        data = self.two_languages()
        first = data["clips"][0]["lines"][0]
        first["speech"] = False
        prints = {ln["id"]: self.centres[0] for _, ln in Workspace.lines(data)}
        del prints[data["clips"][0]["lines"][1]["id"]]
        speakers.assign_voices(data, prints)
        self.assertIsNone(first["voice"])
        self.assertIsNone(data["clips"][0]["lines"][1]["voice"])


class Lines(unittest.TestCase):
    def test_a_long_clip_is_split_at_whispers_parts(self):
        item = clip_of(KEY, "en", [], seconds=12.0)
        segments = [{"start": 0.0, "end": 3.0, "text": " Hello there."}, {"start": 3.0, "end": 3.4, "text": "Uh"},
                    {"start": 4.0, "end": 8.0, "text": "Second line."}, {"start": 8.0, "end": 8.0, "text": "  "},
                    {"start": 8.5, "end": 14.0, "text": "Past the end."}]
        lines = lab.split_clip(item, segments)
        self.assertEqual([(ln["start"], ln["end"], ln["text"]) for ln in lines],
                         [(0.0, 3.4, "Hello there. Uh"), (4.0, 8.0, "Second line."), (8.5, 12.0, "Past the end.")])
        self.assertEqual([ln["id"] for ln in lines], [f"{KEY}:0", f"{KEY}:1", f"{KEY}:2"])
        self.assertEqual(lab.split_clip(item, []), [])

    def test_missed_words(self):
        self.assertEqual(lab.missed_words("Hello, Clank!", "hello clank"), 0.0)
        self.assertEqual(lab.missed_words("", "hello clank"), 1.0)
        self.assertAlmostEqual(lab.missed_words("hello uh clank", "hello there clank"), 1 / 3)
        self.assertEqual(lab.missed_words("anything", ""), 0.0)
        self.assertEqual(lab.missed_words("Ça va? Très bien.", "ça va très bien"), 0.0)
        self.assertEqual(lab.missed_words("こんにちは", "こんにちは"), 0.0)

    def test_a_take_goes_in_the_old_lines_place(self):
        rate = 1000
        base = array.array("h", [1000] * 5000)
        take = array.array("h", [7] * 1500)
        lost = lab.place(base, take, rate, 1.0, 2.0, 3.0)
        self.assertEqual(lost, 0)
        self.assertEqual(base[500], 1000)                 # before the line: untouched
        self.assertEqual(base[1500], 7)                   # the old line silenced, the take in
        self.assertEqual(base[2300], 1007)                # after the old line, the take over the rest
        self.assertEqual(base[2600], 1000)                # after the take
        self.assertEqual(len(base), 5000)

    def test_a_take_longer_than_its_room_is_cut_with_a_fade(self):
        rate = 1000
        base = array.array("h", [0] * 3000)
        take = array.array("h", [20000] * 2500)
        lost = lab.place(base, take, rate, 1.0, 1.5, 2.0)
        self.assertAlmostEqual(lost, 1.5)
        self.assertEqual(base[1500], 20000)
        self.assertLess(abs(base[1999]), 20000)
        self.assertEqual(base[2000], 0)
        # At the clip's end with no next line, cut at the clip's end.
        base = array.array("h", [0] * 3000)
        self.assertAlmostEqual(lab.place(base, take, rate, 2.0, 2.5, None), 1.5)
        # Loud old line plus loud take never wraps around.
        base = array.array("h", [30000] * 3000)
        lab.place(base, array.array("h", [30000] * 3000), rate, 0.0, 0.01, None)
        self.assertEqual(max(base), 32767)

    def test_sound_helpers(self):
        data = tone(1.0, 16000)
        self.assertEqual(len(lab.resample(data, 16000, 8000)), 8000)
        self.assertEqual(len(lab.resample(data, 16000, 48000)), 48000)
        self.assertEqual(lab.resample(array.array("h"), 1, 2), array.array("h"))
        self.assertAlmostEqual(lab.level(data), 0.3 / math.sqrt(2), places=2)
        self.assertEqual(lab.level(array.array("h")), 0.0)
        stereo = array.array("h", [100, 300] * 4)
        self.assertEqual(list(lab.mono(lab.pcm_of(stereo), 2)), [200] * 4)


class Work(unittest.TestCase):
    """A workspace made by hand, as read_disc and listening leave it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.space = Workspace(Path(self.tmp.name) / "rac1-ntsc")
        self.whole = "aaaaaaaaaaaaaaaa"
        self.scene = "bbbbbbbbbbbbbbbb"
        self.data = disc_data([
            clip_of(self.whole, "en", [line(f"{self.whole}:0", 0, 2.0, "Need a hand?", character="Clank",
                                            character_source="voice", voice="en-0")], name="help", group="Help",
                    seconds=2.0),
            clip_of(self.scene, "en", [line(f"{self.scene}:0", 0.5, 2.5, "Hello.", character="Clank",
                                            character_source="voice", voice="en-0"),
                                       line(f"{self.scene}:1", 3.0, 5.0, "Hi Clank.", character="Ratchet",
                                            character_source="voice", voice="en-1")], seconds=6.0),
        ])
        self.space.save(self.data)
        for item, ln in Workspace.lines(self.data):
            write_wav(self.space.snippet(ln["id"]), lab.pcm_of(tone(ln["end"] - ln["start"])), 16000)

    def tearDown(self):
        self.tmp.cleanup()

    def take(self, line_id: str, number: int, seconds: float) -> None:
        folder = self.space.takes(line_id)
        write_wav(folder / f"{number}.wav", lab.pcm_of(tone(seconds, pitch=440.0, loud=0.5)), 16000)
        write_json(folder / f"{number}.json", {"text": "x", "seconds": seconds})

    def test_a_reference_is_made_of_the_characters_surest_lines_once(self):
        prints = {f"{self.whole}:0": [1.0, 0.0], f"{self.scene}:0": [0.0, 1.0]}
        path, text = lab.make_reference(self.space, self.data, "Clank", "en", (3.0, 30.0), prints)
        pcm, rate, _ = read_wav(path)
        self.assertAlmostEqual(len(pcm) / 2 / rate, 2.0 + lab.SAMPLE_GAP + 2.0, places=2)
        self.assertEqual(set(text.split(" ")), {"Need", "a", "hand?", "Hello."})
        stamp = path.stat().st_mtime_ns
        self.assertEqual(lab.make_reference(self.space, self.data, "Clank", "en", (3.0, 30.0), prints)[0].stat().st_mtime_ns,
                         stamp)
        with self.assertRaisesRegex(RuntimeError, "no lines"):
            lab.make_reference(self.space, self.data, "Qwark", "en", (3.0, 30.0))
        with self.assertRaisesRegex(RuntimeError, "no lines"):
            lab.make_reference(self.space, self.data, "Clank", "fr", (3.0, 30.0))

    def test_naming_and_words(self):
        lab.name(self.space, voice="en-1", character="Captain Qwark")
        data = self.space.load()
        self.assertEqual(Workspace.find(data, f"{self.scene}:1")[1]["character"], "Captain Qwark")
        self.assertIn("Captain Qwark", [c["name"] for c in self.space.characters()["characters"]])
        lab.name(self.space, voice="en-1", character="")
        self.assertIsNone(Workspace.find(self.space.load(), f"{self.scene}:1")[1]["character"])
        lab.name(self.space, line=f"{self.scene}:1", character="Ratchet")
        self.assertEqual(Workspace.find(self.space.load(), f"{self.scene}:1")[1]["character_source"], "line")
        lab.set_text(self.space, f"{self.whole}:0", "  Need a wrench?  ")
        self.assertEqual(lab.text_of(self.space, Workspace.find(self.space.load(), f"{self.whole}:0")[1]),
                         "Need a wrench?")
        lab.set_text(self.space, f"{self.whole}:0", "")
        self.assertEqual(self.space.characters()["edits"], {})
        with self.assertRaises(ValueError):
            lab.set_text(self.space, "../x", "x")

    def test_choosing_a_take(self):
        line_id = f"{self.whole}:0"
        self.take(line_id, 1, 1.0)
        lab.choose(self.space, line_id, 1)
        self.assertEqual(self.space.characters()["chosen"], {line_id: 1})
        with self.assertRaises(KeyError):
            lab.choose(self.space, line_id, 5)
        lab.choose(self.space, line_id, None)
        self.assertEqual(self.space.characters()["chosen"], {})

    def build(self, out: Path) -> dict:
        with mock.patch.object(lab, "clip_audio", return_value=(array.array("h", [1000] * 6 * 16000), 16000)):
            return lab.build_pack(self.space, self.data, out)

    def test_the_pack_holds_each_recording_with_its_takes(self):
        self.take(f"{self.whole}:0", 1, 1.5)
        self.take(f"{self.scene}:1", 1, 4.0)          # 4 s where 3 s are free before the clip's end... cut
        chars = self.space.characters()
        chars["chosen"] = {f"{self.whole}:0": 1, f"{self.scene}:1": 1}
        self.space.save_characters(chars)
        out = Path(self.tmp.name) / "pack"
        summary = self.build(out)
        self.assertEqual((summary["clips"], summary["lines"]), (2, 2))
        self.assertTrue(any("longer than its place" in n for n in summary["notes"]))
        manifest = json.loads((out / "openrac-mod.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["game"], manifest["version"], manifest["export"]), ("rac1", "ntsc", "voices-rac1-ntsc"))
        whole, rate, _ = read_wav(out / "audio" / f"{self.whole}.wav")
        self.assertEqual(rate, 16000)
        self.assertAlmostEqual(len(whole) / 2 / rate, 1.5, places=2)   # a whole clip is the take itself
        scene, rate, _ = read_wav(out / "audio" / f"{self.scene}.wav")
        self.assertEqual(len(scene) // 2, 6 * 16000)                     # a scene keeps its length
        samples = lab.samples_of(scene)
        self.assertEqual(samples[16000], 1000)                           # the other line: the disc's
        # A take whose file went missing: the disc's line plays, and the pack says so.
        (self.space.takes(f"{self.whole}:0") / "1.wav").unlink()
        summary = self.build(out)
        self.assertEqual(summary["clips"], 1)
        self.assertTrue(any("missing" in n for n in summary["notes"]))
        self.assertFalse((out / "audio" / f"{self.whole}.wav").exists())

    def test_the_pack_goes_into_the_launchers_mods_and_replaces_the_last(self):
        import overrides
        folder = Path(self.tmp.name) / "overrides"
        self.take(f"{self.whole}:0", 1, 1.0)
        lab.choose(self.space, f"{self.whole}:0", 1)
        with mock.patch.object(lab, "clip_audio", return_value=(array.array("h", [0] * 16000), 16000)):
            first = lab.install_pack(self.space, self.data, folder)
            second = lab.install_pack(self.space, self.data, folder)
        self.assertIsNotNone(first["pack"])
        installed = [p for p in (folder / "packs").iterdir() if p.is_dir() and not p.name.startswith(".")]
        self.assertEqual(len([p for p in installed if (p / "openrac-mod.json").is_file()]), 1)
        self.assertEqual(first["pack"], second["pack"])
        pack_dir = next(p for p in installed if (p / "openrac-mod.json").is_file())
        manifest, used, ignored, problems = overrides.check(pack_dir)
        self.assertEqual((used, ignored, problems), ([f"audio/{self.whole}.wav"], [], []))
        # No take kept any more: the pack goes.
        lab.choose(self.space, f"{self.whole}:0", None)
        with mock.patch.object(lab, "clip_audio", return_value=(array.array("h", [0] * 16000), 16000)):
            gone = lab.install_pack(self.space, self.data, folder)
        self.assertIsNone(gone["pack"])
        self.assertFalse(any((p / "openrac-mod.json").is_file() for p in (folder / "packs").iterdir() if p.is_dir()))

    def test_listening_writes_down_splits_and_groups(self):
        long_key = "cccccccccccccccc"
        self.data["clips"].append(clip_of(long_key, None, [line(f"{long_key}:0", 0, 12.0)], name="long",
                                          group="Speech", seconds=12.0))
        quiet_key = "dddddddddddddddd"
        self.data["clips"].append(clip_of(quiet_key, "en", [line(f"{quiet_key}:0", 0, 2.0)], name="hum",
                                          group="Speech", seconds=2.0))
        self.space.save(self.data)
        audio = (array.array("h", tone(12.0)), 16000)

        class Listener:
            def __init__(self):
                self.asked = []

            def request(self, op, on_event=None, **fields):
                self.asked.append((op, len(fields["files"])))
                results = []
                for path in fields["files"]:
                    if on_event:
                        on_event({"done": len(results) + 1, "total": len(fields["files"])})
                    if op == "embed":
                        voice = 1 if "cccc" in path else 0
                        results.append({"vector": [1.0, 0.0] if voice == 0 else [0.0, 1.0]})
                    elif "dddd" in path:
                        results.append({"text": "", "language": "en", "language_probability": 0.2,
                                        "segments": [{"start": 0, "end": 2, "text": "", "no_speech": 0.9}]})
                    else:
                        results.append({"text": "One. Two.", "language": "fr", "language_probability": 0.97,
                                        "segments": [{"start": 0.0, "end": 5.0, "text": "One.", "no_speech": 0.0},
                                                     {"start": 5.5, "end": 11.0, "text": "Two.", "no_speech": 0.0}]})
                return {"results": results}

        fake = Listener()
        with mock.patch.object(lab, "clip_audio", return_value=audio), \
                mock.patch.object(lab.hardware, "detect", return_value=computer(None)):
            result = lab.listen(self.space, envs.Home(Path(self.tmp.name)), None, worker=fake)
        data = self.space.load()
        long_clip = next(c for c in data["clips"] if c["key"] == long_key)
        self.assertEqual(long_clip["language"], "fr")
        self.assertEqual([ln["text"] for ln in long_clip["lines"]], ["One.", "Two."])
        self.assertFalse(Workspace.find(data, f"{quiet_key}:0")[1]["speech"])
        self.assertEqual(result["voices"], 2)                                   # en and fr
        # Names given before listening again stay with their lines.
        self.assertEqual(Workspace.find(data, f"{self.whole}:0")[1]["character"], "Clank")
        self.assertEqual([op for op, _ in fake.asked], ["transcribe", "embed"])
        # Again: nothing to write down or print again.
        fake.asked.clear()
        with mock.patch.object(lab, "clip_audio", return_value=audio), \
                mock.patch.object(lab.hardware, "detect", return_value=computer(None)):
            lab.listen(self.space, envs.Home(Path(self.tmp.name)), None, worker=fake)
        self.assertEqual(fake.asked, [])

    def test_listening_needs_its_environment(self):
        with self.assertRaisesRegex(RuntimeError, "install it first"):
            lab.listen_env(envs.Home(Path(self.tmp.name)))


if __name__ == "__main__":
    unittest.main()
