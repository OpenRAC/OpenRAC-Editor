@tool
extends RefCounted
## The cutscene tools for AI assistants, on the editor's MCP server (docs/MCP.md). A cutscene is
## read and written as its .cutscene document (docs/CUTSCENE_FORMAT.md: ticks at 60 a second, the
## game's space, camera keys, actors, subtitles, sounds, events), which the Cutscene node turns
## into its children and timeline (from_cutscene) and back (to_cutscene). Each write is one undo
## step. plugin.gd makes one and calls register() and unregister().

const TICKS: float = 60.0

## The picture cutscene_frame takes, 4:3 as the game's.
const FRAME_SIZE := Vector2i(640, 480)

var plugin: EditorPlugin
var server: Object
var names: PackedStringArray = []


func register(on: Object, owner: EditorPlugin) -> void:
	server = on
	plugin = owner
	var read_only := {"readOnlyHint": true}
	var document := prop("object", "A .cutscene document (resource openrac://docs/cutscene-format): format, version, "
		+ "level, name, length (ticks), languages, trigger, options, hero, camera {keys, shake, end}, actors, subtitles, "
		+ "sounds, events. Times in ticks (60 a second), places in the game's coordinates.")

	add("cutscene_list", "The level's cutscenes: name, title, length in ticks and seconds, trigger, actors, lines.", {}, [], cutscene_list, read_only)
	add("cutscene_get", "One cutscene as its .cutscene document, with what an export would leave out or the player "
		+ "would skip.", {"name": prop("string", "The cutscene's name (cutscene_list).")}, ["name"], cutscene_get, read_only)
	add("cutscene_write", "Writes a whole cutscene from a .cutscene document: a new one, or in place of the one of "
		+ "that name. The editor builds its camera, actors, lines, sounds and events. One undo step. Answers the "
		+ "document as the editor kept it and any problems.", {"name": prop("string", "Its name (a node and file name: letters, digits, _)."),
		"document": document, "position": prop("array", "Where the Cutscene node stands [x, y, z] (its trigger's centre).")},
		["name", "document"], cutscene_write)
	add("cutscene_add", "Adds to a cutscene without writing it whole: camera keys, actors (an actor of the same id "
		+ "is replaced), subtitles, sounds, events; and sets top-level keys (length, trigger, options, hero, "
		+ "languages). One undo step.", {"name": prop("string", "The cutscene."),
		"camera_keys": prop("array", "Camera keys {t, eye, look_at | rotation, fov, cut, ease}."),
		"actors": prop("array", "Actors {id, source, track, path, anims, look_at, visible, end}."),
		"subtitles": prop("array", "Lines {t, end, text: {en: ...}, actor}."),
		"sounds": prop("array", "Sounds {t, kind, file | game_sound, actor}."),
		"events": prop("array", "Events {t, do, ...}."),
		"set": prop("object", "Top-level keys to set, e.g. {\"length\": 900, \"trigger\": {...}}.")}, ["name"], cutscene_add)
	add("cutscene_remove", "Deletes a cutscene from the level. One undo step.", {"name": prop("string", "The cutscene.")}, ["name"], cutscene_remove)
	add("cutscene_check", "What the packer and the player would refuse or skip in a cutscene (every cutscene "
		+ "without name).", {"name": prop("string", "The cutscene (default: all).")}, [], cutscene_check, read_only)
	add("cutscene_frame", "A picture of a cutscene at a tick, through its own camera, with its actors where the "
		+ "timeline puts them, at the game's 4:3 (or 16:9 with wide).", {"name": prop("string", "The cutscene."),
		"t": prop("number", "The tick (60 a second)."), "wide": prop("boolean", "16:9 instead of 4:3.")}, ["name", "t"], cutscene_frame, read_only)


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


func root() -> Node:
	return EditorInterface.get_edited_scene_root()


