"""The program that runs inside a model's environment (envs.py) and does the work that needs it:
speaking a line in a character's voice, and, in the listen environment, writing down what a line
says and telling voices apart.

It reads one JSON request a line on its standard input and answers each with one JSON line on its
standard output, so the model stays loaded between lines:

    {"id": 1, "op": "load", "backend": "chatterbox", "repo": "...", "device": "cuda"}
    {"id": 2, "op": "speak", "text": "...", "language": "en", "reference": "ref.wav",
     "reference_text": "...", "out": "take.wav", "rate": 22050, "seed": 7, "loudness": 0.12}
    {"id": 3, "op": "transcribe", "files": ["a.wav"], "language": null}
    {"id": 4, "op": "embed", "files": ["a.wav"]}
    {"id": 5, "op": "quit"}

An answer is {"id": n, "ok": true, ...} or {"id": n, "ok": false, "error": "..."}; long requests
also send {"id": n, "event": "progress", "done": k, "total": t} lines first. What the libraries
print goes to standard error, so it cannot break the answers. Only the standard library is
imported before a request needs more, so `hello` works in any Python.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
import wave

# The answers' own channel: a copy of standard output made before anything can print. Standard
# output itself then points at standard error, where the libraries' chatter goes.
_ANSWERS = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n", buffering=1)
os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
sys.stdout = sys.stderr

# Qwen3-TTS takes a language by its English name.
LANGUAGE_NAMES = {"en": "English", "fr": "French", "de": "German", "es": "Spanish", "it": "Italian",
                  "ja": "Japanese", "pt": "Portuguese", "ru": "Russian", "ko": "Korean", "zh": "Chinese",
                  "pl": "Polish", "nl": "Dutch"}
# Fish and ZONOS3 servers take these codes for a language.
ZONOS_LANGUAGES = {"en": "en_us", "fr": "fr_fr", "de": "de_de", "es": "es_es", "it": "it_it", "ja": "ja_jp",
                   "pl": "pl_pl", "pt": "pt_br", "nl": "nl_nl", "ru": "ru_ru", "ko": "ko_kr", "zh": "zh_cn"}
# Quiet below this share of full scale counts as silence when a take's ends are trimmed.
SILENCE = 0.01
# Silence kept before and after the words of a take, in seconds.
PAD_SECONDS = 0.05


def answer(message: dict) -> None:
    _ANSWERS.write(json.dumps(message, ensure_ascii=False) + "\n")
    _ANSWERS.flush()


# ---- sound files (standard library) ------------------------------------------------------------


def read_wav(path: str) -> tuple[list[float], int]:
    """A WAV file's samples (mono: channels averaged) as floats in -1..1, and its rate: any WAV
    through soundfile where the environment has it, else 16-bit PCM."""
    try:
        import soundfile
    except ImportError:
        soundfile = None
    if soundfile is not None:
        data, rate = soundfile.read(path, dtype="float32", always_2d=True)
        return data.mean(axis=1).tolist(), int(rate)
    with wave.open(path, "rb") as file:
        channels, width, rate = file.getnchannels(), file.getsampwidth(), file.getframerate()
        frames = file.readframes(file.getnframes())
    if width != 2:
        raise ValueError(f"{os.path.basename(path)}: {8 * width}-bit; the worker reads 16-bit WAV")
    import array
    data = array.array("h")
    data.frombytes(frames)
    if sys.byteorder == "big":
        data.byteswap()
    if channels == 1:
        return [s / 32768.0 for s in data], rate
    return [sum(data[i:i + channels]) / (32768.0 * channels) for i in range(0, len(data), channels)], rate


def write_wav(path: str, samples, rate: int) -> None:
    """Mono 16-bit WAV, clipped to full scale; written beside PATH and moved in place."""
    import array
    data = array.array("h", (max(-32768, min(32767, int(round(float(s) * 32767.0)))) for s in samples))
    if sys.byteorder == "big":
        data.byteswap()
    temporary = path + ".part"
    with wave.open(temporary, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(data.tobytes())
    os.replace(temporary, path)


def trim(samples: list[float], rate: int) -> list[float]:
    """SAMPLES without the silence before the first word and after the last, but for a short pad."""
    loud = [i for i, s in enumerate(samples) if abs(s) >= SILENCE]
    if not loud:
        return samples
    pad = int(PAD_SECONDS * rate)
    return samples[max(0, loud[0] - pad):min(len(samples), loud[-1] + pad + 1)]


def rms(samples) -> float:
    count = len(samples)
    return math.sqrt(sum(float(s) * float(s) for s in samples) / count) if count else 0.0


def match_loudness(samples: list[float], target: float | None) -> list[float]:
    """SAMPLES scaled to the RMS loudness TARGET, without letting a peak clip."""
    level = rms(samples)
    if not target or level <= 0:
        return samples
    peak = max(abs(s) for s in samples) or 1.0
    gain = min(target / level, 0.98 / peak)
    return [s * gain for s in samples]


def resample(samples: list[float], source: int, target: int) -> list[float]:
    """SAMPLES at rate TARGET: torchaudio's windowed sinc where the environment has it, else
    linear steps (good enough for the listen environment's 16 kHz)."""
    if source == target or not samples:
        return list(samples)
    try:
        import torch
        import torchaudio.functional as fn
        return fn.resample(torch.tensor(samples, dtype=torch.float32), source, target).tolist()
    except ImportError:
        pass
    count = max(1, int(round(len(samples) * target / source)))
    step = (len(samples) - 1) / max(1, count - 1)
    out = []
    for i in range(count):
        at = i * step
        k = int(at)
        frac = at - k
        b = samples[k + 1] if k + 1 < len(samples) else samples[k]
        out.append(samples[k] + (b - samples[k]) * frac)
    return out


# ---- the models ---------------------------------------------------------------------------------


class Backend:
    """One way of speaking: loads a model, then speaks text in the voice of a reference file."""

    rate = 24000

    def __init__(self, request: dict):
        self.repo = request.get("repo")
        self.device = request.get("device") or "cpu"
        self.url = (request.get("url") or "").rstrip("/")

    def speak(self, text: str, language: str, reference: str, reference_text: str, options: dict) -> list[float]:
        raise NotImplementedError


def seed_all(seed: int | None) -> None:
    if seed is None:
        return
    import random
    random.seed(seed)
    try:
        import numpy
        numpy.random.seed(seed % (2 ** 32))
    except ImportError:
        pass
    try:
        import torch
        torch.manual_seed(seed)
    except ImportError:
        pass


class Chatterbox(Backend):
    def __init__(self, request: dict):
        super().__init__(request)
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        self.model = ChatterboxMultilingualTTS.from_pretrained(device=self.device)
        self.rate = self.model.sr
        self.reference = None

    def speak(self, text, language, reference, reference_text, options):
        # The reference is prepared once per character: the model keeps it until another comes.
        if reference != self.reference:
            self.model.prepare_conditionals(reference, exaggeration=float(options.get("exaggeration", 0.5)))
            self.reference = reference
        wav = self.model.generate(text, language_id=language,
                                  exaggeration=float(options.get("exaggeration", 0.5)),
                                  cfg_weight=float(options.get("cfg_weight", 0.5)),
                                  temperature=float(options.get("temperature", 0.8)))
        return wav.squeeze(0).tolist()


class Qwen3(Backend):
    def __init__(self, request: dict):
        super().__init__(request)
        import torch
        from qwen_tts import Qwen3TTSModel
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        self.model = Qwen3TTSModel.from_pretrained(self.repo, device_map=self.device, dtype=dtype)
        self.prompts: dict[str, object] = {}

    def speak(self, text, language, reference, reference_text, options):
        name = LANGUAGE_NAMES.get(language)
        if name is None:
            raise ValueError(f"Qwen3-TTS does not speak {language}")
        key = reference + "\0" + (reference_text or "")
        if key not in self.prompts:
            self.prompts[key] = self.model.create_voice_clone_prompt(
                ref_audio=reference, ref_text=reference_text or None, x_vector_only_mode=not reference_text)
        wavs, rate = self.model.generate_voice_clone(text=text, language=name, voice_clone_prompt=self.prompts[key],
                                                     temperature=float(options.get("temperature", 0.9)))
        self.rate = rate
        return [float(s) for s in wavs[0]]


class VoxCPM(Backend):
    def __init__(self, request: dict):
        super().__init__(request)
        from voxcpm import VoxCPM as Model
        # torch.compile needs a compiler on Windows and Macs; it only speeds up Linux with a card.
        optimize = self.device == "cuda" and sys.platform.startswith("linux")
        self.model = Model.from_pretrained(self.repo, load_denoiser=False, optimize=optimize, device=self.device)
        self.rate = self.model.tts_model.sample_rate

    def speak(self, text, language, reference, reference_text, options):
        extra = {"prompt_wav_path": reference, "prompt_text": reference_text} if reference_text else {}
        wav = self.model.generate(text=text, reference_wav_path=reference, cfg_value=float(options.get("cfg", 2.0)),
                                  inference_timesteps=int(options.get("steps", 10)), **extra)
        return [float(s) for s in wav]


def post(url: str, body: dict, timeout: float = 600.0) -> tuple[bytes, dict]:
    import urllib.request
    data = json.dumps(body).encode("utf-8")
    import urllib.error
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.read(), dict(reply.headers)
    except urllib.error.HTTPError as exc:
        said = exc.read()[:300].decode("utf-8", "replace").strip()
        raise RuntimeError(f"the model's server refused the line ({exc.code}): {said}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"the model's server at {url.rsplit('/v', 1)[0]} does not answer ({reason}): "
                           "start it first") from None


def wav_bytes_samples(data: bytes) -> tuple[list[float], int]:
    """The samples of a WAV file a server sent."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as file:
        file.write(data)
        name = file.name
    try:
        return read_wav(name)
    finally:
        os.unlink(name)


def b64_file(path: str) -> str:
    import base64
    with open(path, "rb") as file:
        return base64.b64encode(file.read()).decode("ascii")


class Zonos3(Backend):
    """Zyphra's ZONOS3 servers' native endpoint, POST /v2/generate (its docs/api.md): a reference
    clip in base64, "format": "wav" for a 16-bit WAV."""

    rate = 44100

    def speak(self, text, language, reference, reference_text, options):
        body = {"text": text, "language": ZONOS_LANGUAGES.get(language, language), "stream": False, "format": "wav",
                "speaker_audio_base64": b64_file(reference)}
        if options.get("seed") is not None:
            body["seed"] = int(options["seed"])
        data, _ = post(self.url + "/v2/generate", body)
        samples, self.rate = wav_bytes_samples(data)
        return samples


class Fish(Backend):
    """fish-speech's API server, POST /v1/tts with JSON: the text, the reference clip in base64 with
    what it says, and "format": "wav"."""

    rate = 44100

    def speak(self, text, language, reference, reference_text, options):
        body = {"text": text, "format": "wav", "streaming": False, "normalize": True,
                "references": [{"audio": b64_file(reference), "text": reference_text or ""}]}
        if options.get("seed") is not None:
            body["seed"] = int(options["seed"])
        data, _ = post(self.url + "/v1/tts", body)
        samples, self.rate = wav_bytes_samples(data)
        return samples


class OpenAI(Backend):
    """An OpenAI-style speech server (vLLM-Omni serving Voxtral): POST /v1/audio/speech with the
    reference clip as a data URL in ref_audio, "response_format": "wav"."""

    def speak(self, text, language, reference, reference_text, options):
        body = {"model": self.repo, "input": text, "response_format": "wav", "language": language,
                "ref_audio": "data:audio/wav;base64," + b64_file(reference)}
        if reference_text:
            body["ref_text"] = reference_text
        data, _ = post(self.url + "/v1/audio/speech", body)
        samples, self.rate = wav_bytes_samples(data)
        return samples


BACKENDS = {"chatterbox": Chatterbox, "qwen3": Qwen3, "voxcpm": VoxCPM, "zonos3": Zonos3, "fish": Fish,
            "openai": OpenAI}


# ---- listening: words and voices ----------------------------------------------------------------


class Listener:
    """Whisper (faster-whisper) for the words and an ECAPA speaker model (SpeechBrain) for the
    voices, loaded when first needed."""

    def __init__(self, device: str):
        self.device = device
        self.whisper = None
        self.speaker = None

    def transcribe(self, path: str, language: str | None, model: str) -> dict:
        if self.whisper is None:
            from faster_whisper import WhisperModel
            compute = "float16" if self.device == "cuda" else "int8"
            self.whisper = WhisperModel(model, device="cuda" if self.device == "cuda" else "cpu", compute_type=compute)
        # A processor reads greedily: five beams take about three times as long for the same words here.
        segments, info = self.whisper.transcribe(path, language=language, beam_size=5 if self.device == "cuda" else 1,
                                                 vad_filter=False,
                                                 condition_on_previous_text=False)
        parts = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
                  "no_speech": round(float(s.no_speech_prob), 3)} for s in segments]
        return {"text": " ".join(p["text"] for p in parts if p["text"]).strip(), "language": info.language,
                "language_probability": round(float(info.language_probability), 3), "segments": parts}

    def embed(self, path: str) -> list[float]:
        import torch
        if self.speaker is None:
            from speechbrain.inference.speaker import EncoderClassifier
            self.speaker = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device": "cuda" if self.device == "cuda" else "cpu"},
                savedir=os.path.join(os.environ.get("HF_HOME", "."), "speechbrain-ecapa"))
        samples, rate = read_wav(path)
        samples = resample(samples, rate, 16000)
        if len(samples) < 1600:
            raise ValueError("shorter than a tenth of a second")
        with torch.no_grad():
            vector = self.speaker.encode_batch(torch.tensor([samples], dtype=torch.float32)).squeeze().tolist()
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [round(v / norm, 6) for v in vector]


# ---- the loop -----------------------------------------------------------------------------------


def versions() -> dict:
    found = {"python": sys.version.split()[0], "platform": sys.platform}
    for name in ("torch", "numpy"):
        try:
            module = __import__(name)
            found[name] = getattr(module, "__version__", "?")
        except ImportError:
            pass
    try:
        import torch
        found["cuda"] = bool(torch.cuda.is_available())
        found["mps"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        if found["cuda"]:
            found["card"] = torch.cuda.get_device_name(0)
    except ImportError:
        pass
    return found


def handle(request: dict, state: dict) -> dict:
    op = request.get("op")
    if op == "hello":
        return versions()
    if op == "load":
        kind = BACKENDS.get(request.get("backend"))
        if kind is None:
            raise ValueError(f"no backend {request.get('backend')!r}")
        started = time.monotonic()
        state["backend"] = kind(request)
        return {"seconds": round(time.monotonic() - started, 2), "rate": state["backend"].rate}
    if op == "speak":
        backend = state.get("backend")
        if backend is None:
            raise RuntimeError("no model is loaded")
        text = str(request.get("text") or "").strip()
        if not text:
            raise ValueError("there is no text to speak")
        options = dict(request.get("options") or {})
        seed_all(request.get("seed"))
        if request.get("seed") is not None:
            options.setdefault("seed", request["seed"])
        started = time.monotonic()
        samples = backend.speak(text, request.get("language") or "en", request["reference"],
                                request.get("reference_text") or "", options)
        rate = backend.rate
        if not samples or rms(samples) < 1e-4:
            raise RuntimeError("the model made silence")
        samples = trim(list(samples), rate)
        target = int(request.get("rate") or rate)
        samples = match_loudness(resample(samples, rate, target), request.get("loudness"))
        write_wav(request["out"], samples, target)
        return {"out": request["out"], "seconds": round(len(samples) / target, 3), "rate": target,
                "took": round(time.monotonic() - started, 2)}
    if op in ("transcribe", "embed"):
        listener = state.setdefault("listener", Listener(request.get("device") or "cpu"))
        files = list(request.get("files") or [])
        results = []
        for done, path in enumerate(files):
            try:
                if op == "transcribe":
                    results.append(listener.transcribe(path, request.get("language"), request.get("model") or "small"))
                else:
                    results.append({"vector": listener.embed(path)})
            except Exception as exc:  # one bad file does not stop the rest
                results.append({"error": f"{type(exc).__name__}: {exc}"})
            answer({"id": request.get("id"), "event": "progress", "done": done + 1, "total": len(files)})
        return {"results": results}
    raise ValueError(f"no request {op!r}")


def main() -> int:
    state: dict = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            answer({"id": None, "ok": False, "error": "not a JSON request"})
            continue
        if request.get("op") == "quit":
            answer({"id": request.get("id"), "ok": True})
            return 0
        try:
            result = handle(request, state)
            answer({"id": request.get("id"), "ok": True, **result})
        except Exception as exc:
            traceback.print_exc()
            answer({"id": request.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
