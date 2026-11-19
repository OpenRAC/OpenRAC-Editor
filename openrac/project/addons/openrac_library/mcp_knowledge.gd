@tool
extends RefCounted
## What the editor's MCP server tells AI assistants besides its tools (docs/MCP.md): resources
## (the guides, the class names, this level) and prompts (whole tasks, step by step). plugin.gd
## makes one and calls register() and unregister().

const Library = preload("library.gd")
const Play = preload("play.gd")

## The guides, by resource name: the file in the editor's docs folder and what it is.
const DOCS: Dictionary = {
	"guide/ai": ["AI_GUIDE.md", "Start here: the world, the coordinates, how a task goes, cutscenes in short."],
	"docs/mcp": ["MCP.md", "The editor's MCP server: connecting, tools, values."],
	"docs/level-editor": ["LEVEL_EDITOR.md", "The level editor: projects, packing, Check and Play."],
	"docs/cutscene-format": ["CUTSCENE_FORMAT.md", "The .cutscene document: camera, actors, subtitles, sounds, events."],
	"docs/map-modding": ["MAP_MODDING.md", "New planets and maps."],
	"docs/level-modding": ["LEVEL_MODDING.md", "What a level edit can change."],
	"docs/behaviour-format": ["BEHAVIOUR_FORMAT.md", "Behaviour presets a moby can be given."],
}

var server: Object
var uris: PackedStringArray = []
var prompts: PackedStringArray = []
var tools: RefCounted


func register(on: Object, level_tools: RefCounted) -> void:
	server = on
	tools = level_tools
	var docs: String = docs_dir()

	for key in DOCS:
		var file: String = docs.path_join(DOCS[key][0])

		if FileAccess.file_exists(file):
			resource("openrac://" + key, DOCS[key][0], DOCS[key][1], "text/markdown", file)

	resource("openrac://classes/names", "Class names", "Every moby, tie and shrub class of Ratchet & Clank with its name "
		+ "(kind, class, name; tab separated).", "text/tab-separated-values", "res://addons/openrac_library/names/rac1.tsv")
	resource("openrac://level/summary", "This level", "The open level now: project, planet, classes placed, cutscenes.",
		"application/json", level_summary)

	prompt("new_planet", "Make a new planet from this level: change it, name it, check it, add it, fly there and look.",
		[arg("name", "The planet's name.", false), arg("idea", "What it should be like.", false)], new_planet)
	prompt("cutscene_dialogue", "Make a cutscene where characters talk: actors, shots that follow the talk, subtitles, a trigger, checked and seen in the game.",
		[arg("story", "Who says what, and where.", true)], cutscene_dialogue)
	prompt("place_enemies", "Place enemies or other mobys somewhere in the level, on the ground, and check they wake up in the game.",
		[arg("what", "Which mobys and how many.", true), arg("where", "Where (a place, a moby, a point).", true)], place_enemies)
	prompt("check_and_fix", "Check the level and fix every edit the game cannot take, until none is left.", [], check_and_fix)
	prompt("look_in_game", "Start the game on this level with the edits and look at them: pictures, mobys, Ratchet.",
		[arg("what", "What to look at.", false)], look_in_game)


func unregister() -> void:
	if server == null:
		return

	for uri in uris:
		server.unregister_resource(uri)

	for name in prompts:
		server.unregister_prompt(name)

	uris.clear()
	prompts.clear()
	server = null


## The editor's docs folder: beside its tools (<editor>/openrac/tools, <editor>/docs).
static func docs_dir() -> String:
	var tools: String = Play.user_arg("--openrac-tools")

	if not tools.is_empty():
		return tools.simplify_path().get_base_dir().get_base_dir().path_join("docs")

	return ""


func resource(uri: String, name: String, description: String, mime: String, source: Variant) -> void:
	server.register_resource(uri, name, description, mime, source)
	uris.append(uri)


func prompt(name: String, description: String, arguments: Array, callable: Callable) -> void:
	server.register_prompt(name, description, arguments, callable)
	prompts.append(name)


static func arg(name: String, description: String, required: bool) -> Dictionary:
	return {"name": name, "description": description, "required": required}


