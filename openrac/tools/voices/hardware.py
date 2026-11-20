"""What the player's computer has for a voice model: its graphics cards and their memory, the
processor and the memory, so the Voices dock can suggest a preset (models.preset_for).

Each probe runs a program the system already has and reads its answer; one that is missing or
fails adds nothing. The readers of those answers are plain functions, tested on answers from each
system (test_voices.py), so the result is the same on Windows, Linux and macOS.

- NVIDIA (Windows, Linux): nvidia-smi, which comes with the driver.
- AMD (Linux): the amdgpu driver's files under /sys/class/drm. PyTorch runs AMD cards on Linux
  only (ROCm), so an AMD card on Windows is listed but not used.
- Apple Silicon: sysctl. The Mac's graphics share its memory (unified memory).
- Anything else (Intel graphics, AMD on Windows): listed, and the model runs on the processor.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

GIB = 1024 ** 3
# How long one probe may take before it counts as missing.
PROBE_SECONDS = 10


def run(args: list[str]) -> str | None:
    """What ARGS prints, or None when the program is missing, fails or hangs."""
    program = shutil.which(args[0])
    if program is None:
        return None
    try:
        done = subprocess.run([program, *args[1:]], capture_output=True, text=True, timeout=PROBE_SECONDS,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def nvidia_cards(answer: str | None) -> list[dict]:
    """nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits:
    one line a card, its memory in MiB."""
    cards = []
    for line in (answer or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            mib = float(parts[1])
        except ValueError:
            continue
        cards.append({"vendor": "nvidia", "name": parts[0], "vram_gb": round(mib / 1024, 1),
                      "driver": parts[2] if len(parts) > 2 else "", "backend": "cuda"})
    return cards


def amd_cards(drm: Path) -> list[dict]:
    """The AMD cards the amdgpu driver lists under DRM (/sys/class/drm): vendor 0x1002 and the
    driver's mem_info_vram_total. A card with less than 1 GB of its own is integrated graphics."""
    cards = []
    if not drm.is_dir():
        return cards
    # card0, card1...: the cards themselves, not their outputs (card0-DP-1).
    for card in sorted(p for p in drm.glob("card*") if re.fullmatch(r"card\d+", p.name)):
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text().strip()
            total = int((device / "mem_info_vram_total").read_text().strip())
        except (OSError, ValueError):
            continue
        if vendor != "0x1002" or total < GIB:
            continue
        name = ""
        try:
            name = (device / "product_name").read_text().strip()
        except OSError:
            pass
        cards.append({"vendor": "amd", "name": name or f"AMD graphics ({card.name})",
                      "vram_gb": round(total / GIB, 1), "driver": "amdgpu", "backend": "rocm"})
    return cards


def windows_cards(answer: str | None) -> list[dict]:
    """PowerShell's Win32_VideoController as JSON (one object, or a list): every card's name. Its
    AdapterRAM stops at 4 GB, so it is not used for a preset; NVIDIA cards come from nvidia-smi."""
    try:
        found = json.loads(answer or "null")
    except ValueError:
        return []
    if isinstance(found, dict):
        found = [found]
    cards = []
    for item in found if isinstance(found, list) else []:
        name = str(item.get("Name") or "").strip() if isinstance(item, dict) else ""
        if not name or "nvidia" in name.lower() or "microsoft basic" in name.lower():
            continue
        vendor = "amd" if ("amd" in name.lower() or "radeon" in name.lower()) else \
                 "intel" if "intel" in name.lower() else "other"
        cards.append({"vendor": vendor, "name": name, "vram_gb": 0.0, "driver": "", "backend": "none"})
    return cards


def apple_chip(machine: str, brand: str | None, memory: str | None) -> list[dict]:
    """An Apple Silicon Mac's graphics: its chip's name (sysctl machdep.cpu.brand_string) and the
    memory it shares with the processor (hw.memsize). An Intel Mac has none PyTorch uses."""
    if machine != "arm64":
        return []
    try:
        total = int((memory or "").strip())
    except ValueError:
        total = 0
    return [{"vendor": "apple", "name": (brand or "").strip() or "Apple Silicon",
             "vram_gb": round(total / GIB, 1), "driver": "metal", "backend": "mps", "unified": True}]


def memory_gb() -> float:
    """The computer's memory, or 0 when it cannot be told."""
    try:
        if sys.platform == "win32":
            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("free", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                            ("page_free", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return round(status.total / GIB, 1)
            return 0.0
        if sys.platform == "darwin":
            answer = run(["sysctl", "-n", "hw.memsize"])
            return round(int(answer.strip()) / GIB, 1) if answer else 0.0
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GIB, 1)
    except (OSError, ValueError, AttributeError):
        return 0.0


def best_card(cards: list[dict]) -> dict | None:
    """The card a model runs on: the one PyTorch can use with the most memory."""
    usable = [c for c in cards if c.get("backend") in ("cuda", "rocm", "mps")]
    return max(usable, key=lambda c: c.get("vram_gb", 0.0)) if usable else None


def device_of(card: dict | None) -> str:
    """PyTorch's name for where a model runs: ROCm builds call AMD cards "cuda" too."""
    if card is None:
        return "cpu"
    return "mps" if card["backend"] == "mps" else "cuda"


def detect() -> dict:
    """This computer, as the Voices dock shows it."""
    cards: list[dict] = []
    if sys.platform == "darwin":
        cards += apple_chip(platform.machine(), run(["sysctl", "-n", "machdep.cpu.brand_string"]),
                            run(["sysctl", "-n", "hw.memsize"]))
    else:
        cards += nvidia_cards(run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                                   "--format=csv,noheader,nounits"]))
        if sys.platform.startswith("linux"):
            cards += amd_cards(Path("/sys/class/drm"))
        elif sys.platform == "win32":
            cards += windows_cards(run(["powershell", "-NoProfile", "-Command",
                                        "Get-CimInstance Win32_VideoController | Select-Object Name | ConvertTo-Json"]))
    best = best_card(cards)
    return {"os": sys.platform, "machine": platform.machine(), "cpu_threads": os.cpu_count() or 1,
            "ram_gb": memory_gb(), "cards": cards, "best": best, "device": device_of(best)}
