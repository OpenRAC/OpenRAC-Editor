"""Talks to a worker (worker.py) running in a model's environment: starts it, sends it requests
and reads its answers, one JSON line each.

A thread reads the worker's answers into a queue, so a request can wait with a time limit on every
system (Windows cannot wait on a pipe with select). A worker that dies says so with the last lines
it printed, so the dock can show why.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

WORKER = Path(__file__).with_name("worker.py")


class WorkerError(RuntimeError):
    pass


class Worker:
    def __init__(self, python: Path | str, env: dict[str, str] | None = None, cwd: Path | None = None):
        self.process = subprocess.Popen(
            [str(python), "-u", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env,
            cwd=str(cwd) if cwd else None, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.answers: queue.Queue = queue.Queue()
        self.errors: list[str] = []
        self.next_id = 0
        self.readers = [threading.Thread(target=self._read, daemon=True),
                        threading.Thread(target=self._read_errors, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.answers.put(json.loads(line))
            except ValueError:
                self.errors.append(line)
        self.answers.put(None)

    def _read_errors(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            line = line.rstrip()
            if line:
                # Progress bars rewrite one line with carriage returns: keep the last state.
                self.errors = (self.errors + [line.split("\r")[-1]])[-40:]

    def last_words(self, count: int = 8) -> str:
        return "\n".join(self.errors[-count:])

    def request(self, op: str, timeout: float | None = None, on_event=None, **fields) -> dict:
        """Sends one request and waits for its answer; ON_EVENT gets its progress lines."""
        self.next_id += 1
        ident = self.next_id
        message = {"id": ident, "op": op, **fields}
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise WorkerError("The voice model stopped.\n" + self.last_words()) from None
        while True:
            try:
                reply = self.answers.get(timeout=timeout)
            except queue.Empty:
                self.close(kill=True)
                raise WorkerError(f"The voice model did not answer within {timeout:.0f} seconds.") from None
            if reply is None:
                raise WorkerError("The voice model stopped.\n" + self.last_words())
            if reply.get("id") != ident:
                continue
            if reply.get("event"):
                if on_event:
                    on_event(reply)
                continue
            if not reply.get("ok"):
                raise WorkerError(str(reply.get("error") or "the voice model failed"))
            return reply

    def close(self, kill: bool = False) -> None:
        if self.process.poll() is None:
            if not kill:
                try:
                    self.request("quit", timeout=10)
                except WorkerError:
                    pass
            if self.process.poll() is None:
                self.process.kill()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        # The readers end with the process's pipes; then the pipes are closed (a write still
        # waiting in one to a process that has gone is dropped).
        for reader in self.readers:
            reader.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def current_python() -> str:
    """This Python, for tests: a worker that only says hello needs nothing else."""
    return sys.executable
