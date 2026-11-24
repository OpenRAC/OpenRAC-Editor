---
name: openrac-level
description: Make or change a Ratchet & Clank level, planet or cutscene in OpenRAC Editor and check it in OpenRAC's game. Use when asked to place mobys, write cutscenes, make a new planet, or see an edit in the game.
---

# A level in OpenRAC Editor, seen in the game

Two MCP servers do the work: `openrac-editor` (the open editor: the level, its mobys and
cutscenes, Check, pack, new planets) and `openrac-game` (the port, a tick at a time: pictures,
mobys, the hero). Read the resource `openrac://guide/ai` first; the editor's prompts
(`new_planet`, `cutscene_dialogue`, `place_enemies`, `check_and_fix`, `look_in_game`) are the
steps of whole tasks.

## How a task goes

1. `project_info`: which level and disc, whether Check and Play can run, the launcher's folders.
2. Look before changing: `moby_list` near the place, `class_search`, `ground_at`; `view_set` and
   `screenshot` to see it in the editor.
3. Change with the level's tools, each an undo step: `moby_add` with `on_ground`, `moby_set`,
   `moby_remove`; `cutscene_write` for a whole cutscene, `cutscene_add` to grow one;
   `cutscene_check` and `cutscene_frame` at the start of each shot. After placing or moving
   things, `level_place` with their `names` and `move` (`job_wait`): it sets each one down on its
   whole underside, off slopes and out of walls. Never trust a single ground point for a model.
4. `scene_save`, then `level_check` and `job_wait`. Fix each problem it lists (node, why) and
   check again until none is left.
5. `level_pack` (`job_wait`), then in the game: `game_start` with the level and `mods` set to the
   packed folder (`project_info` folders.packed; `game`, `data`, `levels` from its folders when the
   game server has no defaults). `game_hero_place` near each change, `game_step`, `game_shot`,
   `game_mobys`; `game_scene` with a cutscene's name plays it through its trigger.
6. Compare what you see with what was meant, go back to 3, and say what you made with pictures.
7. A new planet: `planet_set` (plain ASCII, at most 40 characters), `planet_add` (`job_wait`).

## Rules

- Coordinates are the game's: x and y across, z up, about a metre a unit; headings in degrees about
  z, 0 along +x. Cutscene times are ticks, 60 a second.
- Never a negative scale (the game cannot draw a mirrored object).
- A class must be one the level loads, or come in `import` mode (with its own logic) or `look`
  mode (scenery); `class_search` says which.
- Subtitles in every language of the disc (`project_info` disc; Europe: en, fr, de, es, it), in
  ASCII without `& * = [ ] { } ~`.
- Edits change what is placed and where, never the game's code.
- Name the discs Europe, Europe v1.00, USA and Japan.
