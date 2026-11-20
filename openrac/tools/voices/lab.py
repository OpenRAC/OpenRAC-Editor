"""The Voices dock's steps, each one a function voice_lab.py runs for it:

1. read      the disc's speech (catalogue.py) into the workspace
2. listen    each line cut from the disc; the words of lines without subtitles written down, long
             clips without subtitles split where the words pause, every line's voice print, and
             the voices grouped (speakers.py)
3. name      the player names a voice, or one line's speaker; names spread to other languages
4. speak     a model says a line's text (the disc's or the player's) in its character's voice,
             from a sample made of that character's own lines; each try is a take, checked by
             listening to it
5. pack      the takes the player chose, as a sound pack the game plays in place of the disc's

Each step reports progress through REPORT(stage, done, total, text) and can be run again: what an
earlier run made is kept unless it no longer fits (a line's text changed, a character renamed).
"""

from __future__ import annotations

import array
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from . import catalogue, envs, hardware, models, speakers
from .client import Worker
from .workspace import Workspace, read_json, read_wav, write_json, write_wav

# A clip longer than this without subtitles is split into lines where Whisper hears pauses.
SPLIT_SECONDS = 8.0
# Whisper parts shorter than this join their neighbour when a clip is split.
SHORTEST_PART = 1.0
# A line Whisper hears nothing in (this likely to be no speech, or no words) is left out of the dock.
NO_SPEECH = 0.6
# Whisper's language guess counts for a clip only when this sure.
SURE_LANGUAGE = 0.6
# Lines a sample is made of: their length, and the silence put between them.
SAMPLE_LINE_SECONDS = (1.5, 12.0)
SAMPLE_GAP = 0.25
# Tries per line before a take that does not say its text is kept anyway.
TRIES = 3
# A take that misses more than this share of its words is tried again.
MOST_MISSED = 0.35
# Lines heard per request to the listener, saved after each.
BATCH = 10
# Whisper's size for listening: large-v3-turbo on a graphics card, small on the processor.
WHISPER = {"cuda": "large-v3-turbo", "cpu": "small", "mps": "small"}
PACK_NAME = "AI voices"


def nothing(stage: str, done: int, total: int, text: str = "") -> None:
    pass


# ---- sound in the standard library -----------------------------------------------------------------


def samples_of(pcm: bytes) -> array.array:
    data = array.array("h")
    data.frombytes(pcm)
    if sys.byteorder == "big":
        data.byteswap()
    return data


def pcm_of(data: array.array) -> bytes:
    if sys.byteorder == "big":
        data = array.array("h", data)
        data.byteswap()
    return data.tobytes()


