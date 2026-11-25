@tool
extends RefCounted
## The level's tools for AI assistants, on the editor's MCP server (EditorMCPServer, docs/MCP.md):
## the project and its pipeline (check, pack, play, planet) and the level (classes, mobys, the
## ground). plugin.gd makes one and calls register() and unregister(). Every tool speaks the game's
## terms: positions in the game's coordinates (x, y, z with z up, as the game and its docs give
## them), headings in degrees about the game's up, classes by number or name.
## mcp_cutscene_tools.gd has the cutscene tools, mcp_knowledge.gd the resources and prompts.

const Library = preload("library.gd")
const Play = preload("play.gd")
const BehaviourInspector = preload("behaviour_inspector.gd")
const PlanetInspector = preload("planet_inspector.gd")
const LevelFaces = preload("../openrac_collision/level_faces.gd")

## At most this many mobys in one answer of moby_list.
const LIST_LIMIT: int = 512

var plugin: EditorPlugin
var server: Object
var names: PackedStringArray = []

## The disc's levels and their classes, read once (class_search).
var disc_assets: Array = []
## The level collision for ground_at, by the scene it was read from.
var faces: LevelFaces
var faces_scene: String = ""
var faces_fingerprint: int = 0


func register(on: Object, owner: EditorPlugin) -> void:
	server = on
	plugin = owner
	var read_only := {"readOnlyHint": true}
	var opens_world := {"readOnlyHint": false, "destructiveHint": false, "openWorldHint": true}

	add("project_info", "This level project: game, disc and market, level number and name, planet name and id, "
		+ "the launcher's folders, whether Check and Play can run, and the other levels of the disc.", {}, [], project_info, read_only)
	add("level_check", "Check level (saves first): packs every level of the disc's projects into a throwaway folder and "
		+ "answers each edit the game cannot take as {node, path, why}. A job: follow it with job_wait.", {}, [], level_check, read_only)
	add("level_pack", "Saves, then packs every edited level into the launcher's packed levels folder (what Play and the "
		+ "launcher play). A job.", {}, [], level_pack, opens_world)
	add("level_play", "Saves, packs and starts the game on this level with the edits, as Play level does. A job; "
		+ "for pictures and state of the running game use the game's own MCP server (OpenRAC tools/game_mcp.py).", {}, [], level_play, opens_world)
	add("level_place", "Sets things down on the ground so none floats, sinks or stands in a wall: a moby, tie or "
		+ "shrub is lowered or raised until its whole underside (not one point) touches the floor, plants and rocks "
		+ "follow a slope, and with move a made thing (a crate) that cannot stand level goes to the nearest good spot "
		+ "within 4 units. Run it after placing or moving anything (moby_add, moby_set, scatter), on those names with "
		+ "move. Saves first, then the scene is read again. A job.", {
		"names": prop("array", "Only nodes with these names (* and ? patterns). Default: the whole level."),
		"move": prop("boolean", "A thing that cannot stand where it is moves to the nearest good spot (default false: only listed)."),
		"water": prop("number", "The water's height: things afloat on it stay."),
		"check": prop("boolean", "Only list what is off; change nothing.")}, [], level_place)
	add("level_check_map", "Checks the level for what a player would see wrong: cracks and holes in the level's own "
		+ "ground, collision off what is drawn (falls through, stands in the air), things floating, sunk or inside "
		+ "each other. Each problem as {node, path, why} (why ends with \"at (x, y, z)\"). Pack, Play, Add as planet "
		+ "and map export refuse a level with any. With fix it first mends what it can: cracks welded, collision "
		+ "made from the drawn ground, things set down and moved apart; the scene is read again. A job.", {
		"fix": prop("boolean", "Mend what can be mended first (default false: only list)."),
		"water": prop("number", "The sea's height: nothing under it shows (default: the project's map/water).")},
		[], level_check_map)
	add("planet_get", "The planet's name in the ship's menu and its id (set once it was added).", {}, [], planet_get, read_only)
	add("planet_set", "Names this level as a planet (the ship's menu): plain letters A-Z, digits, spaces and punctuation, "
		+ "at most 40 characters. Saved in project.godot.", {"name": prop("string", "The planet's name.")}, ["name"], planet_set)
	add("planet_add", "Adds this level as a new planet the ship can fly to (the launcher's \"Add as a new planet\"): "
		+ "packs it and installs it in the launcher's mods. Adding again updates the same planet. A job.", {}, [], planet_add, opens_world)
	add("moby_states_refresh", "Works out again how the game starts this level's mobys (spawned, hidden, state), "
		+ "after edits, from the packed level. A job.", {}, [], moby_states_refresh, {"readOnlyHint": false})

	add("class_list", "The moby classes this level has (each placed with its own logic), with names, sizes and how "
		+ "many the level places. Filter by words in the name.", {"words": prop("string", "Only names with these words.")}, [], class_list, read_only)
	add("class_search", "Finds moby classes in every level of this disc by words in their names (enemies, vendors, "
		+ "crates...). Each result says how it would come here: own (this level has it), import (with its own logic "
		+ "from its level), or look (static scenery).", {"words": prop("string", "Words of the name, e.g. \"robot\"."),
		"level": prop("integer", "Only this level (0 to 18).")}, ["words"], class_search, read_only)
	add("moby_list", "The level's mobys (or one class, a name, those near a point), with node path, class, spawn number, "
		+ "position, heading, behaviour.", {"class": prop("integer", "Only this class."), "name": prop("string",
		"Only names matching this pattern (* and ?)."), "near": prop("array", "[x, y, z]: nearest first."),
		"radius": prop("number", "With near: only within this distance."), "limit": prop("integer", "At most (default 100).")}, [], moby_list, read_only)
	add("moby_add", "Places a moby of a class at a position and heading, as the Assets dock does. From this level, "
		+ "or from another level of the disc (level): with its own logic when that class can be imported, else as "
		+ "static scenery (mode \"look\"), or drawn on another class's logic (mode \"skin\", host_class). Undoable. "
		+ "May answer with a job while the editor imports the class's files.", {
		"class": prop("integer", "The class number (class_list, class_search)."),
		"level": prop("integer", "The level the class comes from (default: this one)."),
		"position": prop("array", "[x, y, z] in the game's coordinates. Default: in front of the 3D view."),
		"yaw": prop("number", "Heading in degrees about the game's up (default 0)."),
		"on_ground": prop("boolean", "Put it on the floor under the position (default false); level_place with move then "
			+ "sets its whole underside down."),
		"mode": prop("string", "auto (default), own, import, look, skin or simple (a simple move in place of its logic: behaviour)."),
		"behaviour": prop("string", "For mode simple: spin, bob, patrol, orbit, face_hero, follow or wander."),
		"host_class": prop("integer", "For mode skin: the class of this level whose logic it runs.")}, ["class"], moby_add)
	add("moby_set", "Changes a moby: position, heading, scale, its behaviour preset and numbers, its pvars "
		+ "(rc1_pv_<name>), its group. One undo step.", {
		"node": prop("string", "Node path (moby_list), or use spawn_id."),
		"spawn_id": prop("integer", "The moby's spawn number."),
		"position": prop("array", "[x, y, z]."), "yaw": prop("number", "Heading in degrees."),
		"scale": prop("number", "Size, 1 = the class's own."),
		"behaviour": prop("string", "spin, bob, patrol, orbit, face_hero, follow, wander, or \"\" for its own logic."),
		"behaviour_numbers": prop("object", "The preset's numbers, e.g. {\"speed\": 90} (behaviour_list)."),
		"pvars": prop("object", "Named pvars and values, e.g. {\"moveSpeed\": 6}."),
		"group": prop("integer", "The moby's group (-1 for none).")}, [], moby_set)
	add("moby_remove", "Deletes mobys by node path or spawn number. One undo step.", {
		"nodes": prop("array", "Node paths."), "spawn_ids": prop("array", "Spawn numbers.")}, [], moby_remove)
	add("behaviour_list", "The behaviour presets a moby can be given (moby_set) and their numbers.", {}, [], behaviour_list, read_only)
	add("ground_at", "The floor under a point, from the level's collision: its height, its normal and its surface. "
		+ "Use it to stand objects and actors on the ground.", {"position": prop("array", "[x, y, z]: the floor below a point just above it."),
		"from_height": prop("number", "Look down from this height instead.")}, ["position"], ground_at, read_only)


