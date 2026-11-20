"""Where the voice work keeps its files, and the Python environment each model runs in.

Everything lives in one folder the player can delete, `voices` in the launcher's data folder:

    voices/
      uv/                  uv, when the computer has none (fetched once)
      python/              the Python builds uv fetched for the environments
      envs/<model>/        one environment a model: its packages and openrac-env.json
      envs/listen/         the one that listens to the disc's speech (words and voices)
      models/              the models' weights (Hugging Face's cache)
      <game>-<version>/    a disc's speech, characters and the lines made (workspace.py)

A model gets an environment of its own because the models pin different versions of the same
packages (Chatterbox needs torch 2.6, VoxCPM a newer one). uv makes them: it fetches the Python a
model needs, picks the PyTorch build for the graphics card (--torch-backend), and keeps one copy
of a package that several environments share.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

# The uv fetched when the computer has none: a release whose installs this code was tested with.
UV_VERSION = "0.11.32"
UV_RELEASES = "https://github.com/astral-sh/uv/releases/download"
# The packages of the environment that listens to the disc: words (Whisper, through
# CTranslate2) and voices (an ECAPA speaker model, through SpeechBrain).
LISTEN = "listen"
LISTEN_PACKAGES = ["faster-whisper==1.2.1", "speechbrain==1.1.2", "soundfile==0.14.0"]
LISTEN_PYTHON = "3.11"
# Every model's environment also gets these: the worker reads and writes sound with them.
WORKER_PACKAGES = ["soundfile==0.14.0", "numpy"]
ENV_FILE = "openrac-env.json"
# Room a new environment needs besides the model's weights: PyTorch and its friends.
ENV_GB = {"cuda": 6.0, "rocm": 8.0, "mps": 1.5, "cpu": 1.5}


def python_in(env: Path, system: str = sys.platform) -> Path:
    """An environment's Python: Scripts\\python.exe on Windows, bin/python elsewhere."""
    return env / "Scripts" / "python.exe" if system == "win32" else env / "bin" / "python"


def uv_target(system: str = sys.platform, machine: str | None = None) -> str:
    """uv's name for this computer in its release files."""
    machine = (machine or platform.machine()).lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    if system == "win32":
        return f"{arch}-pc-windows-msvc"
    if system == "darwin":
        return f"{arch}-apple-darwin"
    return f"{arch}-unknown-linux-gnu"


def uv_archive(system: str = sys.platform, machine: str | None = None) -> str:
    target = uv_target(system, machine)
    return f"uv-{target}.zip" if system == "win32" else f"uv-{target}.tar.gz"


def torch_backend(hw: dict, system: str = sys.platform) -> str | None:
    """--torch-backend for a model's environment: uv finds the CUDA or ROCm build that suits the
    card's driver ("auto"); without a card, the small build without either ("cpu"). On a Mac
    PyPI's own build is the right one (it has Metal), so none is given."""
    if system == "darwin":
        return None
    return "auto" if hw.get("device") == "cuda" else "cpu"


def package_name(requirement: str) -> str:
    """A requirement's package, as pip compares them: "NumPy<2" and "numpy==2.1" are both numpy."""
    return re.split(r"[\s<>=!~;\[]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")


