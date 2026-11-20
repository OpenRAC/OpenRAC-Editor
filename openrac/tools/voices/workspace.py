"""A disc's voice work, kept in voices/<game>-<version>/ (envs.Home):

    lines.json          the disc's speech (catalogue.py), with what listening and the player added:
                        each line's words, its voice and its character
    characters.json     the characters the player named, and the made lines (takes) each line uses
    snippets/<id>.wav   each line's own audio, cut from the disc for listening and as a sample
    refs/<char>-<lang>.wav    the sample a model clones a character from, and refs/<...>.json its words
    takes/<line>/<n>.wav      what a model made for a line; <n>.json says with what

Every JSON file is written beside itself and moved in place, so a crash never leaves half of one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import wave

ID = re.compile(r"^[0-9a-f]{16}:\d{1,4}$")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_wav(path: Path, pcm: bytes, rate: int, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with wave.open(str(temporary), "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm)
    os.replace(temporary, path)


def read_wav(path: Path) -> tuple[bytes, int, int]:
    """(16-bit samples, rate, channels) of a 16-bit WAV file."""
    with wave.open(str(path), "rb") as file:
        if file.getsampwidth() != 2:
            raise ValueError(f"{path.name} is not 16-bit")
        return file.readframes(file.getnframes()), file.getframerate(), file.getnchannels()


def safe_name(text: str) -> str:
    """A name a file can carry on every system: letters, digits, - and _."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return cleaned[:48] or "unnamed"


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)

    @classmethod
    def of(cls, voices: Path, game: str, version: str) -> "Workspace":
        if not re.fullmatch(r"[a-z0-9]+", game) or not re.fullmatch(r"[a-z0-9]+", version):
            raise ValueError(f"{game}/{version} is not a game and disc version")
        return cls(Path(voices) / f"{game}-{version}")

    # ---- the files -------------------------------------------------------------------------------

    @property
    def lines_file(self) -> Path:
        return self.root / "lines.json"

    @property
    def characters_file(self) -> Path:
        return self.root / "characters.json"

    def snippet(self, line_id: str) -> Path:
        return self.root / "snippets" / (self.check_id(line_id).replace(":", "-") + ".wav")

    def reference(self, character: str, language: str) -> Path:
        return self.root / "refs" / f"{safe_name(character)}-{safe_name(language)}.wav"

    def takes(self, line_id: str) -> Path:
        return self.root / "takes" / self.check_id(line_id).replace(":", "-")

    @staticmethod
    def check_id(line_id: str) -> str:
        if not ID.match(line_id or ""):
            raise ValueError(f"{line_id!r} is not a line")
        return line_id

    def load(self) -> dict:
        data = read_json(self.lines_file, None)
        if data is None:
            raise FileNotFoundError("the disc's speech has not been read yet")
        return data

    def save(self, data: dict) -> None:
        write_json(self.lines_file, data)

    def characters(self) -> dict:
        data = read_json(self.characters_file, {})
        data.setdefault("format", 1)
        data.setdefault("characters", [])     # {"id", "name"}
        data.setdefault("chosen", {})         # line id -> take number the pack uses
        data.setdefault("edits", {})          # line id -> the text the player gave it
        return data

    def save_characters(self, data: dict) -> None:
        write_json(self.characters_file, data)

    # ---- looking things up -----------------------------------------------------------------------

    @staticmethod
    def lines(data: dict):
        """(clip, line) of every line of DATA."""
        for item in data["clips"]:
            for line in item["lines"]:
                yield item, line

    @staticmethod
    def find(data: dict, line_id: str) -> tuple[dict, dict]:
        for item, line in Workspace.lines(data):
            if line["id"] == line_id:
                return item, line
        raise KeyError(f"no line {line_id}")

    def take_list(self, line_id: str) -> list[dict]:
        """The takes made for a line, oldest first, each with its number and file."""
        folder = self.takes(line_id)
        out = []
        for meta in sorted(folder.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else -1):
            if not meta.stem.isdigit():
                continue
            info = read_json(meta, {})
            wav = meta.with_suffix(".wav")
            if wav.is_file() and isinstance(info, dict):
                out.append({**info, "take": int(meta.stem), "file": str(wav)})
        return out

    def next_take(self, line_id: str) -> int:
        taken = [t["take"] for t in self.take_list(line_id)]
        return max(taken, default=0) + 1