func unregister() -> void:
	if server == null:
		return

	for name in names:
		server.unregister_tool(name)

	names.clear()
	server = null


func add(name: String, description: String, properties: Dictionary, required: Array, callable: Callable, annotations: Dictionary = {}) -> void:
	server.register_tool(name, description, {"type": "object", "properties": properties, "required": required}, callable, annotations)
	names.append(name)


static func prop(type: String, description: String) -> Dictionary:
	return {"type": type, "description": description}


static func fail(text: String) -> Dictionary:
	return {"error": text}


# =============================================================================
# THE PROJECT AND ITS PIPELINE
# =============================================================================

func root() -> Node:
	return EditorInterface.get_edited_scene_root()


func game() -> Node3D:
	var at: Node = root()
	return at.get_node_or_null("Game") as Node3D if at != null else null


## The level's level.json, or {}.
static func level_stats() -> Dictionary:
	for folder in DirAccess.get_directories_at("res://levels"):
		var stats: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://levels/%s/level.json" % folder))

		if stats is Dictionary:
			return stats

	return {}


func project_info(_args: Dictionary) -> Variant:
	var stats: Dictionary = level_stats()
	var project: String = ProjectSettings.globalize_path("res://").trim_suffix("/")
	var projects: String = project.get_base_dir()
	var disc_note: Variant = JSON.parse_string(FileAccess.get_file_as_string(projects.path_join(".openrac/disc.json")))
	var version: String = String(stats.get("version", "pal"))
	var others: Array = []

	for folder in DirAccess.get_directories_at(projects):
		if folder.begins_with("level_") and FileAccess.file_exists(projects.path_join(folder).path_join("project.godot")):
			others.append({"level": folder.trim_prefix("level_").to_int(),
				"name": Library.level_name(Library.project_title(projects.path_join(folder)), false),
				"project": projects.path_join(folder)})

	var check: Variant = Play.command_for("check", Play.this_level())
	var play: Variant = Play.command_for("play", Play.this_level())
	return {"game": String(stats.get("game", "rac1")), "disc": version, "market": Library.disc_name(version),
		"level": int(stats.get("level", -1)), "name": Library.level_name(Library.project_title(project), false),
		"project": project, "scene": root().scene_file_path if root() != null else "",
		"planet": planet_get({}),
		"disc_remembered": disc_note is Dictionary and FileAccess.file_exists(String(disc_note.get("disc", ""))),
		"can_check": check is Dictionary, "can_play": play is Dictionary,
		"why_not": play if play is String else (check if check is String else ""),
		"folders": {"packed": Play.user_arg("--openrac-packed"), "data": Play.user_arg("--openrac-data"),
			"levels": Play.user_arg("--openrac-levels"), "overrides": Play.user_arg("--openrac-overrides"),
			"states": Play.user_arg("--openrac-states"), "tools": Play.user_arg("--openrac-tools")},
		"levels": others}


