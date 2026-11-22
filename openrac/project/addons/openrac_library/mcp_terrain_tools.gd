@tool
extends RefCounted
## The terrain tools for AI assistants, on the editor's MCP server (docs/MCP.md, docs/TERRAIN.md).
## They work on the level's terrain mesh (levels/level_NN/terrain.glb) through
## openrac/tools/terrain_tools.py, run as a job; the editor then imports the terrain again. Every change
## keeps a backup first (terrain_undo takes it back). Places are the game's: x, y across, z up.
## plugin.gd makes one and calls register() and unregister(); the Terrain panel (terrain_dock.gd)
## runs the same commands.

const Play = preload("play.gd")

var plugin: EditorPlugin
var server: Object
var names: PackedStringArray = []


func register(on: Object, owner: EditorPlugin) -> void:
	server = on
	plugin = owner
	var read_only := {"readOnlyHint": true}
	var brush := {
		"at": prop("array", "The brush's middle [x, y, z]; left out: the whole terrain."),
		"radius": prop("number", "The brush's radius in units (default 10)."),
		"strength": prop("number", "How much: units for raise/lower/roughen, 0-1 for smooth/flatten and for paint's share (default 1)."),
		"hardness": prop("number", "0 soft edge to 1 hard edge (default 0.5)."),
	}
	var filters := {
		"slope_min": prop("number", "Only faces at least this steep, in degrees (0 flat, 90 wall, 180 ceiling)."),
		"slope_max": prop("number", "Only faces at most this steep, in degrees."),
		"z_min": prop("number", "Only faces at least this high."),
		"z_max": prop("number", "Only faces at most this high."),
	}

	add("terrain_info", "The level terrain's numbers next to Pokitaru's: triangles per 100 m2 (Pokitaru 54), shares "
		+ "of flat ground (17%), walls (30%) and overhangs (25%), light spread (0.44) and share in shadow (41%), texture "
		+ "tile size (3 units), the textures used, and plain advice. A job.",
		{"near": prop("array", "Only within [x, y, radius].")}, [], terrain_info, read_only)
	add("terrain_textures", "The ground pictures this level has (textures/terrain_*.png), to name in terrain_paint.",
		{}, [], terrain_textures, read_only)
	add("terrain_sculpt", "A sculpt brush on the terrain mesh (any shape, overhangs too): raise or lower along the "
		+ "surface, smooth, roughen, or flatten to the brush's height. A job.",
		merged(brush, {"mode": prop("string", "raise, lower, smooth, roughen or flatten.")}), ["mode", "at"], terrain_sculpt)
	add("terrain_paint", "Gives the faces in a brush a ground picture (one per triangle, as the game draws them), "
		+ "with slope and height filters and optional patches. A job.",
		merged(merged(brush, filters), {"texture": prop("string", "The picture, e.g. textures/terrain_0003.png (terrain_textures)."),
			"noise": prop("number", "Paint in patches this many units wide (0: everywhere in the brush)."),
			"coverage": prop("number", "With noise: the share painted, 0-1.")}), ["texture"], terrain_paint)
	add("terrain_uv", "Maps the pictures of the faces in a brush again by box projection (floors from above, walls "
		+ "from the side, upright), one tile every TILE units: sharp, no smears. A job.",
		merged(merged(brush, filters), {"tile": prop("number", "Units per picture (Pokitaru: 3).")}), [], terrain_uv)
	add("terrain_bake_light", "Bakes the light into the terrain's vertex colours: sun with shadows, ambient occlusion "
		+ "from the sky, warm bounce. Colours run 0-2 as the game's (1 normal). A job, seconds to a minute.",
		{"sun": prop("array", "[azimuth, elevation] in degrees (default [-40, 50])."),
		"region": prop("array", "Only [x0, y0, x1, y1]; default: the terrain's middle 96%."),
		"strength": prop("number", "Overall brightness (default 1)."),
		"ambient": prop("number", "Light added everywhere, so no wall in shade goes black (default 0; 0.2 is soft)."),
		"ao": prop("integer", "Sky directions for the occlusion (default 24; more is smoother, slower)."),
		"models": prop("array", "Model glbs (res:// paths) baked with the terrain: each shades the others."),
		"subdivide": prop("number", "With models: split their faces to this size first, so walls and floors show shadows (2 is good)."),
		"glow": prop("number", "With models: vertices brighter than this keep their colour (flames, lamps; 0.9)."),
		"lights": prop("array", "Point lights: [{at: [x, y, z], colour: [r, g, b], radius}], blocked by what stands between.")},
		[], terrain_bake_light)
	add("terrain_rock_add", "Adds a rock to the terrain's rock recipe (terrain-rocks.json): a mesh shape with a "
		+ "narrow foot (undercut), a bulging shoulder, a rounded top and layers, like Pokitaru's islands. "
		+ "terrain_build makes the mesh.", {"at": prop("array", "Its foot [x, y, z] (under the sea for an island)."),
		"radius": prop("number", "Its radius."), "height": prop("number", "From the foot to the top."),
		"undercut": prop("number", "0-0.8: how much narrower the foot is (overhang)."),
		"bulge": prop("number", "0-0.5: the shoulder."), "taper": prop("number", "0-0.6: narrower toward the top."),
		"roughness": prop("number", "0-0.6: broken rock."), "warp": prop("number", "0-0.8: how far the outline wanders."),
		"stretch": prop("array", "[sx, sy]: an oval."), "yaw": prop("number", "The oval's heading."),
		"seed": prop("integer", "Another rock of the same size.")}, ["at", "radius", "height"], terrain_rock_add)
	add("terrain_rocks_clear", "Empties the rock recipe (the Rocks mesh stays until the next build).", {}, [], terrain_rocks_clear)
	add("terrain_build", "Turns the rock recipe into the terrain node Rocks: a closed mesh with overhangs, textured by "
		+ "slope (grass tops, grass lips on the edges, rock walls, creepers, dark undersides) and mapped at 3 units "
		+ "a tile. Then bake the light. A job.", {"step": prop("number", "Detail in units (default 2)."),
		"budget": prop("integer", "At most this many triangles (0: no limit).")}, [], terrain_build)
	add("terrain_undo", "Takes the terrain back to before the last terrain change.", {}, [], terrain_undo)


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


