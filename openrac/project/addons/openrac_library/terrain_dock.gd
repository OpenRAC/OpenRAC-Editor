@tool
extends EditorDock
## The Terrain panel: brushes for the level's ground, on its mesh (overhangs too), not a height map.
## Pick what the brush does, its size and strength, then click on the ground in the 3D view.
## Bake light puts sun, shadows and sky light into the ground's colours. Build rocks turns the rock
## recipe into the Rocks mesh. Every change keeps a backup (Undo). The work is
## openrac/tools/terrain_tools.py's (docs/TERRAIN.md), the same commands as the terrain_* MCP tools.

const Tools = preload("mcp_terrain_tools.gd")

const MODES: Array = [
	["Raise", "sculpt", "raise"], ["Lower", "sculpt", "lower"], ["Smooth", "sculpt", "smooth"],
	["Roughen", "sculpt", "roughen"], ["Flatten", "sculpt", "flatten"],
	["Paint picture", "paint", ""], ["Map pictures again", "uv", ""],
]

var mode_list: OptionButton
var picture_list: OptionButton
var size_box: SpinBox
var strength_box: SpinBox
var tile_box: SpinBox
var active_check: CheckButton
var status: Label
var buttons: Array[Button] = []

var worker: Thread = null
## The ground's triangles for picking, read when first needed and after each change.
var picker: Array = []


func _init() -> void:
	title = "Terrain"
	default_slot = EditorDock.DOCK_SLOT_RIGHT_UL


func _ready() -> void:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 6)
	add_child(box)

	active_check = CheckButton.new()
	active_check.text = "Brush on (click the ground in the 3D view)"
	box.add_child(active_check)

	mode_list = OptionButton.new()
	for m in MODES:
		mode_list.add_item(m[0])
	mode_list.item_selected.connect(func(_i): update_rows())
	box.add_child(labelled("Brush", mode_list))

	picture_list = OptionButton.new()
	box.add_child(labelled("Picture", picture_list))

	size_box = number(10.0, 1.0, 200.0, 0.5)
	box.add_child(labelled("Size", size_box))
	strength_box = number(1.0, 0.05, 20.0, 0.05)
	box.add_child(labelled("Strength", strength_box))
	tile_box = number(3.0, 0.5, 32.0, 0.5)
	box.add_child(labelled("Picture every (units)", tile_box))

	var row := HFlowContainer.new()
	box.add_child(row)
	for entry in [["Bake light", bake_light], ["Build rocks", build_rocks], ["Undo", undo], ["Numbers", numbers]]:
		var b := Button.new()
		b.text = entry[0]
		b.pressed.connect(entry[1])
		row.add_child(b)
		buttons.append(b)

	status = Label.new()
	status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	status.text = "Pick a brush, then click on the ground."
	box.add_child(status)
	fill_pictures()
	update_rows()


func _exit_tree() -> void:
	if worker != null and worker.is_started():
		worker.wait_to_finish()


static func labelled(text: String, control: Control) -> Control:
	var row := HBoxContainer.new()
	var label := Label.new()
	label.text = text
	label.custom_minimum_size.x = 120
	row.add_child(label)
	control.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(control)
	return row


static func number(value: float, low: float, high: float, step: float) -> SpinBox:
	var s := SpinBox.new()
	s.min_value = low
	s.max_value = high
	s.step = step
	s.value = value
	return s


func fill_pictures() -> void:
	picture_list.clear()
	var path := Tools.terrain_res()
	if path.is_empty():
		return
	var folder := path.get_base_dir().path_join("textures")
	for file in DirAccess.get_files_at(folder):
		if file.begins_with("terrain_") and file.ends_with(".png"):
			var tex := load(folder.path_join(file)) as Texture2D
			if tex != null:
				picture_list.add_icon_item(tex, file.get_basename())
			else:
				picture_list.add_item(file.get_basename())
			picture_list.set_item_metadata(picture_list.item_count - 1, "textures/" + file)


func update_rows() -> void:
	var kind: String = MODES[mode_list.selected][1]
	picture_list.get_parent().visible = kind == "paint"
	tile_box.get_parent().visible = kind == "uv"
	strength_box.get_parent().visible = kind != "uv"


func busy() -> bool:
	return worker != null and worker.is_alive()