## Runs openrac_editor.py KIND (check, play, place) as a job, with EXTRA arguments; the scenes are saved first.
func run_tool(kind: String, title: String, extra: PackedStringArray = PackedStringArray()) -> Variant:
	var command: Variant = Play.command_for(kind, Play.this_level())

	if command is String:
		return fail(command)

	EditorInterface.save_all_scenes()
	var args: PackedStringArray = ["-u"]
	args.append_array(command["args"])
	args.append_array(extra)
	var job: String = server.job_run_process(title, command["program"], args, parsed.bind(kind))
	return {"job": job, "state": "running", "next": "job_wait with this id"}


## A finished run's lines as data: each refused edit, what was said, whether it went through.
func parsed(code: int, lines: PackedStringArray, kind: String) -> Dictionary:
	var problems: Array = []
	var said: Array = []
	var at: Node = root()

	for line in lines:
		if line.begins_with("openrac-problem: "):
			var text: String = line.trim_prefix("openrac-problem: ")
			var node_name: String = text.get_slice(":", 0).strip_edges() if ":" in text else ""
			var found: Node = at.find_child(node_name, true, false) if at != null and not node_name.is_empty() else null
			problems.append({"node": node_name, "path": String(at.get_path_to(found)) if found != null else "",
				"why": text.substr(text.find(":") + 1).strip_edges() if ":" in text else text})
		elif line.begins_with("openrac_editor: "):
			said.append(line.trim_prefix("openrac_editor: "))
		elif not line.begins_with("openrac-progress"):
			said.append(line.trim_prefix("openrac-check: "))

	var result: Dictionary = {"ok": code == 0 and problems.is_empty(), "kind": kind, "problems": problems, "said": said}

	if kind == "check-map":
		var fixed: bool = false

		for line in said:
			fixed = fixed or String(line).begins_with("fixed: ")

		# The fix wrote the scene under the editor: read it again.
		if fixed and at != null:
			EditorInterface.reload_scene_from_path.call_deferred(at.scene_file_path)

		if not result["ok"]:
			if not problems.is_empty():
				result["error"] = "%d problem(s) a player would see: run with fix, then mend the rest (path) by hand." % problems.size()
			elif not said.is_empty():
				result["error"] = "\n".join(PackedStringArray(said.slice(-5)))
			else:
				result["error"] = "It did not run."

		return result

	if kind == "place":
		# The scene file was written under the editor: read it again (nothing changed with --check).
		if at != null and not said.is_empty() and not String(said[-1]).begins_with("would") and not String(said[-1]).begins_with("set 0 "):
			EditorInterface.reload_scene_from_path.call_deferred(at.scene_file_path)

		if not result["ok"]:
			result["error"] = "%d thing(s) cannot stand where they are (why): move them by hand, or run again with move." \
				% problems.size() if not problems.is_empty() else ("\n".join(said.slice(-5)) if not said.is_empty() else "It did not run.")

		return result

	if not result["ok"]:
		result["error"] = "%d edit(s) cannot be packed: fix each (path) and check again." % problems.size() \
			if not problems.is_empty() else ("\n".join(said.slice(-5)) if not said.is_empty() else "It did not run.")

	return result