func level_summary() -> Variant:
	var info: Variant = tools.project_info({})
	var classes: Variant = tools.class_list({})
	var placed: Array = []

	if classes is Dictionary and classes.has("classes"):
		placed = classes["classes"].filter(func(c: Dictionary) -> bool: return int(c["placed"]) > 0)

	return {"project": info, "classes_placed": placed,
		"cutscenes": EditorInterface.get_edited_scene_root().find_children("*", "Cutscene", true, false).map(
			func(n: Node) -> String: return String(n.name)) if EditorInterface.get_edited_scene_root() != null else []}


func new_planet(args: Dictionary) -> String:
	return """Make a new planet from this OpenRAC Editor level%s%s.

Read openrac://guide/ai first. Then:
1. project_info: which level this is, its disc, and whether Check, Play and planets can run.
2. Look at the level: moby_list, class_list, screenshot after view_set at the places you change.
3. Make it the planet: move, add (moby_add with on_ground) and remove mobys; give some a behaviour
   (moby_set); add cutscenes (cutscene_write) for an arrival or a meeting. Keep the hero's start
   (the Ratchet moby) somewhere he can stand.
4. scene_save, then level_check and job_wait; fix each problem it lists and check again.
5. planet_set with the name, then planet_add and job_wait.
6. With the game's MCP server: game_start on this level with the packed levels, game_step until
   Ratchet has control, game_shot from the places you changed, game_mobys near them. Fix what
   looks wrong and repeat from 4.
Say what you made, with pictures.""" % [
		(" called \"%s\"" % args["name"]) if args.get("name", "") != "" else "",
		(": %s" % args["idea"]) if args.get("idea", "") != "" else ""]


func cutscene_dialogue(args: Dictionary) -> String:
	return """Make a cutscene in this level: %s

Read openrac://guide/ai and openrac://docs/cutscene-format. Then:
1. Find the characters: moby_list near the place, class_list for classes to add as actors.
   ground_at for where actors stand.
2. Time the talk: about 15 ticks a word plus 30 a line (60 ticks a second); each subtitle runs
   from its t to its end, in every language of the disc (project_info's disc; Europe has en, fr,
   de, es, it).
3. Shots: a wide shot to open, then over-the-shoulder shots on whoever speaks, a cut (cut: true)
   at each new shot, ease smooth inside a shot. Eye about 1.6 units up, look_at the speaker
   ({"actor": id}).
4. cutscene_write the whole document: length, trigger (a volume sphere where it should start, once),
   hero (held, or actor with {"figure": "hero"}), camera keys, actors with track and anims,
   subtitles.
5. cutscene_check, then cutscene_frame at the start of each shot; fix what is off.
6. scene_save, level_check, then in the game (game server): game_start, game_scene with its name,
   game_step and game_shot during it.""" % args.get("story", "")


func place_enemies(args: Dictionary) -> String:
	return """Place %s %s.

1. class_search for the kind (enemy names) and pick classes that come as own or import.
2. moby_list near the place and ground_at around it, to find open floor away from walls.
3. moby_add each with on_ground, facing where Ratchet comes from (yaw).
4. scene_save, level_check (job_wait), fix problems.
5. In the game: game_start on the level, game_hero_place near them, game_step 120, game_mobys near
   the place (their state should change from idle as Ratchet comes), game_shot.""" % [args.get("what", ""), args.get("where", "")]


func check_and_fix(_args: Dictionary) -> String:
	return """Check this level and fix every edit the game cannot take.

1. scene_save, level_check, job_wait.
2. For each problem: select its path, read why, fix it (a mirrored object: give it a positive
   scale with moby_set; a class the level does not load: moby_add it in import or look mode
   instead; a cutscene problem: cutscene_get and cutscene_write).
3. Check again until it says every edit packs."""


func look_in_game(args: Dictionary) -> String:
	return """Look at this level's edits in the game%s.

1. level_pack (job_wait) so the packed levels have the edits.
2. With the game's MCP server: game_start with this level and the packed folder (project_info's
   folders), game_step until game_status says playing.
3. game_hero_place near each change, game_step 60, game_shot; game_mobys near it.
4. Say what you saw, with the pictures.""" % ((": %s" % args["what"]) if args.get("what", "") != "" else "")
