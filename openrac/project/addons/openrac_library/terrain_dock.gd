@tool
extends EditorDock
## The Terrain panel: brushes for the level's ground, on its mesh (overhangs too), not a height map.
## Brush mode (the Terrain brush button above the 3D view, or here) turns the 3D view into a
## canvas: a circle follows the mouse on the ground, a click or a drag uses the brush there, and a
## right drag or the wheel still moves the camera. [ and ] change the size, Esc leaves the mode.
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
var mode_button: Button
var status: Label
var buttons: Array[Button] = []

var worker: Thread = null
## The ground's triangles for picking, read when first needed and after each change.
var picker: Array = []

## Brush mode is on: the 3D view paints instead of selecting.
var brush_on := false
## Called when brush mode turns on or off (the plugin's toolbar button follows it).
var brush_changed: Callable = Callable()
## Asks the plugin to draw the 3D view's overlay again.
var redraw: Callable = Callable()
## The ground point under the mouse (Godot space), or null; the camera it was seen with.
var hover: Variant = null
var camera: Camera3D = null
## The stroke being dragged: its spots in Godot space, and those not sent to the tools yet.
var dragging := false
var stroke: Array[Vector3] = []
var pending: Array[Vector3] = []


func _init() -> void:
	title = "Terrain"
	default_slot = EditorDock.DOCK_SLOT_RIGHT_BL  # under Properties, beside Collision and Voices


func _ready() -> void:
	# In a scroll, so the panel never needs more height than its column has (it shares it with
	# Properties above).
	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(scroll)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 6)
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(box)

	mode_button = Button.new()
	mode_button.text = "Brush mode"
	mode_button.toggle_mode = true
	mode_button.tooltip_text = "Paint on the ground in the 3D view: click or drag. Right drag still moves the camera. [ ] size, Esc to stop."
	mode_button.clip_text = true
	mode_button.toggled.connect(set_brush)
	box.add_child(mode_button)

	mode_list = OptionButton.new()
	mode_list.fit_to_longest_item = false
	mode_list.clip_text = true
	for m in MODES:
		mode_list.add_item(m[0])
	mode_list.item_selected.connect(func(_i): update_rows(); ask_redraw())
	box.add_child(labelled("Brush", mode_list))

	picture_list = OptionButton.new()
	picture_list.fit_to_longest_item = false
	picture_list.clip_text = true
	box.add_child(labelled("Picture", picture_list))

	size_box = number(10.0, 1.0, 200.0, 0.5)
	size_box.value_changed.connect(func(_v): ask_redraw())
	box.add_child(labelled("Size", size_box))
	strength_box = number(1.0, 0.05, 20.0, 0.05)
	box.add_child(labelled("Strength", strength_box))
	tile_box = number(3.0, 0.5, 32.0, 0.5)
	box.add_child(labelled("Tile size", tile_box))

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
	status.text = "Turn on Brush mode, then click or drag on the ground."
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
	# No width of its own beyond the column's: the panel shares it with Properties and Voices.
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.size_flags_stretch_ratio = 0.8
	label.clip_text = true
	label.tooltip_text = text
	label.mouse_filter = Control.MOUSE_FILTER_PASS
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
	if changes:
		picker.clear()
	var result: Dictionary = Tools.finished(code, lines, changes)
	if not result.get("ok", false):
		status.text = what + " failed: " + String(result.get("error", ""))
		return
	var words: PackedStringArray = []
	for key in result:
		if key != "ok" and not (result[key] is Array and (result[key] as Array).size() > 4):
			words.append("%s %s" % [String(key).replace("_", " "), str(result[key])])
	status.text = what + " done. " + ", ".join(words)
	if not dragging:
		send_stroke()


func bake_light() -> void:
	start("bake-light", PackedStringArray(), "Baking light")


func build_rocks() -> void:
	start("build", PackedStringArray(), "Building rocks")


func undo() -> void:
	start("undo", PackedStringArray(), "Undo")


func numbers() -> void:
	start("info", PackedStringArray(), "Reading the numbers", false)


## Turns brush mode on or off (the button here, the toolbar's, Esc).
func set_brush(on: bool) -> void:
	if on and Tools.terrain_res().is_empty():
		on = false
		status.text = "This level has no terrain to paint on."
	if on == brush_on:
		mode_button.set_pressed_no_signal(on)
		return
	brush_on = on
	mode_button.set_pressed_no_signal(on)
	hover = null
	dragging = false
	stroke.clear()
	if on:
		EditorInterface.set_main_screen_editor("3D")
		make_visible()
		status.text = "Click or drag on the ground. [ ] size, Esc to stop."
	if brush_changed.is_valid():
		brush_changed.call(on)
	ask_redraw()


func ask_redraw() -> void:
	if redraw.is_valid():
		redraw.call()