func level_check(_args: Dictionary) -> Variant:
	return run_tool("check", "Check level")


func level_pack(_args: Dictionary) -> Variant:
	return run_tool("play", "Build", PackedStringArray(["--no-start"]))


func level_play(_args: Dictionary) -> Variant:
	return run_tool("play", "Play level")


func level_place(args: Dictionary) -> Variant:
	var extra := PackedStringArray()

	if bool(args.get("check", false)):
		extra.append("--check")

	if bool(args.get("move", false)):
		extra.append("--move")

	if args.get("water") != null:
		extra.append_array(["--water", str(float(args["water"]))])

	var only: Variant = args.get("names")

	if only is Array and not only.is_empty():
		extra.append("--names")

		for pattern in only:
			extra.append(String(pattern))

	return run_tool("place", "Set things down", extra)


func level_check_map(args: Dictionary) -> Variant:
	var extra := PackedStringArray()

	if bool(args.get("fix", false)):
		extra.append("--fix")

	if args.get("water") != null:
		extra.append_array(["--water", str(float(args["water"]))])

	return run_tool("check-map", "Check the map", extra)


func planet_get(_args: Dictionary) -> Dictionary:
	PlanetInspector.sync_id()
	return {"name": String(ProjectSettings.get_setting("openrac/planet/name", "")),
		"id": String(ProjectSettings.get_setting("openrac/planet/id", ""))}


func planet_set(args: Dictionary) -> Variant:
	var problem: String = PlanetInspector.save_name(String(args.get("name", "")))

	if not problem.is_empty():
		return fail(problem)

	return planet_get({})


func planet_add(_args: Dictionary) -> Variant:
	var tools: String = Play.user_arg("--openrac-tools")
	var python: String = Play.user_arg("--openrac-python")
	var overrides: String = Play.user_arg("--openrac-overrides")

	if tools.is_empty() or python.is_empty() or overrides.is_empty():
		return fail("Open the level from the launcher (Mods, Level editor, Open) to add it as a planet.")

	if String(planet_get({})["name"]).is_empty():
		return fail("Name the planet first (planet_set).")

	EditorInterface.save_all_scenes()
	var args: PackedStringArray = ["-u", tools.path_join("openrac_editor.py"), "planet", "remembered",
		ProjectSettings.globalize_path("res://").trim_suffix("/"), overrides, "--anywhere"]
	# The first add writes the planet's id into project.godot: the editor takes it at once.
	var done := func(code: int, lines: PackedStringArray) -> Dictionary:
		PlanetInspector.sync_id()
		return parsed(code, lines, "planet")
	var job: String = server.job_run_process("Add as a new planet", python, args, done)
	return {"job": job, "state": "running", "next": "job_wait with this id"}


func moby_states_refresh(_args: Dictionary) -> Variant:
	var tools: String = Play.user_arg("--openrac-tools")
	var python: String = Play.user_arg("--openrac-python")

	if tools.is_empty() or python.is_empty() or Play.user_arg("--openrac-states").is_empty():
		return fail("Open the level from the launcher to work out the mobys' start states.")

	EditorInterface.save_all_scenes()
	var args: PackedStringArray = ["-u", tools.path_join("moby_states.py"), ProjectSettings.globalize_path("res://").trim_suffix("/"),
		"--data", Play.user_arg("--openrac-data"), "--levels", Play.user_arg("--openrac-levels"),
		"--program", Play.user_arg("--openrac-states")]
	var job: String = server.job_run_process("Object start states", python, args)
	return {"job": job, "state": "running", "next": "job_wait with this id"}