static func merged(a: Dictionary, b: Dictionary) -> Dictionary:
	var out := a.duplicate()
	out.merge(b, true)
	return out


func fail(message: String) -> Dictionary:
	return {"error": message}


## The level's terrain file: res:// path, or "" when the project has none.
static func terrain_res() -> String:
	if not DirAccess.dir_exists_absolute("res://levels"):
		return ""
	for folder in DirAccess.get_directories_at("res://levels"):
		var path := "res://levels/%s/terrain.glb" % folder
		if FileAccess.file_exists(path):
			return path
	return ""


## terrain_tools.py's command line for OP with ARGS, or an error text.
static func command(op: String, args: PackedStringArray) -> Variant:
	var path := terrain_res()
	if path.is_empty():
		return "This level project has no terrain.glb."
	var python: String = Play.user_arg("--openrac-python")
	var tools: String = Play.user_arg("--openrac-tools")
	if python.is_empty() or tools.is_empty():
		return "Open the level from the launcher (Mods, Level editor, Open) first."
	var out: PackedStringArray = ["-u", tools.path_join("terrain_tools.py"), op, ProjectSettings.globalize_path(path)]
	out.append_array(args)
	return {"program": python, "args": out}


## The arguments of a brush and its filters, from a tool call's or the panel's values.
static func brush_args(args: Dictionary) -> PackedStringArray:
	var out: PackedStringArray = []
	if args.get("at") is Array and (args["at"] as Array).size() == 3:
		out.append("--at")
		for v in args["at"]:
			out.append(str(float(v)))
	for key in ["radius", "strength", "hardness", "slope_min", "slope_max", "z_min", "z_max", "tile", "noise", "coverage"]:
		if args.has(key) and args[key] != null:
			out.append_array(["--" + key.replace("_", "-"), str(float(args[key]))])
	if args.has("seed"):
		out.append_array(["--seed", str(int(args["seed"]))])
	return out


## Runs terrain_tools.py as a job; when it changed the terrain, the editor imports it again.
func run(op: String, args: PackedStringArray, title: String, changes: bool = true) -> Variant:
	var cmd: Variant = command(op, args)
	if cmd is String:
		return fail(cmd)
	if changes:
		EditorInterface.save_all_scenes()
	var done := func(code: int, lines: PackedStringArray) -> Dictionary:
		return finished(code, lines, changes)
	var job: String = server.job_run_process(title, cmd["program"], cmd["args"], done)
	return {"job": job, "state": "running", "next": "job_wait with this id"}


static func finished(code: int, lines: PackedStringArray, changes: bool) -> Dictionary:
	var result: Variant = null
	for i in range(lines.size() - 1, -1, -1):
		result = JSON.parse_string(lines[i])
		if result is Dictionary:
			break
	if code != 0 or not result is Dictionary:
		return {"ok": false, "error": "\n".join(lines.slice(-5)) if not lines.is_empty() else "terrain_tools.py did not run."}
	if changes:
		reimport.call_deferred()
	(result as Dictionary)["ok"] = true
	return result


