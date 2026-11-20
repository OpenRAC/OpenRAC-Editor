"""AI voices for the characters, from the command line or for the editor's Voices dock.

Each command prints its result as one line, `openrac-voices: <json>`, after any progress lines
(`openrac-voices-progress: <json>`); other lines are for people. `serve` keeps one process for the
dock: it reads a JSON command a line ({"id": 1, "command": "speak", ...}) and answers each the same
way, keeping the model loaded between lines.

    python3 openrac/tools/voice_lab.py --data DATA hardware
    python3 openrac/tools/voice_lab.py --data DATA models --languages en,fr
    python3 openrac/tools/voice_lab.py --data DATA install chatterbox-multilingual
    python3 openrac/tools/voice_lab.py --data DATA read DISC.iso
    python3 openrac/tools/voice_lab.py --data DATA --game rac1 --version ntsc listen --languages en
    python3 openrac/tools/voice_lab.py --data DATA --game rac1 --version ntsc voices
    python3 openrac/tools/voice_lab.py --data DATA --game rac1 --version ntsc name --voice en-3 --character Clank
    python3 openrac/tools/voice_lab.py --data DATA --game rac1 --version ntsc speak --model chatterbox-multilingual --line KEY:0
    python3 openrac/tools/voice_lab.py --data DATA --game rac1 --version ntsc pack

DATA is the launcher's data folder: the work goes in DATA/voices, the pack in DATA/overrides.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from pathlib import Path

# Run as a program its folder is on the path already; imported (tests), it goes last, as the
# extractor's modules put OpenRAC's tools right after the extractor's own folder.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.append(str(Path(__file__).resolve().parent))

import openrac_root  # noqa: F401,E402,I001  (OpenRAC's extractor on the import path)
from voices import envs, hardware, lab, models, speakers  # noqa: E402
from voices.workspace import Workspace, read_json  # noqa: E402

RESULT = "openrac-voices: "
PROGRESS = "openrac-voices-progress: "
GAMES = {"rac1": "Ratchet & Clank", "rac2": "Ratchet & Clank: Going Commando"}
# Names the dock offers for each game's speakers, as a start: the player may type any other.
KNOWN = {
    "rac1": ["Ratchet", "Clank", "Captain Qwark", "Chairman Drek", "Big Al", "Skid McMarx", "Helga",
             "Darla Gratch", "The Plumber", "Help Desk", "Infobot", "Gadgetron vendor", "Mayor"],
    "rac2": ["Ratchet", "Clank", "Captain Qwark", "Mr. Fizzwidget", "Angela Cross", "Thug leader",
             "Megacorp vendor", "Help Desk", "Tour guide", "Arena announcer"],
}


def emit(prefix: str, value) -> None:
    sys.stdout.write(prefix + json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class Session:
    """What the commands share: the voices folder, the disc's workspace and the loaded model."""

    def __init__(self, voices: Path, overrides_folder: Path | None, game: str | None, version: str | None,
                 progress=None):
        self.home = envs.Home(voices)
        self.overrides = overrides_folder
        self.game, self.version = game, version
        self.speaker: lab.Speaker | None = None
        self.progress = progress or (lambda value: emit(PROGRESS, value))

    def report(self, stage: str, done: int, total: int, text: str = "") -> None:
        self.progress({"stage": stage, "done": done, "total": total, "text": text})

    def space(self) -> Workspace:
        if not self.game or not self.version:
            spaces = sorted(p for p in self.home.root.glob("*-*") if (p / "lines.json").is_file())
            if len(spaces) == 1:
                self.game, self.version = spaces[0].name.split("-", 1)
            else:
                raise RuntimeError("Say which game and disc (--game and --version), or read the disc first.")
        return Workspace.of(self.home.root, self.game, self.version)

    def close(self) -> None:
        if self.speaker is not None:
            self.speaker.close()
            self.speaker = None

    # ---- commands --------------------------------------------------------------------------------

    def hardware(self, **_) -> dict:
        return hardware.detect()

    def models(self, languages: str | list | None = None, **_) -> dict:
        data = models.load()
        hw = hardware.detect()
        langs = languages.split(",") if isinstance(languages, str) else (languages or ["en"])
        rows = models.ranking(data, hw, [lang for lang in langs if lang])
        for row in rows:
            row["installed"] = row["kind"] == "server" or self.home.env_state(row["id"]) is not None
        preset = models.preset_for(data, hw)
        return {"preset": preset, "presets": data["presets"], "models": rows, "hardware": hw,
                "updated": data["updated"], "listen": self.home.env_state(envs.LISTEN) is not None}

    def install(self, model: str, **_) -> dict:
        self.close()
        if model == envs.LISTEN:
            return lab.install_listen(self.home, self.report)
        item = models.model(models.load(), model)
        if item["kind"] != "python":
            raise RuntimeError(f"{item['name']} runs in its own server: start it as its page says "
                               f"({item['server']['setup']}), then pick it here.")
        hw = hardware.detect()
        device = models.device_for(item, hw)
        backend = envs.torch_backend({**hw, "device": device})
        need = item["download_gb"] + envs.ENV_GB.get(device, 2.0)
        self.report("install", 0, 1, f"Installing {item['name']}")
        state = self.home.install(model, item["python"], item["packages"], backend, need,
                                  report=lambda line: self.report("install", 0, 1, line))
        self.report("install", 1, 1, f"{item['name']} is installed")
        return state

    def remove(self, model: str, **_) -> dict:
        self.close()
        return {"removed": self.home.remove(model)}

    def read(self, disc: str, **_) -> dict:
        space = lab.read_disc(Path(disc), self.home.root, self.report)
        self.game, self.version = space.root.name.split("-", 1)
        return self.summary()

    def summary(self, **_) -> dict:
        space = self.space()
        data = space.load()
        chars = space.characters()
        languages: dict[str, int] = {}
        for item in data["clips"]:
            languages[item.get("language") or "?"] = languages.get(item.get("language") or "?", 0) + len(item["lines"])
        return {"game": data["game"], "version": data["version"], "title": GAMES.get(data["game"], data["game"]),
                "clips": len(data["clips"]), "lines": sum(len(c["lines"]) for c in data["clips"]),
                "languages": languages, "listened": any(line.get("voice") for _, line in Workspace.lines(data)),
                "characters": [c["name"] for c in chars["characters"]], "known": KNOWN.get(data["game"], []),
                "chosen": len(chars["chosen"]), "edits": len(chars["edits"]), "disc": data.get("disc")}

    def listen(self, languages: str | list | None = None, **_) -> dict:
        langs = languages.split(",") if isinstance(languages, str) else languages
        return lab.listen(self.space(), self.home, [lang for lang in langs if lang] if langs else None, self.report)

    def voices(self, **_) -> dict:
        space = self.space()
        data = space.load()
        found = speakers.voices(data)
        samples: dict[str, list[str]] = {}
        for item, line in Workspace.lines(data):
            voice = line.get("voice")
            if voice and len(samples.setdefault(voice, [])) < 3 and line["end"] - line["start"] >= 1.5:
                samples[voice].append(line["id"])
        for entry in found:
            entry["samples"] = [str(space.snippet(i)) for i in samples.get(entry["voice"], [])]
            entry["sample_lines"] = samples.get(entry["voice"], [])
        return {"voices": found}

    def lines(self, character: str | None = None, language: str | None = None, voice: str | None = None,
              search: str | None = None, limit: int = 500, offset: int = 0, **_) -> dict:
        space = self.space()
        data = space.load()
        chars = space.characters()
        rows = []
        needle = (search or "").lower()
        for item, line in Workspace.lines(data):
            if not line.get("speech", True):
                continue
            if character is not None and (line.get("character") or "") != character:
                continue
            if language and item.get("language") != language:
                continue
            if voice and line.get("voice") != voice:
                continue
            text = chars["edits"].get(line["id"]) or line.get("text") or ""
            if needle and needle not in text.lower() and needle not in item["name"].lower():
                continue
            rows.append({"id": line["id"], "clip": item["name"], "group": item["group"], "levels": item["levels"],
                         "language": item.get("language"), "start": line["start"], "end": line["end"],
                         "text": text, "disc_text": line.get("text") or "", "edited": line["id"] in chars["edits"],
                         "character": line.get("character"), "voice": line.get("voice"),
                         "snippet": str(space.snippet(line["id"])), "chosen": chars["chosen"].get(line["id"]),
                         "takes": space.take_list(line["id"])})
        total = len(rows)
        return {"total": total, "lines": rows[int(offset):int(offset) + int(limit)]}

    def name(self, voice: str | None = None, line: str | None = None, character: str | None = None, **_) -> dict:
        return lab.name(self.space(), voice=voice, line=line, character=character)

    def text(self, line: str, text: str | None = None, **_) -> dict:
        lab.set_text(self.space(), line, text)
        return {"line": line, "text": text or ""}

    def speak(self, model: str, line: str | list, text: str | None = None, seed: int | None = None,
              options: dict | None = None, server: str | None = None, check: bool = True, **_) -> dict:
        space = self.space()
        data = space.load()
        self.loaded(model, server, check)
        assert self.speaker is not None
        ids = [line] if isinstance(line, str) else list(line)
        takes, failed = [], []
        for done, ident in enumerate(ids):
            self.report("speak", done, len(ids), ident)
            try:
                takes.append({"line": ident, **self.speaker.speak(space, data, ident, text if len(ids) == 1 else None,
                                                                  seed, options, self.report)})
            except RuntimeError as exc:
                failed.append({"line": ident, "error": str(exc)})
        self.report("speak", len(ids), len(ids), "")
        return {"takes": takes, "failed": failed}

    def loaded(self, model: str, server: str | None = None, check: bool = True) -> lab.Speaker:
        if self.speaker is None or self.speaker.model["id"] != model:
            self.close()
            self.report("load", 0, 1, "Loading the voice model (the first time it also downloads it)")
            self.speaker = lab.Speaker(self.home, model, check=check, server=server)
            self.report("load", 1, 1, "")
        return self.speaker

    def say(self, model: str, character: str, language: str, text: str, out: str, seed: int | None = None,
            options: dict | None = None, server: str | None = None, check: bool = True, **_) -> dict:
        """New words in a character's voice into OUT: a cutscene's voice line made in the editor."""
        space = self.space()
        speaker = self.loaded(model, server, check)
        return speaker.say(space, space.load(), character, language, text, Path(out), seed, options, self.report)

    def choose(self, line: str, take: int | None = None, **_) -> dict:
        lab.choose(self.space(), line, None if take in (None, "none", "") else int(take))
        return {"line": line, "take": take}

    def pack(self, out: str | None = None, **_) -> dict:
        space = self.space()
        data = space.load()
        if out:
            return lab.build_pack(space, data, Path(out), self.report)
        if self.overrides is None:
            raise RuntimeError("Open the editor from the launcher, so it knows where your packs go.")
        return lab.install_pack(space, data, self.overrides, self.report)

    def forget(self, **_) -> dict:
        """Throws away the disc's workspace (lines, names, takes)."""
        self.close()
        space = self.space()
        if space.root.exists():
            shutil.rmtree(space.root)
        return {"forgot": space.root.name}

    COMMANDS = ("hardware", "models", "install", "remove", "read", "summary", "listen", "voices", "lines", "name",
                "text", "speak", "say", "choose", "pack", "forget")

    def run(self, command: str, args: dict) -> dict:
        if command not in self.COMMANDS:
            raise RuntimeError(f"no command {command!r}")
        return getattr(self, command)(**args)