# =============================================================================
# THE LEVEL
# =============================================================================

## The class number a moby node is of (its class scene moby_N.tscn), or -1.
static func class_of(node: Node) -> int:
	var file: String = node.scene_file_path.get_file().get_basename()
	return file.trim_prefix("moby_").to_int() if file.begins_with("moby_") and file.trim_prefix("moby_").is_valid_int() else -1


## A node's place in the game's coordinates: [x, y, z], heading in degrees, size (1 = its class's own).
func game_place(node: Node3D) -> Dictionary:
	var local: Transform3D = game().global_transform.affine_inverse() * node.global_transform
	var front: Vector3 = local.basis.x
	return {"position": [snappedf(local.origin.x, 0.001), snappedf(local.origin.y, 0.001), snappedf(local.origin.z, 0.001)],
		"yaw": snappedf(rad_to_deg(atan2(front.y, front.x)), 0.01),
		"scale": snappedf(local.basis.get_scale().x / class_scale(node), 0.0001)}


## The size NODE has at "scale" 1: an imported class's node carries its class's scale itself (an
## own class's scene has it inside).
static func class_scale(node: Node) -> float:
	return maxf(float(node.get_meta("rc1_foreign_class_scale", 1.0)), 0.0001)


static func vector(value: Variant) -> Variant:
	if value is Array and value.size() == 3:
		return Vector3(float(value[0]), float(value[1]), float(value[2]))

	return null


func mobys() -> Array:
	var group: Node = game().get_node_or_null("Mobys") if game() != null else null
	return group.get_children() if group != null else []


func class_list(args: Dictionary) -> Variant:
	var here: Dictionary = plugin.here()

	if here.is_empty():
		return fail("Open the level's scene first.")

	var counts: Dictionary = {}

	for node in mobys():
		counts[class_of(node)] = int(counts.get(class_of(node), 0)) + 1

	var words: String = String(args.get("words", "")).to_lower()
	var out: Array = []

	for number in here["classes"].get("mobys", {}):
		var asset: Dictionary = {"name": "moby %d" % number, "scale": 1.0}
		Library.read_class_scene(asset, FileAccess.get_file_as_string(here["classes"]["mobys"][number]))
		var known: String = Library.asset_name(String(here["game"]), "mobys", int(number))
		var name: String = known if not known.is_empty() else String(asset["name"])

		if words.is_empty() or Array(words.split(" ", false)).all(func(w: String) -> bool: return name.to_lower().contains(w)):
			out.append({"class": int(number), "name": name, "scale": asset["scale"], "placed": int(counts.get(int(number), 0))})

	out.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return a["class"] < b["class"])
	return {"level": here["level"], "classes": out}


## Every level of this disc and its moby assets (read once).
func disc_levels() -> Array:
	if not disc_assets.is_empty():
		return disc_assets

	var here: Dictionary = plugin.here()
	var projects: String = ProjectSettings.globalize_path("res://").trim_suffix("/").get_base_dir()

	for folder in DirAccess.get_directories_at(projects):
		var project: String = projects.path_join(folder)

		if not folder.begins_with("level_") or not FileAccess.file_exists(project.path_join("project.godot")):
			continue

		var number: int = folder.trim_prefix("level_").to_int()
		var level: Dictionary = {"game": here.get("game", "rac1"), "disc": here.get("disc", ""), "level": number,
			"name": Library.level_name(Library.project_title(project), false), "dir": project.path_join("levels").path_join(folder),
			"project": project}
		var assets: Array = Library.assets_of(level).filter(func(a: Dictionary) -> bool: return a["kind"] == "mobys")
		disc_assets.append({"level": level, "assets": assets})

	return disc_assets


func class_search(args: Dictionary) -> Variant:
	var here: Dictionary = plugin.here()

	if here.is_empty():
		return fail("Open the level's scene first.")

	var words: PackedStringArray = String(args.get("words", "")).to_lower().split(" ", false)
	var only: int = int(args.get("level", -1))
	var out: Array = []

	for entry in disc_levels():
		if only >= 0 and int(entry["level"]["level"]) != only:
			continue

		for asset in entry["assets"]:
			var name: String = String(asset["name"]).to_lower()

			if not Array(words).all(func(w: String) -> bool: return name.contains(w)):
				continue

			var how: String = "look" if Library.made_only(asset, here) else "own" if Library.status(asset, here) == Library.FULL else (
				"import" if Library.import_refusal(asset, here).is_empty() else "look")
			out.append({"class": asset["class"], "name": asset["name"], "level": asset["level"],
				"level_name": asset["level_name"], "scale": asset["scale"], "comes_as": how})

			if out.size() >= LIST_LIMIT:
				return {"results": out, "more": true}

	return {"results": out}