## Input in the 3D view. True when the brush used it (the editor then does not select or move).
func handle_input(cam: Camera3D, event: InputEvent) -> bool:
	if not brush_on:
		return false
	camera = cam
	var key := event as InputEventKey
	if key != null and key.pressed and not key.echo:
		match key.keycode:
			KEY_ESCAPE:
				set_brush(false)
				return true
			KEY_BRACKETLEFT:
				size_box.value /= 1.25
				return true
			KEY_BRACKETRIGHT:
				size_box.value *= 1.25
				return true
		return false
	if event is InputEventMouseMotion:
		var at: Vector2 = (event as InputEventMouseMotion).position
		hover = pick(cam.project_ray_origin(at), cam.project_ray_normal(at))
		if dragging and hover != null and (stroke.is_empty() or stroke[-1].distance_to(hover) >= size_box.value * 0.35):
			stroke.append(hover)
			pending.append(hover)
		ask_redraw()
		return dragging
	if event is InputEventMouseButton and (event as InputEventMouseButton).button_index == MOUSE_BUTTON_LEFT:
		var click := event as InputEventMouseButton
		if click.pressed:
			hover = pick(cam.project_ray_origin(click.position), cam.project_ray_normal(click.position))
			if hover == null:
				status.text = "No ground under the mouse."
				return true
			dragging = true
			stroke = [hover]
			pending.append(hover)
		elif dragging:
			dragging = false
			stroke.clear()
			send_stroke()
		ask_redraw()
		return true
	return false


## Sends the spots dragged so far as one change (one Undo); while the last one still runs they wait.
func send_stroke() -> void:
	if pending.is_empty() or busy():
		return
	# Godot (x, y, z) is the game's (x, -z, y).
	var spots: Array = []
	for g in pending:
		spots.append([g.x, -g.z, g.y])
	pending.clear()
	var args := {"at": spots[0], "radius": size_box.value, "strength": strength_box.value}
	var then: PackedStringArray = []
	for spot in spots.slice(1):
		then.append("--then")
		for v in spot:
			then.append(str(float(v)))
	var m: Array = MODES[mode_list.selected]
	match m[1]:
		"sculpt":
			var extra: PackedStringArray = ["--mode", m[2]]
			extra.append_array(Tools.brush_args(args))
			extra.append_array(then)
			start("sculpt", extra, m[0])
		"paint":
			if picture_list.selected < 0:
				status.text = "Pick a picture first."
				return
			var extra: PackedStringArray = ["--texture", String(picture_list.get_item_metadata(picture_list.selected))]
			extra.append_array(Tools.brush_args(args))
			extra.append_array(then)
			start("paint", extra, "Painting")
		"uv":
			args["tile"] = tile_box.value
			args.erase("strength")
			var extra := Tools.brush_args(args)
			extra.append_array(then)
			start("uv", extra, "Mapping pictures")


## The brush circle on the ground under the mouse, and the stroke being dragged.
func draw_overlay(overlay: Control) -> void:
	if not brush_on or camera == null or hover == null:
		return
	var colour := Color(1, 0.85, 0.3)
	var m: Array = MODES[mode_list.selected]
	match m[2]:
		"raise": colour = Color(0.4, 1, 0.5)
		"lower": colour = Color(1, 0.45, 0.4)
		"smooth", "flatten": colour = Color(0.5, 0.8, 1)
	if m[1] == "paint":
		colour = Color(1, 1, 1)
	ring(overlay, hover, size_box.value, colour, 3.0)
	ring(overlay, hover, size_box.value * 0.5, Color(colour, 0.35), 1.0)
	for spot in stroke:
		if not camera.is_position_behind(spot):
			overlay.draw_circle(camera.unproject_position(spot), 3.0, colour)
	var at := camera.unproject_position(hover)
	if not camera.is_position_behind(hover):
		overlay.draw_string(overlay.get_theme_default_font(), at + Vector2(12, -12),
			"%s  %.1f" % [m[0], size_box.value], HORIZONTAL_ALIGNMENT_LEFT, -1, 14, colour)


## A flat circle around CENTRE (Godot space, level with the ground), drawn on the overlay.
func ring(overlay: Control, centre: Vector3, radius: float, colour: Color, width: float) -> void:
	var points := PackedVector2Array()
	for k in 49:
		var a := TAU * k / 48.0
		var p := centre + Vector3(cos(a), 0, sin(a)) * radius
		if camera.is_position_behind(p):
			return
		points.append(camera.unproject_position(p))
	# A dark edge under the colour, so the circle shows on bright ground too.
	overlay.draw_polyline(points, Color(0, 0, 0, colour.a * 0.6), width + 3.0, true)
	overlay.draw_polyline(points, colour, width, true)


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
