"""Who says each line: the lines' voices grouped, and the characters the player names.

Listening (the listen environment's worker) gives each line a voice print, a list of numbers that
is close for two recordings of one voice (an ECAPA speaker model). Lines of one language whose
prints are close form a voice. Each language is grouped on its own, because each language has its
own actors. The player hears a voice's lines and names it once ("Clank"): every line of that
voice is then Clank's. A name also reaches the other languages: the same line in French is the
French copy of an English line (same group, name and place in its clip), so a French voice most of
whose lines are copies of Clank's is Clank's too.

The grouping is plain Python: a pass that starts a new voice when a line is far from every voice
so far (longest lines first, as they are the surest), then rounds that move each line to its
nearest voice, then voices merged when their centres are close.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

# Two prints closer than this (cosine) are one voice. Measured on the discs' lines: one actor's
# lines are 0.5 to 0.95 apart, two actors' mostly below 0.35 (rac1: Ratchet and Clank 0.04 to 0.33).
SAME_VOICE = 0.5
# Voices whose centres are this close are merged (rac1: Qwark's two recording sessions are 0.58).
MERGE = 0.6
# Lines shorter than this make an unreliable print: they join the nearest voice, but never start one.
SURE_SECONDS = 1.2
ROUNDS = 4


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def normalised(v: list[float]) -> list[float]:
    n = math.sqrt(dot(v, v)) or 1.0
    return [x / n for x in v]


def centre(vectors: list[list[float]]) -> list[float]:
    total = [0.0] * len(vectors[0])
    for v in vectors:
        for i, x in enumerate(v):
            total[i] += x
    return normalised(total)


def group(items: list[tuple[str, list[float], float]], same: float = SAME_VOICE, merge: float = MERGE,
          sure: float = SURE_SECONDS) -> dict[str, int]:
    """Each item (id, print, seconds) to a voice number, the voice with the most lines first. Items
    shorter than SURE seconds join the nearest voice if it is close enough, else stay out (-1)."""
    if not items:
        return {}
    prints = {ident: normalised(vector) for ident, vector, _ in items}
    long_first = sorted(items, key=lambda item: -item[2])
    centres: list[list[float]] = []
    members: list[list[str]] = []
    for ident, _, seconds in long_first:
        if seconds < sure:
            continue
        v = prints[ident]
        scores = [dot(v, c) for c in centres]
        best = max(range(len(scores)), key=scores.__getitem__) if scores else -1
        if best >= 0 and scores[best] >= same:
            members[best].append(ident)
            centres[best] = centre([prints[m] for m in members[best]])
        else:
            centres.append(v)
            members.append([ident])
    sure_ids = [ident for ident, _, seconds in items if seconds >= sure]
    for _ in range(ROUNDS):
        if not centres:
            break
        assignment = {ident: max(range(len(centres)), key=lambda k: dot(prints[ident], centres[k])) for ident in sure_ids}
        members = [[i for i in sure_ids if assignment[i] == k] for k in range(len(centres))]
        kept = [k for k in range(len(centres)) if members[k]]
        centres = [centre([prints[m] for m in members[k]]) for k in kept]
        members = [members[k] for k in kept]
    merged = True
    while merged and len(centres) > 1:
        merged = False
        for a in range(len(centres)):
            for b in range(a + 1, len(centres)):
                if dot(centres[a], centres[b]) >= merge:
                    members[a] += members[b]
                    centres[a] = centre([prints[m] for m in members[a]])
                    del members[b], centres[b]
                    merged = True
                    break
            if merged:
                break
    order = sorted(range(len(members)), key=lambda k: -len(members[k]))
    out: dict[str, int] = {}
    for number, k in enumerate(order):
        for ident in members[k]:
            out[ident] = number
    final = [centres[k] for k in order]
    for ident, _, seconds in items:
        if ident in out:
            continue
        scores = [dot(prints[ident], c) for c in final]
        best = max(range(len(scores)), key=scores.__getitem__) if scores else -1
        out[ident] = best if best >= 0 and scores[best] >= same else -1
    return out


def assign_voices(data: dict, prints: dict[str, list[float]]) -> int:
    """Groups DATA's lines that have a print, language by language, and writes each line's voice
    ("en-0", "en-1", ...; None when it fits none). Returns how many voices there are."""
    by_language: dict[str, list[tuple[str, list[float], float]]] = defaultdict(list)
    where = {}
    for item in data["clips"]:
        for line in item["lines"]:
            where[line["id"]] = (item, line)
            if line["id"] in prints and line.get("speech", True) and item.get("language"):
                by_language[item["language"]].append((line["id"], prints[line["id"]], line["end"] - line["start"]))
    count = 0
    for item in data["clips"]:
        for line in item["lines"]:
            line["voice"] = None
    for language, items in sorted(by_language.items()):
        numbers = group(items)
        count += len({n for n in numbers.values() if n >= 0})
        for ident, number in numbers.items():
            where[ident][1]["voice"] = f"{language}-{number}" if number >= 0 else None
    return count


def counterpart(item: dict, line: dict) -> tuple:
    """What a line's copies in other languages share: its clip's group, name without a language
    ending, levels, and its place in the clip."""
    place = line["id"].rsplit(":", 1)[1]
    return (item["group"], item.get("base") or item["name"], tuple(item["levels"]), place)


def name_voice(data: dict, voice: str, character: str, source: str = "voice") -> int:
    """Gives every line of VOICE the character CHARACTER, but for lines the player set by hand.
    Returns how many lines it named."""
    named = 0
    for item in data["clips"]:
        for line in item["lines"]:
            if line.get("voice") == voice and line.get("character_source") != "line":
                line["character"] = character
                line["character_source"] = source
                named += 1
    return named


def name_line(data: dict, line_id: str, character: str | None) -> None:
    """The player's own choice for one line, which naming a voice leaves alone."""
    for item in data["clips"]:
        for line in item["lines"]:
            if line["id"] == line_id:
                line["character"] = character
                line["character_source"] = "line" if character else ""
                return
    raise KeyError(f"no line {line_id}")