func moby_list(args: Dictionary) -> Variant:
	if game() == null:
		return fail("Open the level's scene first.")

	var only: int = int(args.get("class", -1))
	var pattern: String = String(args.get("name", ""))
	var near: Variant = vector(args.get("near"))
	var radius: float = float(args.get("radius", 0.0))
	var limit: int = clampi(int(args.get("limit", 100)), 1, LIST_LIMIT)
	var rows: Array = []

	for node in mobys():
		if not node is Node3D or (only >= 0 and class_of(node) != only) or (not pattern.is_empty() and not String(node.name).match(pattern)):
			continue

		var place: Dictionary = game_place(node)
		var row: Dictionary = {"path": String(root().get_path_to(node)), "name": String(node.name), "class": class_of(node),
			"spawn_id": int(node.get_meta("rc1_spawn_id", -1)), "position": place["position"], "yaw": place["yaw"],
			"scale": place["scale"]}

		if node.has_meta("rc1_behaviour"):
			row["behaviour"] = node.get_meta("rc1_behaviour")

		if node.has_meta("rc1_foreign_level"):
			row["from_level"] = node.get_meta("rc1_foreign_level")

		if near != null:
			row["distance"] = snappedf((near as Vector3).distance_to(vector(place["position"])), 0.01)

			if radius > 0.0 and row["distance"] > radius:
				continue

		rows.append(row)

	if near != null:
		rows.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return a["distance"] < b["distance"])

	return {"count": rows.size(), "mobys": rows.slice(0, limit)}


func find_moby(args: Dictionary) -> Node3D:
	if args.has("node") and root() != null:
		return root().get_node_or_null(NodePath(String(args["node"]))) as Node3D

	if args.has("spawn_id"):
		for node in mobys():
			if int(node.get_meta("rc1_spawn_id", -2)) == int(args["spawn_id"]):
				return node

	return null


func moby_add(args: Dictionary) -> Variant:
	var here: Dictionary = plugin.here()

	if here.is_empty() or game() == null:
		return fail("Open the level's scene first.")

	var number: int = int(args.get("class", -1))
	var from: int = int(args.get("level", here["level"]))
	var asset: Dictionary = {}

	for entry in disc_levels():
		if int(entry["level"]["level"]) == from:
			for candidate in entry["assets"]:
				if int(candidate["class"]) == number:
					asset = candidate

	if asset.is_empty():
		return fail("Level %02d has no moby class %d (class_search finds classes)." % [from, number])

	var place: Variant = null
	var position: Variant = vector(args.get("position"))

	if position != null:
		if bool(args.get("on_ground", false)):
			var ground: Dictionary = ground_at({"position": args["position"]})

			if ground.has("height"):
				position.z = float(ground["height"])

		place = {"position": position, "yaw": float(args.get("yaw", 0.0))}

	var mode: String = String(args.get("mode", "auto"))
	var full: bool = Library.status(asset, here) == Library.FULL

	if mode == "auto":
		mode = "own" if full else ("import" if Library.import_refusal(asset, here).is_empty() else "look")

	if mode not in ["own", "look", "import", "skin", "simple"]:
		return fail("mode is auto, own, import, look, skin or simple.")

	var behaviour: String = String(args.get("behaviour", ""))

	if mode == "simple":
		if not BehaviourInspector.PRESETS.has(behaviour):
			return fail("mode simple needs behaviour: one of %s." % ", ".join(BehaviourInspector.PRESETS.keys()))

		var why: String = Library.simple_refusal(asset, here)

		if not why.is_empty():
			return fail(why)

	if mode == "own" and not full:
		return fail("This level has no class %d of its own: use mode import or look." % number)

	var job: String = server.job_create("Add %s" % asset["name"])

	# The editor brings one asset in at a time: a second add waits for the first, not fails.
	if plugin.waiting_for_import:
		add_when_free.call_deferred(job, mode, asset, place, int(args.get("host_class", -1)), behaviour)
		return {"job": job, "state": "running", "note": "Waiting for the last asset to come in; job_wait for the node."}

	start_add(job, mode, asset, place, int(args.get("host_class", -1)), behaviour)

	if server.job_running(job):
		return {"job": job, "state": "running", "note": "The editor is importing the class's files; job_wait for the node."}

	return plugin.last_result


