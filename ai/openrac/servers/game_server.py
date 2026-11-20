#!/usr/bin/env python3
"""OpenRAC's game MCP server (OpenRAC's tools/game_mcp.py, docs/port/GAME_MCP.md), found through
OPENRAC_ROOT (an OpenRAC checkout with a built port). OPENRAC_GAME, OPENRAC_DATA and
OPENRAC_LEVELS give game_start its defaults; without them it takes them as arguments (OpenRAC
Editor's project_info lists the launcher's folders)."""

import os
import runpy
import sys
from pathlib import Path

root = os.environ.get("OPENRAC_ROOT", "")
server = Path(root) / "tools" / "game_mcp.py"
if not root or not server.is_file():
    sys.stderr.write("openrac-game: set OPENRAC_ROOT to an OpenRAC checkout (with tools/game_mcp.py).\n")
    sys.exit(1)
sys.argv = [str(server)]
runpy.run_path(str(server), run_name="__main__")
