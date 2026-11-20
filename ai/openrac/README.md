# OpenRAC plugin for Claude Code

Makes Ratchet & Clank levels with OpenRAC Editor and shows them in OpenRAC's game:

- `openrac-editor`: the open editor's MCP server (docs/MCP.md), through `servers/editor_bridge.py`,
  which reads its address and token from the editor's `mcp.json`. Open a level project first.
- `openrac-game`: OpenRAC's game server (OpenRAC's docs/port/GAME_MCP.md), through
  `servers/game_server.py`. Set `OPENRAC_ROOT` to an OpenRAC checkout with a built port, and
  `OPENRAC_GAME`, `OPENRAC_DATA`, `OPENRAC_LEVELS` for its defaults.
- The skill `openrac-level`: how a task goes, from looking at the level to pictures in the game.

Install from this repository:

```sh
claude plugin marketplace add nickyyOO/OpenRAC-Editor
claude plugin install openrac@openrac-editor
```

Then ask, for example: "Make Kerwan a new planet called New Kerwan, with a cutscene where a vendor
greets Ratchet when he lands."