def mono(pcm: bytes, channels: int) -> array.array:
    data = samples_of(pcm)
    if channels == 1:
        return data
    return array.array("h", (sum(data[i:i + channels]) // channels for i in range(0, len(data), channels)))


def resample(data: array.array, source: int, target: int) -> array.array:
    """Linear steps from SOURCE to TARGET Hz: for joining samples of other rates into one sample.
    The worker resamples what a model made with a proper filter."""
    if source == target or not data:
        return array.array("h", data)
    count = max(1, round(len(data) * target / source))
    out = array.array("h", bytes(2 * count))
    ratio = (len(data) - 1) / max(1, count - 1)
    for i in range(count):
        at = i * ratio
        k = int(at)
        b = data[k + 1] if k + 1 < len(data) else data[k]
        out[i] = int(data[k] + (b - data[k]) * (at - k))
    return out


def level(data) -> float:
    """RMS loudness, 0 to 1."""
    if not data:
        return 0.0
    return math.sqrt(sum(s * s for s in data) / len(data)) / 32768.0


# ---- 1. read ---------------------------------------------------------------------------------------


def read_disc(disc: Path, voices: Path, report=nothing) -> Workspace:
    """Reads the disc's speech into its workspace. An earlier read keeps what listening and the
    player added to the lines that are still there."""
    report("read", 0, 1, "Reading the disc's speech")
    data = catalogue.read(disc, lambda done, total: report("read", done, total, "Searching the disc"))
    space = Workspace.of(voices, data["game"], data["version"])
    try:
        old = space.load()
    except FileNotFoundError:
        old = None
    if old is not None:
        kept = {c["key"]: c for c in old["clips"]}
        for item in data["clips"]:
            before = kept.get(item["key"])
            if before is None:
                continue
            # A clip split by listening keeps its lines; a line keeps its words, voice and name.
            if len(before["lines"]) > 1 and len(item["lines"]) == 1:
                item["lines"] = before["lines"]
            else:
                by_id = {line["id"]: line for line in before["lines"]}
                for line in item["lines"]:
                    if line["id"] in by_id:
                        old_line = by_id[line["id"]]
                        for field in ("voice", "character", "character_source", "speech"):
                            if field in old_line:
                                line[field] = old_line[field]
                        if old_line.get("text") and not line.get("text"):
                            line["text"], line["text_source"] = old_line["text"], old_line.get("text_source", "")
            if before.get("language") and not item.get("language"):
                item["language"] = before["language"]
                item["language_source"] = before.get("language_source", "")
    data["disc"] = str(Path(disc).resolve())
    space.save(data)
    report("read", 1, 1, f"{sum(len(c['lines']) for c in data['clips'])} lines")
    return space


# ---- 2. listen -------------------------------------------------------------------------------------


def clip_audio(disc: Path, lba: int) -> tuple[array.array, int]:
    with catalogue.Disc(Path(disc)) as handle:
        pcm, rate = catalogue.clip_samples(handle, lba)
    return samples_of(pcm), rate


def cut(data: array.array, rate: int, start: float, end: float) -> array.array:
    return data[max(0, int(start * rate)):max(0, int(end * rate))]


def make_snippets(space: Workspace, data: dict, languages: list[str] | None, report=nothing) -> list[str]:
    """Each line's own audio, cut from the disc where it is missing. Returns the lines that have one."""
    wanted = [(item, line) for item, line in Workspace.lines(data)
              if languages is None or item.get("language") in languages or item.get("language") is None]
    clips = {}
    for item, line in wanted:
        clips.setdefault(item["key"], item)
    ready = []
    for done, item in enumerate(clips.values()):
        report("cut", done, len(clips), item["name"])
        lines = item["lines"]
        if all(space.snippet(line["id"]).is_file() for line in lines):
            ready += [line["id"] for line in lines]
            continue
        audio, rate = clip_audio(Path(data["disc"]), item["lba"])
        for line in lines:
            part = cut(audio, rate, line["start"], line["end"])
            if len(part) < rate // 10:
                continue
            write_wav(space.snippet(line["id"]), pcm_of(part), rate)
            ready.append(line["id"])
    report("cut", len(clips), len(clips), "")
    return ready


def split_clip(item: dict, segments: list[dict]) -> list[dict]:
    """A clip's lines from Whisper's parts: a part shorter than SHORTEST_PART joins the one before."""
    parts = []
    for segment in segments:
        text = segment.get("text", "").strip()
        if not text:
            continue
        start, end = float(segment["start"]), min(float(segment["end"]), item["seconds"])
        if parts and (end - start < SHORTEST_PART or parts[-1]["end"] - parts[-1]["start"] < SHORTEST_PART):
            parts[-1]["end"] = end
            parts[-1]["text"] = (parts[-1]["text"] + " " + text).strip()
        else:
            parts.append({"start": start, "end": end, "text": text})
    lines = []
    for n, part in enumerate(parts):
        lines.append({"id": f"{item['key']}:{n}", "start": round(part["start"], 3), "end": round(part["end"], 3),
                      "text": part["text"], "text_source": "listened", "voice": None, "character": None,
                      "character_source": ""})
    return lines


def listen_env(home: envs.Home) -> Path:
    state = home.env_state(envs.LISTEN)
    if state is None:
        raise RuntimeError("Listening is not installed yet: install it first.")
    return envs.python_in(home.env(envs.LISTEN))


def install_listen(home: envs.Home, report=nothing) -> dict:
    hw = hardware.detect()
    backend = envs.torch_backend(hw)
    report("install", 0, 1, "Installing listening (Whisper and a speaker model)")
    state = home.install(envs.LISTEN, envs.LISTEN_PYTHON, envs.LISTEN_PACKAGES, backend,
                         need_gb=envs.ENV_GB.get(hw["device"], 2.0) + 2.0,
                         report=lambda line: report("install", 0, 1, line))
    report("install", 1, 1, "Listening is installed")
    return state


def listen(space: Workspace, home: envs.Home, languages: list[str] | None = None, report=nothing,
           worker: Worker | None = None) -> dict:
    """Step 2 for the lines of LANGUAGES (all when None)."""
    data = space.load()
    hw = hardware.detect()
    device = "cuda" if hw["device"] == "cuda" else "cpu"
    own = worker is None
    worker = worker or Worker(listen_env(home), env=home.worker_env())
    try:
        # Words: the lines without subtitles, and the language of clips whose name does not say it.
        make_snippets(space, data, languages, report)
        todo = [(item, line) for item, line in Workspace.lines(data)
                if (languages is None or item.get("language") in languages or item.get("language") is None)
                and not line.get("text") and space.snippet(line["id"]).is_file() and line.get("speech", True)]
        for first in range(0, len(todo), BATCH):
            # A batch at a time, each saved: a run stopped halfway keeps what it heard.
            batch = todo[first:first + BATCH]
            files = [str(space.snippet(line["id"])) for _, line in batch]
            reply = worker.request("transcribe", files=files, device=device, model=WHISPER[device], language=None,
                                   on_event=lambda e, first=first: report("words", first + e["done"], len(todo), ""))
            for (item, line), result in zip(batch, reply["results"]):
                if "error" in result:
                    continue
                segments = result.get("segments", [])
                if not result.get("text") or all(s["no_speech"] >= NO_SPEECH for s in segments):
                    line["speech"] = False
                    continue
                if not item.get("language") and result.get("language_probability", 0) >= SURE_LANGUAGE:
                    item["language"] = result["language"]
                    item["language_source"] = "listened"
                whole = len(item["lines"]) == 1 and line["start"] == 0
                if whole and item["seconds"] > SPLIT_SECONDS and len(segments) > 1:
                    item["lines"] = split_clip(item, segments) or item["lines"]
                    for new in item["lines"]:
                        space.snippet(new["id"]).unlink(missing_ok=True)
                else:
                    line["text"], line["text_source"] = result["text"], "listened"
            space.save(data)
        if todo:
            make_snippets(space, data, languages, report)
        # Voices: a print for every line with speech, kept between runs.
        prints_file = space.root / "prints.json"
        prints = read_json(prints_file, {})
        need = [line["id"] for item, line in Workspace.lines(data)
                if line["id"] not in prints and line.get("speech", True) and space.snippet(line["id"]).is_file()
                and (languages is None or item.get("language") in languages)]
        for first in range(0, len(need), BATCH * 5):
            # Saved a batch at a time too: a stopped run keeps the prints it made.
            batch = need[first:first + BATCH * 5]
            reply = worker.request("embed", files=[str(space.snippet(i)) for i in batch], device=device,
                                   on_event=lambda e, first=first: report("voices", first + e["done"], len(need), ""))
            for ident, result in zip(batch, reply["results"]):
                if "vector" in result:
                    prints[ident] = result["vector"]
            write_json(prints_file, prints)
        count = speakers.assign_voices(data, prints)
        # Names given before keep their voices: a renumbered voice is named again from its lines.
        for voice in {line["voice"] for _, line in Workspace.lines(data) if line.get("voice")}:
            names = [line["character"] for _, line in Workspace.lines(data)
                     if line.get("voice") == voice and line.get("character_source") in ("voice", "line")]
            if names:
                speakers.name_voice(data, voice, max(set(names), key=names.count))
        space.save(data)
        report("voices", 1, 1, f"{count} voices")
        return {"voices": count, "lines": sum(1 for _ in Workspace.lines(data))}
    finally:
        if own:
            worker.close()


# ---- 3. name ---------------------------------------------------------------------------------------


def name(space: Workspace, voice: str | None = None, line: str | None = None, character: str | None = None,
         spread: bool = True) -> dict:
    data = space.load()
    chars = space.characters()
    character = (character or "").strip() or None
    if character and character not in [c["name"] for c in chars["characters"]]:
        chars["characters"].append({"id": len(chars["characters"]) + 1, "name": character})
        space.save_characters(chars)
    named = 0
    if voice:
        if character is None:
            for _, item in Workspace.lines(data):
                if item.get("voice") == voice and item.get("character_source") != "line":
                    item["character"], item["character_source"] = None, ""
        else:
            named = speakers.name_voice(data, voice, character)
    if line:
        speakers.name_line(data, line, character)
        named += 1
    spread_to = speakers.spread_names(data) if spread and character else []
    space.save(data)
    return {"named": named, "spread": spread_to}


# ---- 4. speak --------------------------------------------------------------------------------------


def text_of(space: Workspace, line: dict) -> str:
    return space.characters()["edits"].get(line["id"]) or line.get("text") or ""


def set_text(space: Workspace, line_id: str, text: str | None) -> None:
    """The player's words for a line, or the disc's again (TEXT empty)."""
    chars = space.characters()
    Workspace.check_id(line_id)
    if text and text.strip():
        chars["edits"][line_id] = text.strip()
    else:
        chars["edits"].pop(line_id, None)
    space.save_characters(chars)


def make_reference(space: Workspace, data: dict, character: str, language: str, seconds: tuple[float, float],
                   prints: dict[str, list[float]] | None = None) -> tuple[Path, str]:
    """The sample a model clones CHARACTER from: their surest lines in LANGUAGE (closest to their
    voice's centre, of a usable length), joined with short silences up to SECONDS[1] long. Kept
    in refs/ with its words beside it; made again when the lines it was made of change."""
    least, most = seconds
    lines = [(item, line) for item, line in Workspace.lines(data)
             if line.get("character") == character and item.get("language") == language
             and line.get("speech", True) and space.snippet(line["id"]).is_file()
             and SAMPLE_LINE_SECONDS[0] <= line["end"] - line["start"] <= SAMPLE_LINE_SECONDS[1]]
    if not lines:
        lines = [(item, line) for item, line in Workspace.lines(data)
                 if line.get("character") == character and item.get("language") == language
                 and line.get("speech", True) and space.snippet(line["id"]).is_file()]
    if not lines:
        raise RuntimeError(f"{character} has no lines in this language to learn the voice from.")
    if prints:
        vectors = [prints[line["id"]] for _, line in lines if line["id"] in prints]
        if vectors:
            middle = speakers.centre(vectors)
            lines.sort(key=lambda pair: -speakers.dot(prints.get(pair[1]["id"], middle), middle))
    picked, total = [], 0.0
    for item, line in lines:
        if total >= most:
            break
        picked.append((item, line))
        total += line["end"] - line["start"]
    ids = [line["id"] for _, line in picked]
    target = space.reference(character, language)
    meta_file = target.with_suffix(".json")
    meta = read_json(meta_file, {})
    if target.is_file() and meta.get("lines") == ids and meta.get("most") == most:
        return target, meta.get("text", "")
    parts, rates = [], []
    for _, line in picked:
        pcm, rate, channels = read_wav(space.snippet(line["id"]))
        parts.append((mono(pcm, channels), rate))
        rates.append(rate)
    rate = max(set(rates), key=rates.count)
    joined = array.array("h")
    gap = array.array("h", bytes(2 * int(SAMPLE_GAP * rate)))
    for data_, source in parts:
        if joined:
            joined.extend(gap)
        joined.extend(resample(data_, source, rate))
    write_wav(target, pcm_of(joined), rate)
    text = " ".join(line.get("text", "") for _, line in picked).strip()
    write_json(meta_file, {"lines": ids, "most": most, "text": text, "seconds": round(len(joined) / rate, 2),
                           "enough": total >= least})
    return target, text


def missed_words(said: str, wanted: str) -> float:
    """The share of WANTED's words that SAID lacks (in order), ignoring case and punctuation."""
    def words(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())
    a, b = words(wanted), words(said)
    if not a:
        return 0.0
    # Longest common subsequence of words.
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0]
        for j, y in enumerate(b):
            current.append(previous[j] + 1 if x == y else max(previous[j + 1], current[j]))
        previous = current
    return 1.0 - previous[-1] / len(a)


class Speaker:
    """A model loaded in its worker, ready to speak lines, and the listen worker that checks them."""

    def __init__(self, home: envs.Home, model_id: str, catalogue_data: dict | None = None, check: bool = True,
                 server: str | None = None):
        self.home = home
        self.catalogue = catalogue_data or models.load()
        self.model = models.model(self.catalogue, model_id)
        hw = hardware.detect()
        self.device = models.device_for(self.model, hw)
        if self.model["kind"] == "python":
            state = home.env_state(model_id)
            if state is None:
                raise RuntimeError(f"{self.model['name']} is not installed yet: install it first.")
            python = envs.python_in(home.env(model_id))
        else:
            python = Path(sys.executable)
        self.worker = Worker(python, env=home.worker_env())
        self.worker.request("load", timeout=3600, backend=self.model["backend"], repo=self.model["repo"],
                            device=self.device, url=server or self.model.get("server", {}).get("url"))
        self.checker = None
        if check and home.env_state(envs.LISTEN) is not None:
            self.checker = Worker(envs.python_in(home.env(envs.LISTEN)), env=home.worker_env())

    def close(self) -> None:
        self.worker.close()
        if self.checker:
            self.checker.close()

    def check_language(self, language: str | None) -> str:
        if not language:
            raise RuntimeError("This line's language is not known yet: listen to the disc first.")
        if language not in self.model["languages"]:
            raise RuntimeError(f"{self.model['name']} does not speak this line's language ({language}).")
        return language

    def takes(self, space: Workspace, data: dict, character: str, language: str, words: str, outs, rate: int | None,
              loudness: float | None, seed: int | None, options: dict | None, report=nothing,
              extra: dict | None = None) -> dict:
        """Up to TRIES takes of WORDS in CHARACTER's voice, each into the next of OUTS (a callable
        giving (number, path)), until the listener hears enough of the words. Returns the best."""
        prints = read_json(space.root / "prints.json", {})
        reference, reference_text = make_reference(space, data, character, language,
                                                   tuple(self.model["reference_seconds"]), prints)
        seed = seed if seed is not None else random.randrange(1, 2 ** 31)
        best = None
        for attempt in range(TRIES):
            number, out = outs()
            out.parent.mkdir(parents=True, exist_ok=True)
            report("speak", attempt, TRIES, f"Take {number}")
            reply = self.worker.request("speak", timeout=1800, text=words, language=language,
                                        reference=str(reference), reference_text=reference_text,
                                        out=str(out), rate=rate, seed=seed + attempt,
                                        loudness=loudness, options=options or {})
            missed = None
            if self.checker is not None:
                heard = self.checker.request("transcribe", files=[str(out)], language=language,
                                             device="cuda" if self.device == "cuda" else "cpu",
                                             model=WHISPER.get(self.device, "small"))["results"][0]
                missed = missed_words(heard.get("text", ""), words) if "error" not in heard else None
            meta = {"text": words, "language": language, "character": character, "model": self.model["id"],
                    "seed": seed + attempt, "seconds": reply["seconds"], "rate": reply["rate"],
                    "missed": None if missed is None else round(missed, 3), "made": int(time.time()),
                    "options": options or {}, **(extra or {})}
            take = {**meta, "take": number, "file": str(out)}
            if best is None or (missed is not None and (best["missed"] is None or missed < best["missed"])):
                best = take
            write_json(out.with_suffix(".json"), meta)
            if missed is None or missed <= MOST_MISSED:
                break
        return best

    def speak(self, space: Workspace, data: dict, line_id: str, text: str | None = None, seed: int | None = None,
              options: dict | None = None, report=nothing) -> dict:
        """A new take of a disc's line, in its character's voice; the best becomes the line's chosen."""
        item, line = Workspace.find(data, line_id)
        character = line.get("character")
        if not character:
            raise RuntimeError("Name who says this line first.")
        language = self.check_language(item.get("language"))
        words = (text if text is not None else text_of(space, line)).strip()
        if not words:
            raise RuntimeError("This line has no words to say.")
        original = space.snippet(line_id)
        loudness = None
        if original.is_file():
            pcm, _, channels = read_wav(original)
            loudness = level(mono(pcm, channels)) or None
        folder = space.takes(line_id)

        def outs() -> tuple[int, Path]:
            number = space.next_take(line_id)
            return number, folder / f"{number}.wav"

        best = self.takes(space, data, character, language, words, outs, item["rate"], loudness, seed, options, report,
                          extra={"slot": round(line["end"] - line["start"], 3)})
        chars = space.characters()
        chars["chosen"][line_id] = best["take"]
        space.save_characters(chars)
        return best

    def say(self, space: Workspace, data: dict, character: str, language: str, text: str, out: Path,
            seed: int | None = None, options: dict | None = None, report=nothing) -> dict:
        """New words in CHARACTER's voice into OUT (a cutscene's voice line): the best of the tries."""
        language = self.check_language(language)
        words = (text or "").strip()
        if not words:
            raise RuntimeError("There are no words to say.")
        if not any(line.get("character") == character for _, line in Workspace.lines(data)):
            raise RuntimeError(f"No line of the disc is {character}'s yet: name their voice first.")
        out = Path(out)
        scratch = out.parent / f".{out.stem}-takes"
        count = iter(range(1, TRIES + 1))

        def outs() -> tuple[int, Path]:
            number = next(count)
            return number, scratch / f"{number}.wav"

        try:
            best = self.takes(space, data, character, language, words, outs, None, None, seed, options, report)
            out.parent.mkdir(parents=True, exist_ok=True)
            os.replace(best["file"], out)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return {**best, "file": str(out)}


def choose(space: Workspace, line_id: str, take: int | None) -> None:
    """The take the pack uses for a line, or none (the disc's own line plays)."""
    Workspace.check_id(line_id)
    chars = space.characters()
    if take is None:
        chars["chosen"].pop(line_id, None)
    else:
        if take not in [t["take"] for t in space.take_list(line_id)]:
            raise KeyError(f"line {line_id} has no take {take}")
        chars["chosen"][line_id] = take
    space.save_characters(chars)


# ---- 5. pack ---------------------------------------------------------------------------------------


FADE_SECONDS = 0.02


def place(base: array.array, take: array.array, rate: int, start: float, end: float, after: float | None) -> float:
    """Puts TAKE in BASE at START in place of the line that ran to END: the old line faded out and
    silenced, the take mixed in. AFTER is where the next line starts (None: the clip's end); a take
    longer than its room is cut there with a fade. Returns how many seconds it lost."""
    first, last = int(start * rate), min(len(base), int(end * rate))
    fade = max(1, int(FADE_SECONDS * rate))
    for i in range(first, last):
        if i < first + fade:
            base[i] = int(base[i] * (1 - (i - first) / fade))
        elif i > last - fade:
            base[i] = int(base[i] * (1 - (last - i) / fade))
        else:
            base[i] = 0
    room = int(((after if after is not None else len(base) / rate) - start) * rate)
    room = max(0, min(room, len(base) - first))
    lost = max(0, len(take) - room) / rate
    count = min(len(take), room)
    for k in range(count):
        gain = 1.0 if lost == 0 or k < count - fade else (count - k) / fade
        value = base[first + k] + int(take[k] * gain)
        base[first + k] = max(-32768, min(32767, value))
    return lost


def build_pack(space: Workspace, data: dict, out: Path, report=nothing) -> dict:
    """Writes OUT (a pack folder: openrac-mod.json and audio/<key>.wav) from the chosen takes, and
    returns what it holds and what did not fit."""
    chars = space.characters()
    chosen = chars["chosen"]
    by_clip: dict[str, list[tuple[dict, int]]] = defaultdict(list)
    clips = {}
    for item, line in Workspace.lines(data):
        if line["id"] in chosen:
            by_clip[item["key"]].append((line, chosen[line["id"]]))
            clips[item["key"]] = item
    if out.exists():
        shutil.rmtree(out)
    (out / "audio").mkdir(parents=True)
    notes = []
    written = placed = 0
    for done, (key, lines) in enumerate(sorted(by_clip.items())):
        item = clips[key]
        report("pack", done, len(by_clip), item["name"])
        whole = len(item["lines"]) == 1 and len(lines) == 1
        takes = []
        for line, number in lines:
            path = space.takes(line["id"]) / f"{number}.wav"
            if not path.is_file():
                notes.append(f"{item['name']}: take {number} of a line is missing; the disc's line plays")
                continue
            pcm, rate, channels = read_wav(path)
            takes.append((line, resample(mono(pcm, channels), rate, item["rate"])))
        if not takes:
            continue
        if whole:
            result = takes[0][1]
        else:
            base, rate = clip_audio(Path(data["disc"]), item["lba"])
            starts = sorted(line["start"] for line in item["lines"])
            for line, take in takes:
                after = next((s for s in starts if s > line["start"]), None)
                lost = place(base, take, rate, line["start"], line["end"], after)
                if lost > 0.05:
                    notes.append(f"{item['name']}: a line is {lost:.1f} s longer than its place in the scene "
                                 "and was cut; shorten its words")
            result = base
        write_wav(out / "audio" / f"{key}.wav", pcm_of(result), item["rate"])
        written += 1
        placed += len(takes)
    manifest = {"format": 1, "game": data["game"], "version": data["version"], "name": PACK_NAME,
                "author": "", "description": "Lines spoken by AI voice models in the characters' voices, "
                "made in OpenRAC Editor from your own disc.", "export": f"voices-{data['game']}-{data['version']}"}
    write_json(out / "openrac-mod.json", manifest)
    report("pack", len(by_clip), len(by_clip), "")
    return {"clips": written, "lines": placed, "notes": notes}


def install_pack(space: Workspace, data: dict, overrides_folder: Path, report=nothing) -> dict:
    """The pack built and put in the launcher's overrides folder (OpenRAC's overrides.py), in
    place of the one made before; with no take chosen, the one made before is removed."""
    import overrides
    with tempfile.TemporaryDirectory(prefix=".voices-", dir=overrides.ensure(overrides_folder)) as scratch:
        staged = Path(scratch) / "pack"
        summary = build_pack(space, data, staged, report)
        export = f"voices-{data['game']}-{data['version']}"
        if summary["clips"] > 0:
            # What the launcher's Mods page would say of it: a pack it refuses is never put in.
            try:
                _, used, _, problems = overrides.check(staged)
            except overrides.PackError as exc:
                used, problems = [], [str(exc)]
            if problems or len(used) != summary["clips"]:
                raise RuntimeError("The pack is not one the game takes: " + "; ".join(problems or ["files left out"]))
        if summary["clips"] == 0:
            pack = overrides.export_pack(overrides_folder, export)
            if pack:
                overrides.remove(overrides_folder, pack)
            return {**summary, "pack": None}
        pack = overrides.install(staged, overrides_folder, replace_export=export)
    return {**summary, "pack": pack}
