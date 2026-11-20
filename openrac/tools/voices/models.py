"""The voice models the Voices dock offers (models.json), and which one a computer should start with.

A preset is a size of computer: no graphics card, then by the card's memory, and Apple Silicon.
Each lists its models best first, so the dock shows that order and starts on the first one the
computer can run (models' own "vram_gb" and "devices"). The player can choose any model; the
dock says why one may not fit instead of hiding it.
"""

from __future__ import annotations

import json
from pathlib import Path

CATALOGUE = Path(__file__).with_name("models.json")
# What a card needs over a model's weights for the rest of the work (the reference, the decoder).
HEADROOM_GB = 0.5
# A Mac's graphics share its memory with everything else: a model can count on about this share.
APPLE_SHARE = 0.6
# The game's languages: every disc's speech is in one of them.
GAME_LANGUAGES = ("en", "fr", "de", "es", "it", "ja")


def load(path: Path = CATALOGUE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    check(data)
    return data


def check(data: dict) -> None:
    """Refuses a catalogue a preset of which names a model it does not have, or with two models of
    one id: the dock would show an empty row."""
    ids = [m["id"] for m in data["models"]]
    if len(set(ids)) != len(ids):
        raise ValueError("models.json lists a model twice")
    for preset in data["presets"]:
        missing = [m for m in preset["models"] if m not in ids]
        if missing:
            raise ValueError(f"preset {preset['id']} names models models.json lacks: {', '.join(missing)}")
    for model in data["models"]:
        if model["kind"] not in ("python", "server"):
            raise ValueError(f"{model['id']}: kind {model['kind']!r} is neither python nor server")
        if model["kind"] == "python" and not model.get("packages"):
            raise ValueError(f"{model['id']}: a python model needs its packages")
        if model["kind"] == "server" and not model.get("server", {}).get("url"):
            raise ValueError(f"{model['id']}: a server model needs its server's address")


def model(data: dict, ident: str) -> dict:
    for item in data["models"]:
        if item["id"] == ident:
            return item
    raise KeyError(f"no voice model {ident!r}")


def preset_for(data: dict, hw: dict) -> dict:
    """The preset of a computer (hardware.detect): Apple Silicon by its chip, else the strongest
    preset whose memory the best card has, else the one without a card."""
    best = hw.get("best")
    presets = {p["id"]: p for p in data["presets"]}
    if best is not None and best.get("backend") == "mps":
        return presets["apple"]
    vram = best.get("vram_gb", 0.0) if best is not None else 0.0
    fitting = [p for p in data["presets"] if p["id"] not in ("apple", "cpu") and p["vram_gb"] <= vram]
    return max(fitting, key=lambda p: p["vram_gb"]) if fitting else presets["cpu"]


def fits(item: dict, hw: dict) -> tuple[bool, str]:
    """Whether a model runs on this computer, and if not, why (a sentence for the dock)."""
    if item["kind"] == "server":
        return True, ""
    best = hw.get("best")
    device = hw.get("device", "cpu")
    if device not in item["devices"]:
        if "cpu" in item["devices"]:
            return True, ""
        return False, f"{item['name']} needs an NVIDIA graphics card."
    if device == "cpu" or best is None:
        return True, ""
    memory = best.get("vram_gb", 0.0) * (APPLE_SHARE if best.get("unified") else 1.0)
    if memory + 1e-9 < item["vram_gb"] + (0 if best.get("unified") else HEADROOM_GB) and item["vram_gb"] > 0:
        slow = " It would run on the processor, slowly." if "cpu" in item["devices"] else ""
        return False, (f"{item['name']} needs about {item['vram_gb']:g} GB of graphics memory; "
                       f"this computer's card has {best.get('vram_gb', 0.0):g} GB.{slow}")
    return True, ""


def device_for(item: dict, hw: dict) -> str:
    """Where a model runs here: the card when the model can use it and fits, else the processor; a
    server model runs wherever its server does."""
    if item["kind"] == "server":
        return "server"
    device = hw.get("device", "cpu")
    if device in item.get("devices", []) and fits(item, hw)[0]:
        return device
    return "cpu"


def missing_languages(item: dict, languages: list[str] | tuple[str, ...]) -> list[str]:
    return [lang for lang in languages if lang not in item["languages"]]


def ranking(data: dict, hw: dict, languages: list[str] | tuple[str, ...] = ("en",)) -> list[dict]:
    """Every model, in the order the dock lists them for this computer: its preset's models first,
    best first, then the rest by quality. Each says whether it fits, why not, where it runs, which
    of LANGUAGES it lacks, and whether it is the one the dock starts on (the first that fits, is
    installed with a click and speaks every one of LANGUAGES, else the first that fits)."""
    preset = preset_for(data, hw)
    order = list(preset["models"]) + sorted(
        (m["id"] for m in data["models"] if m["id"] not in preset["models"]),
        key=lambda ident: -model(data, ident)["quality"])
    rows = []
    for ident in order:
        item = model(data, ident)
        ok, why = fits(item, hw)
        rows.append({"id": ident, "name": item["name"], "kind": item["kind"], "fits": ok, "why": why,
                     "device": device_for(item, hw), "missing": missing_languages(item, languages),
                     "in_preset": ident in preset["models"], "license": item["license"],
                     "commercial": item["commercial"], "quality": item["quality"], "vram_gb": item["vram_gb"],
                     "download_gb": item["download_gb"], "notes": item["notes"], "recommended": False})
    pick = next((r for r in rows if r["in_preset"] and r["fits"] and r["kind"] == "python" and not r["missing"]),
                None) or next((r for r in rows if r["fits"] and r["kind"] == "python"), None)
    if pick is not None:
        pick["recommended"] = True
    return rows