func game() -> Node3D:
	return root().get_node_or_null("Game") as Node3D if root() != null else null


## Every Cutscene node of the level, anywhere in the scene.
func cutscenes() -> Array:
	return root().find_children("*", "Cutscene", true, false) if root() != null else []


func find(name: String) -> Node:
	for node in cutscenes():
		if String(node.name) == name or String(node.call("get_cutscene_id")) == name:
			return node

	return null


func level_number() -> int:
	var here: Dictionary = plugin.here()
	return int(here.get("level", -1))


func cutscene_list(_args: Dictionary) -> Variant:
	var out: Array = []

	for node in cutscenes():
		var doc: Dictionary = node.call("to_cutscene")
		out.append({"name": String(node.name), "file": String(node.call("get_cutscene_id")), "title": doc.get("name", ""),
			"length": doc.get("length", 0), "seconds": snappedf(float(doc.get("length", 0)) / TICKS, 0.01),
			"trigger": doc.get("trigger", {}), "actors": Array(doc.get("actors", [])).map(func(a: Dictionary) -> String: return String(a.get("id", ""))),
			"lines": Array(doc.get("subtitles", [])).size(), "path": String(root().get_path_to(node))})

	return {"cutscenes": out}


func cutscene_get(args: Dictionary) -> Variant:
	var node: Node = find(String(args.get("name", "")))

	if node == null:
		return fail("No cutscene %s (cutscene_list)." % args.get("name", ""))

	return {"document": node.call("to_cutscene"), "problems": node.call("export_problems")}


## Builds a Cutscene from DOC in place of OLD (or new under Game), as one undo step: answers what
## the editor kept and what it could not.
func write(name: String, doc: Dictionary, old: Node, position: Variant) -> Variant:
	if game() == null:
		return fail("Open the level's scene first.")

	if name.is_empty() or name.validate_node_name() != name or name.contains(" "):
		return fail("A cutscene's name is letters, digits and _.")

	doc = doc.duplicate(true)
	doc["format"] = doc.get("format", "openrac-cutscene")
	doc["version"] = doc.get("version", 1)
	doc["game"] = doc.get("game", String(plugin.here().get("game", "rac1")))

	if level_number() >= 0:
		doc["level"] = level_number()

	var parent: Node = old.get_parent() if old != null else game()
	var index: int = old.get_index() if old != null else -1
	var made: Node3D = ClassDB.instantiate("Cutscene")
	made.name = name if old == null else String(old.name)

	if old != null:
		made.transform = (old as Node3D).transform
	elif position is Array and position.size() == 3:
		made.position = Vector3(float(position[0]), float(position[1]), float(position[2]))

	# Built in the tree (actors find their mobys), then recorded as done.
	if old != null:
		parent.remove_child(old)

	parent.add_child(made, true)

	if index >= 0:
		parent.move_child(made, index)

	var lost: PackedStringArray = made.call("from_cutscene", doc)
	own(made, root())
	var undo_redo: EditorUndoRedoManager = plugin.get_undo_redo()
	undo_redo.create_action("Write cutscene %s (AI)" % made.name, UndoRedo.MERGE_DISABLE, root())
	undo_redo.add_do_method(parent, "add_child", made, true)

	if index >= 0:
		undo_redo.add_do_method(parent, "move_child", made, index)

	undo_redo.add_do_method(self, "own", made, root())
	undo_redo.add_do_reference(made)
	undo_redo.add_undo_method(parent, "remove_child", made)

	if old != null:
		undo_redo.add_do_method(parent, "remove_child", old)
		undo_redo.add_undo_method(parent, "add_child", old, true)
		undo_redo.add_undo_method(parent, "move_child", old, index)
		undo_redo.add_undo_method(self, "own", old, root())
		undo_redo.add_undo_reference(old)

	# Done above already: recorded only.
	undo_redo.commit_action(false)
	return {"name": String(made.name), "path": String(root().get_path_to(made)), "document": made.call("to_cutscene"),
		"not_kept": lost, "problems": made.call("export_problems")}