## Adds ASSET once the asset being brought in now is in, looking every quarter second up to TRIES
## times (a minute), finishing JOB. A timer's signal, not an await: nothing waits once the tools are off.
func add_when_free(job: String, mode: String, asset: Dictionary, place: Variant, host_class: int, behaviour: String = "", tries: int = 240) -> void:
	# The plugin was switched off meanwhile: its server is gone with it.
	if server == null or plugin == null or not is_instance_valid(plugin) or plugin.dock == null:
		return

	if not plugin.waiting_for_import:
		start_add(job, mode, asset, place, host_class, behaviour)
	elif tries <= 0:
		server.job_finish(job, fail("The last asset is still coming in: try again."), true)
	else:
		plugin.get_tree().create_timer(0.25).timeout.connect(add_when_free.bind(job, mode, asset, place, host_class, behaviour, tries - 1))


func start_add(job: String, mode: String, asset: Dictionary, place: Variant, host_class: int, behaviour: String = "") -> void:
	plugin.last_result = {}

	match mode:
		"own", "look":
			plugin.add_in_view(asset, place, job)
		"import":
			plugin.add_foreign(asset, place, job)
		"skin":
			plugin.add_as(asset, host_class, place, job)
		"simple":
			plugin.add_simple(asset, behaviour, place, job)


func moby_set(args: Dictionary) -> Variant:
	var node: Node3D = find_moby(args)

	if node == null:
		return fail("No such moby (moby_list lists them).")

	var undo_redo: EditorUndoRedoManager = plugin.get_undo_redo()
	undo_redo.create_action("Change %s (AI)" % node.name, UndoRedo.MERGE_DISABLE, root())
	var place: Dictionary = game_place(node)
	var position: Variant = vector(args.get("position"))

	if position != null or args.has("yaw") or args.has("scale"):
		var local: Transform3D = game().global_transform.affine_inverse() * node.global_transform
		var size: float = float(args.get("scale", place["scale"])) * class_scale(node)
		var yaw: float = deg_to_rad(float(args.get("yaw", place["yaw"])))
		var turned := Transform3D(Basis(Vector3(0, 0, 1), yaw).scaled(Vector3(size, size, size)),
			position if position != null else local.origin)
		undo_redo.add_do_property(node, "global_transform", game().global_transform * turned)
		undo_redo.add_undo_property(node, "global_transform", node.global_transform)

	if args.has("behaviour"):
		var preset: String = String(args["behaviour"])

		if not preset.is_empty() and not BehaviourInspector.PRESETS.has(preset):
			undo_redo.commit_action(false)
			return fail("Behaviours: %s." % ", ".join(BehaviourInspector.PRESETS.keys()))

		set_meta_undoable(undo_redo, node, "rc1_behaviour", preset if not preset.is_empty() else null)

	for key in Dictionary(args.get("behaviour_numbers", {})):
		set_meta_undoable(undo_redo, node, "rc1_behaviour_" + String(key), float(args["behaviour_numbers"][key]))

	for key in Dictionary(args.get("pvars", {})):
		set_meta_undoable(undo_redo, node, "rc1_pv_" + String(key), args["pvars"][key])

	if args.has("group"):
		set_meta_undoable(undo_redo, node, "rc1_group", int(args["group"]))

	undo_redo.commit_action()
	var now: Dictionary = game_place(node)
	now["path"] = String(root().get_path_to(node))
	return now


## Sets (or with null removes) a metadata entry as part of the action under way.
static func set_meta_undoable(undo_redo: EditorUndoRedoManager, node: Node, key: String, value: Variant) -> void:
	if value == null:
		undo_redo.add_do_method(node, "remove_meta", key)
	else:
		undo_redo.add_do_method(node, "set_meta", key, value)

	if node.has_meta(key):
		undo_redo.add_undo_method(node, "set_meta", key, node.get_meta(key))
	else:
		undo_redo.add_undo_method(node, "remove_meta", key)


