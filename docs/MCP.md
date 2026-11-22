# MCP server

OpenRAC Editor has a built-in [Model Context Protocol](https://modelcontextprotocol.io) server, so
an AI assistant can read and edit the open level the way you do: find objects, change them, take
pictures of the 3D view and undo what it did. With the level's own tools it can also place mobys
on the ground, write cutscenes, check and pack the level and add it as a new planet; with
OpenRAC's game server (OpenRAC's `docs/port/GAME_MCP.md`) it can then look at the result in the
game. Start with the guide it serves, `openrac://guide/ai` ([AI_GUIDE.md](AI_GUIDE.md)).

## Connect

The server starts with the editor and listens on `http://127.0.0.1:7420/mcp`, on this computer
only. It needs a token, which the editor writes, with the address, to `mcp.json` in its settings
folder:

| System | File |
|---|---|
| Linux | `~/.config/openrac_editor/mcp.json` |
| Windows | `%APPDATA%\openrac_editor\mcp.json` |
| macOS | `~/Library/Application Support/openrac_editor/mcp.json` |

Add it to your client as a Streamable HTTP server with the header
`Authorization: Bearer <token>`. For Claude Code:

```sh
claude mcp add --transport http openrac-editor http://127.0.0.1:7420/mcp \
  --header "Authorization: Bearer <token>"
```

**Editor Settings > OpenRAC > MCP** turns it off, or changes the port or the token. Without a
token the server does not start. It refuses requests from web pages: a request with an `Origin`
header is answered only when that origin is this computer (`http://127.0.0.1`, `http://localhost`
or `http://[::1]`, with any port). `mcp.json` is readable by your user only.

## Tools

| Tool | What it does |
|---|---|
| `editor_state` | Open scenes, the edited scene, the selection, the last undo step |
| `scene_open`, `scene_save` | Open or switch to a level, save it |
| `tree` | List nodes, filtered by class, name, group or metadata |
| `node_get`, `node_set` | Read and change properties and metadata |
| `node_create`, `node_delete`, `node_reparent`, `node_duplicate` | Change the scene tree |
| `node_call` | Call any method of a node |
| `class_info` | Properties, methods, signals and constants of a class or node |
| `undo`, `redo` | The edited scene's history |
| `select` | Select nodes, and move the 3D view to them |
| `view_get`, `view_set` | Read or move a 3D view's camera |
| `screenshot` | A PNG of a 3D view or the whole window |
| `eval` | Run an expression on a node |
| `run_gdscript` | Run GDScript in the editor (`scene` is the level, `editor` is EditorInterface) |
| `log` | The editor's output and errors |
| `files`, `resource_get` | Browse the project and read resources |
| `level_find` | Find mobys, ties, shrubs, volumes and more by class, spawn id or place |
| `batch` | Several calls in one, in order, stopping at the first error |
| `job_status`, `job_wait`, `job_cancel` | Follow long work (see Jobs) |

The level's tools, when a level project is open (its plugin adds them, in the game's coordinates:
x and y across, z up, headings in degrees):

| Tool | What it does |
|---|---|
| `project_info` | The level, its disc, the planet, the launcher's folders, whether Check and Play can run, the disc's other levels |
| `level_check`, `level_pack`, `level_play` | Check level, pack, Play level (jobs); Check answers each refused edit as `{node, path, why}` |
| `planet_get`, `planet_set`, `planet_add` | The planet's name in the ship's menu, and adding the level as a new planet (a job) |
| `moby_states_refresh` | How the game starts the level's mobys, again after edits (a job) |
| `class_list`, `class_search` | Classes this level has; classes in every level of the disc, and how each would come (own, import, look) |
| `moby_list`, `moby_add`, `moby_set`, `moby_remove` | Mobys: list near a point, add on the ground, move, give a behaviour or pvars, delete |
| `behaviour_list` | The behaviour presets and their numbers |
| `ground_at` | The floor under a point, from the level's collision |
| `cutscene_list`, `cutscene_get`, `cutscene_remove` | The level's cutscenes as `.cutscene` documents ([CUTSCENE_FORMAT.md](CUTSCENE_FORMAT.md)) |
| `cutscene_write`, `cutscene_add` | Write a whole cutscene, or add shots, actors, lines, sounds and events to one |
| `cutscene_check` | What the game would not take or would skip |
| `cutscene_frame` | A picture of a moment through the cutscene's camera (a job; needs the editor's window) |
| `terrain_info`, `terrain_textures` | The terrain's numbers next to Pokitaru's, with advice; the ground pictures to paint with ([TERRAIN.md](TERRAIN.md)) |
| `terrain_sculpt`, `terrain_paint`, `terrain_uv` | Brushes on the terrain mesh: raise, lower, smooth, roughen, flatten; a picture per face; box-mapped pictures (jobs) |
| `terrain_rock_add`, `terrain_rocks_clear`, `terrain_build` | Rocks with overhangs from a recipe, built into the terrain node `Rocks` (a job) |
| `terrain_bake_light`, `terrain_undo` | Sun, shadows and sky light baked into the ground's colours (a job); back to before the last terrain change |

Every change made with `node_set`, `node_create`, `node_delete`, `node_reparent` and
`node_duplicate` is one step in the editor's history, so you see it and can undo it.
`node_call` and `run_gdscript` are not undoable. The level's tools are undoable too, each one
step (`undo` takes `steps`). The terrain tools change `terrain.glb` itself: each keeps a backup
first, and `terrain_undo` takes the last one back.

## Jobs

Work that takes a while (Check, pack, Play, a new planet, a picture of a cutscene, a class the
editor is still importing) answers at once with `{"job": "job-1", "state": "running"}`.
`job_wait` answers when the job ends, or after `seconds` (then call it again), with its result and
the output lines it printed; `job_status` answers at once, and `job_cancel` stops it.

## Resources and prompts

The server also serves the editor's guides as resources (`openrac://guide/ai`,
`openrac://docs/cutscene-format`, `openrac://docs/level-editor` and the others in `docs/`), every
class name (`openrac://classes/names`) and the open level in short (`openrac://level/summary`). Its
prompts are whole tasks, step by step: `new_planet`, `cutscene_dialogue`, `place_enemies`,
`check_and_fix` and `look_in_game`.

## Values

Values are JSON. Vectors and colours are arrays (`[x, y, z]`, `[r, g, b, a]`), a `Transform3D` is
`{"basis": [[x], [y], [z]], "origin": [x, y, z]}`, a resource is `{"resource": "res://..."}` and a
node is `{"node": "Game/Mobys/Vendor_0019"}`. Metadata is set as `metadata/<name>`.