def serve(session: Session) -> int:
    """The dock's process: one JSON command a line, each answered on one line."""
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
            ident = request.get("id")
        except ValueError:
            emit(RESULT, {"id": None, "ok": False, "error": "not a JSON command"})
            continue
        if request.get("command") == "quit":
            emit(RESULT, {"id": ident, "ok": True})
            break
        session.progress = lambda value, ident=ident: emit(PROGRESS, {"id": ident, **value})
        try:
            result = session.run(request.get("command", ""), dict(request.get("args") or {}))
            emit(RESULT, {"id": ident, "ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001  (every failure is told to the dock, which shows it)
            traceback.print_exc(file=sys.stderr)
            emit(RESULT, {"id": ident, "ok": False, "error": str(exc) or type(exc).__name__})
    session.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, help="the launcher's data folder (voices go in DATA/voices)")
    parser.add_argument("--voices", type=Path, help="the voices folder itself (default: DATA/voices)")
    parser.add_argument("--overrides", type=Path, help="the overrides folder (default: DATA/overrides)")
    parser.add_argument("--game")
    parser.add_argument("--version")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    commands.add_parser("hardware")
    commands.add_parser("models").add_argument("--languages", default="en")
    commands.add_parser("install").add_argument("model")
    commands.add_parser("remove").add_argument("model")
    commands.add_parser("read").add_argument("disc")
    commands.add_parser("summary")
    commands.add_parser("listen").add_argument("--languages")
    commands.add_parser("voices")
    lines = commands.add_parser("lines")
    for name in ("--character", "--language", "--voice", "--search"):
        lines.add_argument(name)
    naming = commands.add_parser("name")
    naming.add_argument("--voice")
    naming.add_argument("--line")
    naming.add_argument("--character")
    text = commands.add_parser("text")
    text.add_argument("--line", required=True)
    text.add_argument("--text", default="")
    speak = commands.add_parser("speak")
    speak.add_argument("--model", required=True)
    speak.add_argument("--line", action="append", required=True)
    speak.add_argument("--text")
    speak.add_argument("--seed", type=int)
    speak.add_argument("--server")
    speak.add_argument("--no-check", dest="check", action="store_false")
    say = commands.add_parser("say")
    say.add_argument("--model", required=True)
    say.add_argument("--character", required=True)
    say.add_argument("--language", default="en")
    say.add_argument("--text", required=True)
    say.add_argument("--out", required=True)
    say.add_argument("--seed", type=int)
    say.add_argument("--server")
    say.add_argument("--no-check", dest="check", action="store_false")
    choose = commands.add_parser("choose")
    choose.add_argument("--line", required=True)
    choose.add_argument("--take")
    commands.add_parser("pack").add_argument("--out")
    commands.add_parser("forget")
    args = parser.parse_args(argv)
    voices = args.voices or (args.data / "voices" if args.data else None)
    if voices is None:
        parser.error("say where the work goes: --data or --voices")
    overrides_folder = args.overrides or (args.data / "overrides" if args.data else None)
    session = Session(voices, overrides_folder, args.game, args.version)
    if args.command == "serve":
        return serve(session)
    fields = {k: v for k, v in vars(args).items()
              if k not in ("data", "voices", "overrides", "game", "version", "command") and v is not None}
    if args.command == "speak" and len(fields.get("line", [])) == 1:
        fields["line"] = fields["line"][0]
    try:
        result = session.run(args.command, fields)
        emit(RESULT, {"ok": True, "result": result})
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        emit(RESULT, {"ok": False, "error": str(exc) or type(exc).__name__})
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