class Home:
    """The voices folder."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def envs(self) -> Path:
        return self.root / "envs"

    @property
    def weights(self) -> Path:
        return self.root / "models"

    def env(self, ident: str) -> Path:
        if not ident or any(c in ident for c in "/\\:") or ident.startswith("."):
            raise ValueError(f"{ident!r} is not an environment's name")
        return self.envs / ident

    def env_state(self, ident: str) -> dict | None:
        """What an environment was made with, or None when it is missing or half made."""
        env = self.env(ident)
        try:
            state = json.loads((env / ENV_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(state, dict) or not state.get("complete") or not python_in(env).exists():
            return None
        return state

    def worker_env(self) -> dict[str, str]:
        """The variables a worker runs with: the weights in this folder, no telemetry, UTF-8."""
        env = dict(os.environ)
        env.update({"HF_HOME": str(self.weights), "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONUTF8": "1",
                    "PYTHONIOENCODING": "utf-8", "TOKENIZERS_PARALLELISM": "false",
                    "PYTORCH_ENABLE_MPS_FALLBACK": "1", "DO_NOT_TRACK": "1"})
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        return env

    def uv_env(self) -> dict[str, str]:
        """The variables uv runs with: its Pythons and its cache in this folder."""
        env = dict(os.environ)
        env.update({"UV_PYTHON_INSTALL_DIR": str(self.root / "python"), "UV_CACHE_DIR": str(self.root / "cache"),
                    "UV_NO_PROGRESS": "1", "UV_PYTHON_DOWNLOADS": "automatic"})
        for name in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX", "UV_SYSTEM_PYTHON"):
            env.pop(name, None)
        return env

    # ---- uv -------------------------------------------------------------------------------------

    def own_uv(self, system: str = sys.platform) -> Path:
        return self.root / "uv" / ("uv.exe" if system == "win32" else "uv")

    def find_uv(self) -> Path | None:
        """uv on the PATH, else the one fetched before."""
        found = shutil.which("uv")
        if found:
            return Path(found)
        own = self.own_uv()
        return own if own.is_file() else None

    def fetch_uv(self, report=lambda text: None, opener=urllib.request.urlopen) -> Path:
        """Fetches uv's release for this computer, checks it against its published SHA-256 and
        keeps its program in voices/uv."""
        name = uv_archive()
        base = f"{UV_RELEASES}/{UV_VERSION}/{name}"
        report(f"Fetching uv {UV_VERSION}")
        with opener(base + ".sha256", timeout=60) as answer:
            expected = answer.read().decode("ascii", "replace").split()[0].strip().lower()
        with opener(base, timeout=300) as answer:
            data = answer.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError(f"uv's download does not match its checksum ({name})")
        program = "uv.exe" if sys.platform == "win32" else "uv"
        found = None
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for entry in archive.namelist():
                    if entry.rsplit("/", 1)[-1] == program:
                        found = archive.read(entry)
        else:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                for member in archive.getmembers():
                    if member.isfile() and member.name.rsplit("/", 1)[-1] == program:
                        handle = archive.extractfile(member)
                        found = handle.read() if handle else None
        if not found:
            raise RuntimeError(f"uv's download has no {program}")
        target = self.own_uv()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".part")
        temporary.write_bytes(found)
        temporary.chmod(0o755)
        os.replace(temporary, target)
        return target

    def uv(self, report=lambda text: None) -> Path:
        return self.find_uv() or self.fetch_uv(report)

    # ---- environments ---------------------------------------------------------------------------

    def install_commands(self, uv: Path, ident: str, python: str, packages: list[str], backend: str | None,
                         system: str = sys.platform) -> list[list[str]]:
        """The uv commands that make an environment from nothing."""
        env = self.env(ident)
        install = [str(uv), "pip", "install", "--python", str(python_in(env, system))]
        if backend:
            install += ["--torch-backend", backend]
        pinned = {package_name(p) for p in packages}
        return [[str(uv), "venv", "--quiet", "--python", python, "--seed", "--no-project", str(env)],
                install + list(packages) + [p for p in WORKER_PACKAGES if package_name(p) not in pinned]]

    def free_gb(self) -> float:
        probe = self.root
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        return shutil.disk_usage(probe).free / 1024 ** 3

    def install(self, ident: str, python: str, packages: list[str], backend: str | None, need_gb: float,
                report=lambda text: None, runner=None) -> dict:
        """Makes environment IDENT afresh (a half-made one is thrown away first) and returns its
        state. REPORT gets each line uv prints; RUNNER runs one command (tests replace it)."""
        free = self.free_gb()
        if free < need_gb:
            raise RuntimeError(f"Not enough free disk space: this needs about {need_gb:.0f} GB and "
                               f"{free:.1f} GB is free where the launcher keeps its data.")
        env = self.env(ident)
        if env.exists():
            shutil.rmtree(env)
        self.envs.mkdir(parents=True, exist_ok=True)
        uv = self.uv(report)
        runner = runner or self.run
        for command in self.install_commands(uv, ident, python, packages, backend):
            runner(command, report)
        state = {"complete": True, "python": python, "packages": packages, "torch_backend": backend,
                 "uv": UV_VERSION if uv == self.own_uv() else "system", "made": int(time.time())}
        (env / ENV_FILE).write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
        return state

    def run(self, command: list[str], report) -> None:
        """Runs a uv command, handing each line it prints to REPORT; a failure keeps its last lines."""
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", env=self.uv_env(),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tail: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if line:
                report(line)
                tail = (tail + [line])[-12:]
        if process.wait() != 0:
            raise RuntimeError("Installing failed:\n" + "\n".join(tail))

    def remove(self, ident: str) -> bool:
        env = self.env(ident)
        if not env.exists():
            return False
        # A folder another program still holds (Windows) is left for the next try.
        scratch = Path(tempfile.mkdtemp(prefix=".removing-", dir=self.envs))
        os.replace(env, scratch / ident)
        shutil.rmtree(scratch, ignore_errors=True)
        return True
