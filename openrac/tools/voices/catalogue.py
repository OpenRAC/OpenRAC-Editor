"""The speech of a disc: every recording of a voice, and the lines in it.

A clip is one VAG file on the disc, known by the key the game's sound packs replace it by
(OpenRAC's extractor/audio.py, stream_key): a pack's audio/<key>.wav plays in its place. A line is
what one person says: a whole clip, or a part of a scene's clip, where the scene's subtitles say
when each line starts and ends.

- Ratchet & Clank: the extractor's audio catalogue lists the speech (help, vendor, scenes, the
  levels' scene lines, the credits) with its language; the level headers' scene records give each
  scene's subtitles, and so its lines and their words (game_scenes.py reads them).
- Going Commando: its speech is VAG files named by the developers (L9_bang_f: level 9, French).
  The disc is searched for them, and music and effects are left out by their names; the
  language comes from the name's ending, else from listening (speakers.py).

Words no subtitle gives are written down later by listening. Standard library only.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import openrac_root  # noqa: F401,I001  (OpenRAC's extractor on the import path)
from disc import SECTOR, Disc

FORMAT = 1
TICKS = 60
VAG_HEADER = 0x30
MOST_VAG = 64 << 20
# Going Commando's music, sound effects and silences, by their names (the rest is speech). Its
# scenes' speech is named after the folder it was made in (X:\RC2\SOUND\SCENES, cut at 16 letters).
NOT_SPEECH = re.compile(r"""^(?:
    rc\d_l\d+_ | rat\d_l\d+_ | l\d+_transition | trans_silence | sfx_ | therm_ | .*bed\d* $ |
    electrolizerbed | infiltratorbed | breakable_door
)""", re.IGNORECASE | re.VERBOSE)
SCENE = re.compile(r"^x:\\rc\d\\sound", re.IGNORECASE)
# Going Commando's language endings: English has none, or _e.
ENDINGS = {"e": "en", "f": "fr", "g": "de", "s": "es", "i": "it"}


def vag_header(disc: Disc, lba: int) -> tuple[int, int, str] | None:
    """(data size, rate, name) of the VAG file at LBA, or None when there is none."""
    if (lba + 1) * SECTOR > disc.size:
        return None
    head = disc.read(lba * SECTOR, VAG_HEADER)
    if head[:4] != b"VAGp":
        return None
    size, rate = struct.unpack_from(">II", head, 12)
    if not 16 <= size <= MOST_VAG or not 4000 <= rate <= 96000:
        return None
    name = head[0x20:0x30].split(b"\0")[0].decode("latin-1").strip()
    return size, rate, name


def clip_samples(disc: Disc, lba: int) -> tuple[bytes, int]:
    """The clip at LBA as 16-bit mono samples (little-endian) and its rate."""
    import audio
    found = vag_header(disc, lba)
    if found is None:
        raise ValueError(f"no VAG file at sector {lba}")
    size, rate, _ = found
    return audio.decode_adpcm(disc.read(lba * SECTOR + VAG_HEADER, size)), rate


def clip_key(disc: Disc, lba: int) -> str:
    import audio
    return audio.stream_key(disc.read(lba * SECTOR, min(audio.STREAM_KEY_BYTES, disc.size - lba * SECTOR)))


def language_of_name(name: str) -> str | None:
    """Going Commando's language ending: L9_bang_f is French. Names are cut at 16 letters, so a cut
    name tells nothing (None)."""
    match = re.search(r"_([efgsiEFGSI])$", name)
    if match and len(name) < 16:
        return ENDINGS[match.group(1).lower()]
    return None


def base_name(name: str) -> str:
    """A line's name without its language ending, which every language's recording of it shares."""
    return re.sub(r"_([efgsiEFGSI])$", "", name) if len(name) < 16 else name


def clip(key: str, lba: int, rate: int, size: int, group: str, name: str, levels: list[int],
         language: str | None) -> dict:
    return {"key": key, "lba": lba, "rate": rate, "seconds": round(size / 16 * 28 / rate, 3), "group": group,
            "name": name, "base": base_name(name), "levels": sorted(levels), "language": language, "lines": []}


def whole_line(item: dict) -> dict:
    return {"id": f"{item['key']}:0", "start": 0.0, "end": item["seconds"], "text": "", "text_source": "",
            "voice": None, "character": None, "character_source": ""}


# ---- Ratchet & Clank ---------------------------------------------------------------------------


def rac1_scene_parts(disc: Disc) -> dict[str, list[dict]]:
    """For each scene's speech clip (by key): its lines, from the scene's subtitles in that
    clip's language, in seconds from the clip's start."""
    import game_scenes as gs
    toc = disc.read(disc.version.toc_lba * SECTOR, disc.version.toc_size)
    out: dict[str, list[dict]] = {}
    for level in range(19):
        try:
            info = disc.level_header(toc, level)
        except Exception:  # noqa: BLE001  (a level the table does not have)
            continue
        header = disc.read(info["header_lba"] * SECTOR, 0x2434)
        for record in gs.scene_records(header):
            try:
                chunks = gs.read_scene(disc, record)
            except Exception:  # noqa: BLE001  (a scene that does not read: its clip keeps one line)
                continue
            if chunks is None:
                continue
            start = max(0, chunks[0]["audio_start"])
            lines = []
            for chunk in chunks:
                for line in chunk["subtitles"]:
                    item = (line["start"], line["end"], tuple(sorted(line["text"].items())))
                    if item not in lines:
                        lines.append(item)
            for number, sector in enumerate(record["speech"]):
                language = gs.SPEECH_LANGUAGES[number]
                if sector <= 0 or language is None or vag_header(disc, sector) is None:
                    continue
                parts = []
                for begin, end, texts in lines:
                    text = dict(texts).get(language, "")
                    parts.append({"start": round(max(0, begin - start) / TICKS, 3),
                                  "end": round(max(0, end + 1 - start) / TICKS, 3), "text": text})
                out.setdefault(clip_key(disc, sector), parts)
    return out


def rac1_clips(disc: Disc) -> list[dict]:
    import audio
    survey = disc.survey()
    cat = audio.Catalogue(disc)
    audio.read_streams(cat, survey)
    scenes = rac1_scene_parts(disc)
    clips = []
    for entry in cat.files.values():
        if entry.kind != "speech" or entry.language is None or entry.group.startswith(audio.NOT_PLAYED):
            continue
        found = vag_header(disc, entry.lba)
        if found is None:
            continue
        size, rate, _ = found
        item = clip(entry.key, entry.lba, rate, size, entry.group, entry.name, entry.levels, entry.language)
        # The help desk's lines are named after the English ones, so every language's copy of a line
        # has the same name; a scene's lines are its subtitles, the same in every language.
        parts = scenes.get(entry.key)
        if parts:
            for n, part in enumerate(parts):
                if part["end"] <= part["start"]:
                    continue
                item["lines"].append({"id": f"{entry.key}:{n}", "start": part["start"],
                                      "end": min(part["end"], item["seconds"]), "text": part["text"],
                                      "text_source": "subtitle" if part["text"] else "", "voice": None,
                                      "character": None, "character_source": ""})
        if not item["lines"]:
            item["lines"].append(whole_line(item))
        clips.append(item)
    return clips


# ---- Going Commando -----------------------------------------------------------------------------


def scan(disc: Disc, start: int = 0, end: int | None = None, report=lambda done, total: None) -> list[tuple[int, int, int, str]]:
    """Every VAG file that starts a sector between START and END: (sector, size, rate, name). A file
    found is skipped over, so a sector inside one is never taken for another."""
    end = min(end if end is not None else disc.size // SECTOR, disc.size // SECTOR)
    found = []
    lba = start
    step = max(1, (end - start) // 100)
    mark = start
    while lba < end:
        head = vag_header(disc, lba)
        if head is None:
            lba += 1
        else:
            size, rate, name = head
            found.append((lba, size, rate, name))
            lba += max(1, -(-(size + VAG_HEADER) // SECTOR))
        if lba >= mark:
            report(lba - start, end - start)
            mark = lba + step
    return found


def rac2_clips(disc: Disc, report=lambda done, total: None) -> list[dict]:
    import wads
    version = wads.identify(disc)
    if version is None:
        raise ValueError("not a disc of the later games")
    levels = wads.WadDisc(disc, version).levels()
    ranges = []
    for level in levels:
        for kind in ("level", "audio", "scene"):
            part = level.get(kind)
            if part:
                ranges.append((part["lba"], part["lba"] + part["sectors"], level["id"]))
    clips: dict[str, dict] = {}
    for lba, size, rate, name in scan(disc, report=report):
        if NOT_SPEECH.match(name):
            continue
        key = clip_key(disc, lba)
        owners = [level for first, last, level in ranges if first <= lba < last]
        group = "Scenes" if SCENE.match(name) else "Level speech" if owners else "Speech"
        item = clips.get(key)
        if item is None:
            item = clips[key] = clip(key, lba, rate, size, group, name, owners, language_of_name(name))
            item["lines"].append(whole_line(item))
        else:
            item["levels"] = sorted(set(item["levels"]) | set(owners))
    return list(clips.values())


# ---- both ---------------------------------------------------------------------------------------


def identify(path: Path) -> tuple[str, str]:
    """(game, version) of a disc: rac1 and its version, or the later game wads.identify knows."""
    with Disc(path) as disc:
        try:
            return "rac1", disc.version.key
        except Exception:  # noqa: BLE001  (not Ratchet & Clank: one of the later games)
            import wads
            version = wads.identify(disc)
            if version is None:
                raise ValueError(f"{path.name} is not a Ratchet & Clank disc OpenRAC reads") from None
            return version.game, version.key


def read(path: Path, report=lambda done, total: None) -> dict:
    """The speech of the disc at PATH, as workspace.py keeps it (lines.json)."""
    game, version = identify(path)
    with Disc(path) as disc:
        if game == "rac1":
            clips = rac1_clips(disc)
        elif game == "rac2":
            clips = rac2_clips(disc, report)
        else:
            raise ValueError("only Ratchet & Clank and Going Commando have voices here yet")
    clips.sort(key=lambda c: (c["group"], c["levels"][:1], c["name"], c["language"] or "", c["key"]))
    return {"format": FORMAT, "game": game, "version": version, "clips": clips}
