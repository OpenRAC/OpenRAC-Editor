# OpenRAC Editor for AI assistants

This is what an AI assistant needs to know to edit Ratchet & Clank levels with OpenRAC Editor's MCP
server (docs/MCP.md), and to see the result in the game with OpenRAC's game server
(OpenRAC's docs/port/GAME_MCP.md). The server serves this file as `openrac://guide/ai`.

## The world

- **A level project** is one level of the disc, numbered from 0 as the game does (Veldin 0,
  Novalis 1, Aridia 2, Kerwan 3, Eudora 4, Rilgar 5, Blarg 6, Umbris 7, Batalia 8, Gaspar 9,
  Orxon 10, Pokitaru 11, Hoven 12, Gemlik 13, Oltanis 14, Quartu 15, Kalebo III 16, Drek's fleet
  17, Veldin again at the end 18). `project_info` says which, and lists the other levels' projects.
- **The game's coordinates**: x and y across, z up, in game units (about a metre). Every OpenRAC
  tool (`moby_*`, `ground_at`, `cutscene_*`) speaks them. A heading (`yaw`) is degrees about z,
  0 looking along +x. The Godot tools (`node_*`, `tree`) speak the editor's own space, where the
  level sits under the `Game` node that turns it; prefer the OpenRAC tools.
- **Mobys** are the game's objects with logic: Ratchet, enemies, vendors, crates, bolts, doors.
  Each is of a **class** (a number; `class_list` and `class_search` give names). A level loads only
  its own classes; a class of another level of the same disc comes with its own logic (`import`),
  and anything else can be static scenery (`look`).
- **Spawn numbers** (`spawn_id`) name a moby for the game, its cutscenes and its missions. Mobys
  the editor adds get one when they are packed, kept while the node keeps its name.
- **Ties and shrubs** are the level's scenery; **collision** is the level's floors and walls
  (`ground_at` reads it).

## How a task goes

1. `project_info`: the level, its disc, whether Check and Play can run.
2. Read before changing: `moby_list` (near a point), `class_search`, `ground_at`,
   `screenshot` after `view_set` to look.
3. Change with the OpenRAC tools: `moby_add` (with `on_ground`), `moby_set`, `moby_remove`,
   `cutscene_write` and `cutscene_add`. Every change is an undo step (`undo` with `steps`).
4. `scene_save`, then `level_check` and `job_wait`: each refused edit comes with the node's path
   and why. Fix and check again until none is left.
5. To play it: `level_play` (the game starts on the level), or `planet_set` then `planet_add` for a
   new planet the ship can fly to.
6. To see it in the game, use the game server: `game_start` on the level with the packed folder,
   `game_step`, `game_shot`, `game_mobys`, `game_scene`. Compare with what was meant and go back to 3.

Long work answers with a job id; `job_wait` answers when it ends (or after the seconds asked, then
call it again).

## Cutscenes

A cutscene is a `.cutscene` document (`openrac://docs/cutscene-format`). Write one whole with
`cutscene_write`, or grow one with `cutscene_add`. In short:

- `length` and every `t` are **ticks, 60 a second** (a 12 second scene is 720).
- `camera.keys`: `{ "t": 0, "eye": [x, y, z], "look_at": [x, y, z] | {"actor": "vendor"}, "fov": 51,
  "cut": true, "ease": "smooth" }`. A shot is two keys: where it starts and where it ends; `cut` on
  the first key of a new shot.
- `actors`: `{ "id": "vendor", "source": {"spawn_id": 149} | {"class": 11} | {"figure": "hero"},
  "track": [{"t": 0, "position": [...], "rotation": [0, 0, yaw_radians]}], "path": {"points": [...],
  "speed": 8, "t": 120}, "anims": [{"t": 0, "seq": 0, "loop": true}] }`. A class must be one the
  level loads (`class_list`).
- `hero`: `{ "mode": "held" | "hidden" | "actor" }`; as an actor, the hero's figure is `{"figure": "hero"}`.
- `subtitles`: `{ "t": 30, "end": 150, "text": {"en": "..."}, "actor": "vendor" }`, one key per
  language of the disc (`languages`). The game's font writes ASCII without `& * = [ ] { } ~`.
- `trigger`: `{ "kind": "volume", "sphere": {"position": [...], "radius": 6}, "once": true }`, or
  `level_start`, `talk` (with `spawn_id`), `after` (another cutscene), `manual`.
- `events`: `fade`, `moby_hide`, `mission_flag`, `air`, `slow`, `play`, `travel`.

Look at a moment with `cutscene_frame` (a picture through its camera), and check with
`cutscene_check`. In the game, `game_scene` starts it without walking into its trigger.

## Rules the game sets

- Game logic stays as the original's: edits change what is placed and where, never how the game
  runs its code.
- No mirrored objects (a negative scale): the game cannot draw them.
- A planet's name is plain ASCII, at most 40 characters.
- Discs: Europe, Europe v1.00, USA and Japan; never call them PAL or NTSC to a person.