func moby_remove(args: Dictionary) -> Variant:
	var found: Array[Node] = []

	for path in Array(args.get("nodes", [])):
		var node: Node = root().get_node_or_null(NodePath(String(path))) if root() != null else null

		if node == null:
			return fail("No node %s." % path)

		# Named twice (or by path and spawn number): deleted once.
		if not found.has(node):
			found.append(node)

	for id in Array(args.get("spawn_ids", [])):
		var node: Node = find_moby({"spawn_id": int(id)})

		if node == null:
			return fail("No moby with spawn number %d." % int(id))

		if not found.has(node):
			found.append(node)

	if found.is_empty():
		return fail("Name the mobys: nodes or spawn_ids.")

	# The undo puts them back first to last, each at its own place: in their order, so a later
	# place is never past the end.
	found.sort_custom(func(a: Node, b: Node) -> bool: return a.get_index() < b.get_index())
	var undo_redo: EditorUndoRedoManager = plugin.get_undo_redo()
	undo_redo.create_action("Delete %d objects (AI)" % found.size(), UndoRedo.MERGE_DISABLE, root())

	for node in found:
		var parent: Node = node.get_parent()
		undo_redo.add_do_method(parent, "remove_child", node)
		undo_redo.add_undo_method(parent, "add_child", node, true)
		undo_redo.add_undo_method(parent, "move_child", node, node.get_index())
		undo_redo.add_undo_property(node, "owner", root())
		undo_redo.add_undo_reference(node)

	undo_redo.commit_action()
	return {"deleted": found.map(func(n: Node) -> String: return String(n.name))}


func behaviour_list(_args: Dictionary) -> Variant:
	var out: Dictionary = {}

	for key in BehaviourInspector.PRESETS:
		var numbers: Dictionary = {}

		for number in BehaviourInspector.PRESETS[key]:
			var spec: Array = BehaviourInspector.PRESETS[key][number]
			numbers[number] = {"default": spec[0], "low": spec[1], "high": spec[2], "unit": spec[3]}

		numbers["sequence"] = {"default": -1, "low": -1, "high": 255, "unit": "the class's animation (-1: its own)"}
		out[key] = {"label": BehaviourInspector.LABELS[key], "numbers": numbers}

	return {"behaviours": out, "note": "The moby's own logic stops while a behaviour runs (the editor mod plays it)."}


## The face's normal, turned up.
func face_normal(face: int) -> Vector3:
	var points: PackedVector3Array = faces.face_points(face)
	var normal: Vector3 = (points[1] - points[0]).cross(points[2] - points[0]).normalized()
	return -normal if normal.z < 0 else normal


## The first face below a point that one can stand on: walls and steep slopes (more than about 53
## degrees, as the hero slides off them) are passed through. -1 for none.
func floor_below(from: Vector3, hit: Array) -> int:
	for i in 64:
		hit.clear()
		var face: int = faces.pick(from, Vector3(0, 0, -1), hit)

		if face < 0 or face_normal(face).z >= 0.6:
			return face

		from.z = hit[0].z - 0.01

	return -1


func ground_at(args: Dictionary) -> Dictionary:
	var at: Variant = vector(args.get("position"))

	if at == null or root() == null:
		return fail("Give position [x, y, z] in an open level.")

	# Read again after a collision edit too (the fingerprint changes), not only for another scene.
	if faces == null or faces_scene != root().scene_file_path or not is_instance_valid(faces.game) \
			or not is_instance_valid(faces.layer) or faces.game != game() or faces.fingerprint() != faces_fingerprint:
		faces = LevelFaces.new()

		if not faces.load_level(root()):
			faces = null
			return fail("This level has no editable collision.")

		faces.make_buckets()
		faces_scene = root().scene_file_path
		faces_fingerprint = faces.fingerprint()

	# From just above the point (so a floor above it is not taken), or from the height asked.
	var from := Vector3(at.x, at.y, float(args.get("from_height", at.z + 2.0)))

	var hit: Array = []
	var face: int = floor_below(from, hit)

	if face < 0:
		# Nothing below: look from high above.
		from.z = 100000.0
		face = floor_below(from, hit)

	if face < 0:
		return fail("No floor under (%.2f, %.2f)." % [at.x, at.y])

	var normal: Vector3 = face_normal(face)

	var point: Vector3 = hit[0]
	return {"height": snappedf(point.z, 0.001), "position": [snappedf(point.x, 0.001), snappedf(point.y, 0.001), snappedf(point.z, 0.001)],
		"normal": [snappedf(normal.x, 0.001), snappedf(normal.y, 0.001), snappedf(normal.z, 0.001)],
		"surface": faces.face_kind(face), "hero_wall": faces.is_hero(face)}