## Runs terrain_tools.py OP in a thread; when it changed the ground, the editor imports it again.
func start(op: String, args: PackedStringArray, what: String, changes: bool = true) -> void:
	if busy():
		status.text = "Still working on the last change."
		return
	var cmd: Variant = Tools.command(op, args)
	if cmd is String:
		status.text = cmd
		return
	if changes:
		EditorInterface.save_all_scenes()
	status.text = what + "..."
	for b in buttons:
		b.disabled = true
	if worker != null and worker.is_started():
		worker.wait_to_finish()
	worker = Thread.new()
	worker.start(func() -> void:
		var out: Array = []
		var code := OS.execute(cmd["program"], cmd["args"], out, true)
		var lines := PackedStringArray(String("\n".join(out)).split("\n", false))
		done.call_deferred(code, lines, what, changes))


func done(code: int, lines: PackedStringArray, what: String, changes: bool) -> void:
	for b in buttons:
		b.disabled = false
	var result: Dictionary = Tools.finished(code, lines, changes)
	if not result.get("ok", false):
		status.text = what + " failed: " + String(result.get("error", ""))
		return
	if changes:
		picker.clear()
	var words: PackedStringArray = []
	for key in result:
		if key != "ok" and not (result[key] is Array and (result[key] as Array).size() > 4):
			words.append("%s %s" % [String(key).replace("_", " "), str(result[key])])
	status.text = what + " done. " + ", ".join(words)


func bake_light() -> void:
	start("bake-light", PackedStringArray(), "Baking light")


func build_rocks() -> void:
	start("build", PackedStringArray(), "Building rocks")


func undo() -> void:
	start("undo", PackedStringArray(), "Undo")


func numbers() -> void:
	start("info", PackedStringArray(), "Reading the numbers", false)


## A click in the 3D view while the brush is on: the brush at the ground under the mouse.
func handle_click(camera: Camera3D, at: Vector2) -> bool:
	if not active_check.button_pressed:
		return false
	var hit: Variant = pick(camera.project_ray_origin(at), camera.project_ray_normal(at))
	if hit == null:
		status.text = "No ground under the mouse."
		return true
	# Godot (x, y, z) is the game's (x, -z, y).
	var g: Vector3 = hit
	var args := {"at": [g.x, -g.z, g.y], "radius": size_box.value, "strength": strength_box.value}
	var m: Array = MODES[mode_list.selected]
	match m[1]:
		"sculpt":
			var extra: PackedStringArray = ["--mode", m[2]]
			extra.append_array(Tools.brush_args(args))
			start("sculpt", extra, m[0])
		"paint":
			if picture_list.selected < 0:
				status.text = "Pick a picture first."
				return true
			var extra: PackedStringArray = ["--texture", String(picture_list.get_item_metadata(picture_list.selected))]
			extra.append_array(Tools.brush_args(args))
			start("paint", extra, "Painting")
		"uv":
			args["tile"] = tile_box.value
			args.erase("strength")
			start("uv", Tools.brush_args(args), "Mapping pictures")
	return true


## The nearest point where the ray meets the terrain's triangles, or null.
func pick(from: Vector3, dir: Vector3) -> Variant:
	if picker.is_empty():
		read_picker()
	var best: Variant = null
	var best_d := INF
	for entry in picker:
		var xf: Transform3D = entry[0]
		var tm: TriangleMesh = entry[1]
		var inv := xf.affine_inverse()
		var r: Dictionary = tm.intersect_ray(inv * from, (inv.basis * dir).normalized())
		if r.is_empty():
			continue
		var p: Vector3 = xf * Vector3(r["position"])
		var d := from.distance_to(p)
		if d < best_d:
			best_d = d
			best = p
	return best


func read_picker() -> void:
	picker.clear()
	var root := EditorInterface.get_edited_scene_root()
	if root == null:
		return
	var path := Tools.terrain_res()
	for node in root.find_children("*", "Node3D", true, false):
		if node.scene_file_path == path:
			for mi in node.find_children("*", "MeshInstance3D", true, false):
				var mesh: Mesh = (mi as MeshInstance3D).mesh
				if mesh != null:
					picker.append([(mi as MeshInstance3D).global_transform, mesh.generate_triangle_mesh()])