static func reimport() -> void:
	var path := terrain_res()
	if path.is_empty():
		return
	var fs := EditorInterface.get_resource_filesystem()
	fs.update_file(path)
	fs.reimport_files(PackedStringArray([path]))


func terrain_info(args: Dictionary) -> Variant:
	var extra: PackedStringArray = []
	if args.get("near") is Array and (args["near"] as Array).size() == 3:
		extra.append("--near")
		for v in args["near"]:
			extra.append(str(float(v)))
	return run("info", extra, "Terrain numbers", false)


func terrain_textures(_args: Dictionary) -> Variant:
	var path := terrain_res()
	if path.is_empty():
		return fail("This level project has no terrain.glb.")
	var folder := path.get_base_dir().path_join("textures")
	var out: Array = []
	for file in DirAccess.get_files_at(folder):
		if file.begins_with("terrain_") and file.ends_with(".png"):
			out.append("textures/" + file)
	return {"textures": out, "note": "Pokitaru-like grounds: terrain_0003 grass, 0012 rock with hanging grass, "
		+ "0011 creepers, 0017 rock, 0022 sand (other levels differ: look at them in the Assets panel)."}


func terrain_sculpt(args: Dictionary) -> Variant:
	var extra: PackedStringArray = ["--mode", String(args.get("mode", ""))]
	extra.append_array(brush_args(args))
	return run("sculpt", extra, "Sculpt terrain")


func terrain_paint(args: Dictionary) -> Variant:
	var extra: PackedStringArray = ["--texture", String(args.get("texture", ""))]
	extra.append_array(brush_args(args))
	return run("paint", extra, "Paint terrain")


func terrain_uv(args: Dictionary) -> Variant:
	return run("uv", brush_args(args), "Map terrain pictures")


func terrain_bake_light(args: Dictionary) -> Variant:
	var extra: PackedStringArray = []
	if args.get("sun") is Array and (args["sun"] as Array).size() == 2:
		extra.append_array(["--sun", str(float(args["sun"][0])), str(float(args["sun"][1]))])
	if args.get("region") is Array and (args["region"] as Array).size() == 4:
		extra.append("--region")
		for v in args["region"]:
			extra.append(str(float(v)))
	if args.has("strength"):
		extra.append_array(["--strength", str(float(args["strength"]))])
	if args.has("ambient"):
		extra.append_array(["--ambient", str(float(args["ambient"]))])
	if args.has("ao"):
		extra.append_array(["--ao", str(int(args["ao"]))])
	if args.get("models") is Array and not (args["models"] as Array).is_empty():
		extra.append("--models")
		for m in args["models"]:
			extra.append(ProjectSettings.globalize_path(str(m)))
	for key in ["subdivide", "glow"]:
		if args.has(key):
			extra.append_array(["--" + key, str(float(args[key]))])
	if args.get("lights") is Array and not (args["lights"] as Array).is_empty():
		var file := ProjectSettings.globalize_path("user://terrain_lights.json")
		var f := FileAccess.open(file, FileAccess.WRITE)
		if f == null:
			return fail("Cannot write the lights' file.")
		f.store_string(JSON.stringify(args["lights"]))
		f.close()
		extra.append_array(["--lights", file])
	return run("bake-light", extra, "Bake terrain light")


func terrain_rock_add(args: Dictionary) -> Variant:
	if not args.get("at") is Array or (args["at"] as Array).size() != 3:
		return fail("at: the rock's foot [x, y, z].")
	var extra: PackedStringArray = ["--at", str(float(args["at"][0])), str(float(args["at"][1])), str(float(args["at"][2])),
		"--radius", str(float(args.get("radius", 10))), "--height", str(float(args.get("height", 20)))]
	for key in ["undercut", "bulge", "taper", "roughness", "warp", "yaw"]:
		if args.has(key):
			extra.append_array(["--" + key, str(float(args[key]))])
	if args.get("stretch") is Array and (args["stretch"] as Array).size() == 2:
		extra.append_array(["--stretch", str(float(args["stretch"][0])), str(float(args["stretch"][1]))])
	if args.has("seed"):
		extra.append_array(["--seed", str(int(args["seed"]))])
	return run("rock-add", extra, "Add a rock", false)


func terrain_rocks_clear(_args: Dictionary) -> Variant:
	return run("rock-clear", PackedStringArray(), "Clear the rocks", false)


func terrain_build(args: Dictionary) -> Variant:
	var extra: PackedStringArray = []
	if args.has("step"):
		extra.append_array(["--step", str(float(args["step"]))])
	if args.has("budget"):
		extra.append_array(["--budget", str(int(args["budget"]))])
	return run("build", extra, "Build the rocks")


func terrain_undo(_args: Dictionary) -> Variant:
	return run("undo", PackedStringArray(), "Undo terrain change")
