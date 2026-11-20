#!/usr/bin/env python3
"""OpenRAC Editor's MCP server over stdio, for clients that start their servers as programs: each
JSON-RPC line on stdin goes to the running editor (its address and token from mcp.json in the
editor's settings folder, read again for each message, so a restarted editor is found) and its
answer comes back on stdout. Python's standard library only."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def info_file() -> Path:
    """mcp.json in the editor's settings folder (docs/MCP.md)."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "openrac_editor" / "mcp.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "openrac_editor" / "mcp.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "openrac_editor" / "mcp.json"


def forward(body: bytes) -> bytes | None:
    """The editor's answer to one message, or None for a notification."""
    try:
        info = json.loads(info_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConnectionError("OpenRAC Editor is not running: open a level project in it first.")
    request = urllib.request.Request(info["url"], body, {"Content-Type": "application/json",
                                                         "Accept": "application/json, text/event-stream",
                                                         "Authorization": "Bearer " + info["token"]})
    try:
        with urllib.request.urlopen(request, timeout=600) as answer:
            data = answer.read()
            return data if answer.status != 202 and data.strip() else None
    except urllib.error.HTTPError as error:
        return error.read() or None
    except (urllib.error.URLError, OSError):
        raise ConnectionError("OpenRAC Editor does not answer: is it open (Editor Settings > OpenRAC > MCP on)?")


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        try:
            answer = forward(line.encode("utf-8"))
        except ConnectionError as error:
            if not isinstance(message, dict) or message.get("id") is None:
                continue
            answer = json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                 "error": {"code": -32000, "message": str(error)}}).encode()
        if answer is not None:
            sys.stdout.write(answer.decode("utf-8").strip() + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