## Sets OWNER on NODE and every node under it the scene does not get from an instanced file.
func own(node: Node, owner: Node) -> void:
	node.owner = owner

	for child in node.get_children():
		if child.owner == null or child.owner == node or child.owner == owner:
			own(child, owner)


func cutscene_write(args: Dictionary) -> Variant:
	var name: String = String(args.get("name", ""))
	var doc: Variant = args.get("document")

	if not doc is Dictionary:
		return fail("document is a .cutscene document (an object).")

	return write(name, doc, find(name), args.get("position"))


func cutscene_add(args: Dictionary) -> Variant:
	var name: String = String(args.get("name", ""))
	var node: Node = find(name)

	if node == null:
		return fail("No cutscene %s: cutscene_write makes one." % name)

	var doc: Dictionary = (node.call("to_cutscene") as Dictionary).duplicate(true)
	var camera: Dictionary = doc.get("camera", {})
	var keys: Array = Array(camera.get("keys", [])) + Array(args.get("camera_keys", []))
	keys.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return float(a.get("t", 0)) < float(b.get("t", 0)))

	if not keys.is_empty():
		camera["keys"] = keys
		doc["camera"] = camera

	var actors: Array = doc.get("actors", [])

	for actor in Array(args.get("actors", [])):
		actors = actors.filter(func(a: Dictionary) -> bool: return a.get("id") != actor.get("id"))
		actors.append(actor)

	doc["actors"] = actors

	for list in ["subtitles", "sounds", "events"]:
		var items: Array = Array(doc.get(list, [])) + Array(args.get(list, []))
		items.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return float(a.get("t", 0)) < float(b.get("t", 0)))
		doc[list] = items

	for key in Dictionary(args.get("set", {})):
		doc[key] = args["set"][key]

	# A length that ends before what was added grows to hold it.
	var last: float = 0.0

	for list in ["subtitles", "sounds", "events"]:
		for item in doc[list]:
			last = maxf(last, float(item.get("end", item.get("t", 0))))

	for key in keys:
		last = maxf(last, float(key.get("t", 0)))

	if float(doc.get("length", 0)) < last:
		doc["length"] = int(ceil(last))

	return write(String(node.name), doc, node, null)


func cutscene_remove(args: Dictionary) -> Variant:
	var node: Node = find(String(args.get("name", "")))

	if node == null:
		return fail("No cutscene %s." % args.get("name", ""))

	var parent: Node = node.get_parent()
	var undo_redo: EditorUndoRedoManager = plugin.get_undo_redo()
	undo_redo.create_action("Delete cutscene %s (AI)" % node.name, UndoRedo.MERGE_DISABLE, root())
	undo_redo.add_do_method(parent, "remove_child", node)
	undo_redo.add_undo_method(parent, "add_child", node, true)
	undo_redo.add_undo_method(parent, "move_child", node, node.get_index())
	undo_redo.add_undo_method(self, "own", node, root())
	undo_redo.add_undo_reference(node)
	undo_redo.commit_action()
	return {"deleted": String(node.name)}


func cutscene_check(args: Dictionary) -> Variant:
	var out: Dictionary = {}

	for node in cutscenes():
		if args.has("name") and String(node.name) != String(args["name"]):
			continue

		var doc: Dictionary = node.call("to_cutscene")
		var problems: Array = Array(node.call("export_problems"))

		if int(doc.get("length", 0)) <= 0:
			problems.append("It has no length: the packer leaves it out.")
		elif int(doc.get("length", 0)) > 36000:
			problems.append("It is longer than ten minutes: the packer leaves it out.")

		var ids: Dictionary = {}

		for actor in doc.get("actors", []):
			if ids.has(actor.get("id")):
				problems.append("Two actors are called %s." % actor.get("id"))

			ids[actor.get("id")] = true

		for line in doc.get("subtitles", []):
			for language in Dictionary(line.get("text", {})):
				var text: String = String(line["text"][language])

				for c in "&*=[]{}~":
					if text.contains(c):
						problems.append("The line at tick %d has \"%s\", which the game's font cannot write." % [int(line.get("t", 0)), c])

		out[String(node.name)] = problems

	if out.is_empty() and args.has("name"):
		return fail("No cutscene %s." % args["name"])

	return {"problems": out}