def spread_names(data: dict, least: int = 3, share: float = 0.5) -> list[tuple[str, str]]:
    """Names the voices of other languages from the named ones: a voice no one named, more than
    SHARE of whose lines (and at least LEAST) are copies of one character's lines, is that
    character's. Returns the (voice, character) pairs it named."""
    named_copies: dict[tuple, Counter] = defaultdict(Counter)
    for item in data["clips"]:
        for line in item["lines"]:
            if line.get("character") and line.get("character_source") in ("voice", "line"):
                named_copies[counterpart(item, line)][line["character"]] += 1
    votes: dict[str, Counter] = defaultdict(Counter)
    sizes: Counter = Counter()
    named_voices = set()
    done_before: dict[str, set] = defaultdict(set)
    for item in data["clips"]:
        for line in item["lines"]:
            voice = line.get("voice")
            if not voice:
                continue
            sizes[voice] += 1
            done_before[voice].add(line.get("character") if line.get("character_source") == "matched" else None)
            if line.get("character_source") in ("voice", "line"):
                named_voices.add(voice)
            copies = named_copies.get(counterpart(item, line))
            if copies:
                votes[voice][copies.most_common(1)[0][0]] += 1
    done = []
    for voice, counter in sorted(votes.items()):
        if voice in named_voices:
            continue
        character, count = counter.most_common(1)[0]
        if done_before.get(voice) == {character}:
            continue                         # spread before: nothing new
        if count >= least and count > share * sizes[voice]:
            name_voice(data, voice, character, source="matched")
            done.append((voice, character))
    return done


def voices(data: dict) -> list[dict]:
    """Each voice: its language, how many lines, how long in all, and who it is (the name most of
    its lines have)."""
    found: dict[str, dict] = {}
    for item in data["clips"]:
        for line in item["lines"]:
            voice = line.get("voice")
            if not voice:
                continue
            entry = found.setdefault(voice, {"voice": voice, "language": item["language"], "lines": 0,
                                             "seconds": 0.0, "names": Counter()})
            entry["lines"] += 1
            entry["seconds"] += line["end"] - line["start"]
            if line.get("character"):
                entry["names"][line["character"]] += 1
    out = []
    for entry in found.values():
        names = entry.pop("names")
        entry["character"] = names.most_common(1)[0][0] if names else None
        entry["seconds"] = round(entry["seconds"], 1)
        out.append(entry)
    out.sort(key=lambda e: (e["language"], -e["lines"]))
    return out