func cutscene_frame(args: Dictionary) -> Variant:
	var node: Node = find(String(args.get("name", "")))

	if node == null:
		return fail("No cutscene %s." % args.get("name", ""))

	var camera: Camera3D = null

	for child in node.find_children("*", "CutsceneCamera", true, false):
		camera = child
		break

	if camera == null:
		return fail("The cutscene has no camera yet (camera keys in cutscene_add).")

	if DisplayServer.get_name() == "headless":
		return fail("No picture: a headless editor draws nothing. Start the editor with its window.")

	var player: AnimationPlayer = node.call("get_timeline")

	if player != null and player.has_animation(player.assigned_animation if not player.assigned_animation.is_empty() else String(node.call("get_cutscene_id"))):
		var animation: String = player.assigned_animation if not player.assigned_animation.is_empty() else String(node.call("get_cutscene_id"))
		player.assigned_animation = animation
		player.seek(float(args.get("t", 0)) / TICKS, true)

	var wide: bool = bool(args.get("wide", false))
	var size := Vector2i(854, 480) if wide else FRAME_SIZE
	var view := SubViewport.new()
	view.size = size
	view.own_world_3d = false
	view.world_3d = EditorInterface.get_editor_viewport_3d(0).find_world_3d()
	view.render_target_update_mode = SubViewport.UPDATE_ONCE
	var eye := Camera3D.new()
	eye.fov = camera.fov
	eye.near = camera.near
	eye.far = camera.far
	view.add_child(eye)
	plugin.add_child(view)
	eye.global_transform = camera.global_transform
	eye.current = true
	var job: String = server.job_create("Cutscene frame")
	finish_frame.call_deferred(view, job, int(args.get("t", 0)), editor_marks())
	return {"job": job, "state": "running", "next": "job_wait with this id: it answers with the picture"}


## What the editor draws in the level and the game does not: the mobys' name labels, the boxes of
## classes without a model and collision shapes. Hidden from the picture on the rendering server
## only, so the scene itself does not change.
func editor_marks() -> Array:
	var marks: Array = []

	if root() == null:
		return marks

	for node in root().find_children("*", "VisualInstance3D", true, false):
		if node is Label3D or (node is MeshInstance3D and (node.name == &"Marker" or String(node.name).begins_with("Collision"))):
			if node.is_visible_in_tree():
				RenderingServer.instance_set_visible(node.get_instance(), false)
				marks.append(node)

	return marks


## Two frames on, the picture is drawn: hands it to the job, frees the view and shows the marks again.
func finish_frame(view: SubViewport, job: String, tick: int, marks: Array) -> void:
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var image: Image = view.get_texture().get_image()
	view.queue_free()

	for node in marks:
		if is_instance_valid(node):
			RenderingServer.instance_set_visible(node.get_instance(), node.is_visible_in_tree())

	if image == null or image.is_empty():
		server.job_finish(job, {"error": "No picture: the editor draws nothing (a headless editor)."}, true)
		return

	image.convert(Image.FORMAT_RGB8)
	var png: PackedByteArray = image.save_png_to_buffer()
	server.job_finish(job, {"content": [{"type": "image", "mimeType": "image/png", "data": Marshalls.raw_to_base64(png)},
		{"type": "text", "text": "Tick %d, %dx%d, through the cutscene's camera." % [tick, image.get_width(), image.get_height()]}]})
