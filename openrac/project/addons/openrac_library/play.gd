@tool
extends HBoxContainer
## Play level, the big button on the editor's toolbar (plugin.gd adds it). It saves the scenes and
## runs openrac/tools/openrac_editor.py play on this level's project, off the editor's thread: that
## packs into the launcher's packed levels folder and starts the game on this level, or lists each
## edit the packer refuses (a click on one selects its node). The MCP tools also run "check", which
## packs into a folder that is thrown away.
## The launcher names what they need on the editor's command line, after -- (editor.json's
## editor-open): --openrac-python, --openrac-tools, --openrac-packed, and for play --openrac-data,
## --openrac-levels and --openrac-states (the game program sits beside the states program).

## The button's gold, as the game's bolts.
const GOLD := Color("f5c040")

var play_button: Button
var dialog: AcceptDialog
var problems: ItemList
var summary: Label
var worker: Thread
## Fix in the dialog when the map has problems (openrac_editor.py check-map --fix).
var fix_button: Button


func _init() -> void:
	name = "OpenRACPlay"
	play_button = Button.new()
	play_button.text = "Play Level"
	play_button.tooltip_text = "Saves, builds your edits and starts the game here."
	play_button.focus_mode = Control.FOCUS_ALL
	play_button.pressed.connect(start)
	# A gamepad's View button stops here too, as at each panel.
	play_button.add_to_group("editor_gamepad_area")
	add_child(play_button)
	dialog = AcceptDialog.new()
	dialog.title = "Play Level"
	var box := VBoxContainer.new()
	summary = Label.new()
	summary.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(summary)
	problems = ItemList.new()
	problems.size_flags_vertical = Control.SIZE_EXPAND_FILL
	problems.custom_minimum_size = Vector2(0, 160)
	problems.item_activated.connect(select_problem)
	problems.item_clicked.connect(func(index: int, _at: Vector2, _button: int) -> void: select_problem(index))
	box.add_child(problems)
	dialog.add_child(box)
	fix_button = dialog.add_button("Fix", true, "fix")
	fix_button.tooltip_text = "Mends what can be mended without a choice: cracks and holes in your ground welded, collision made from what you see, things set down on the ground and moved out of each other."
	fix_button.visible = false
	dialog.custom_action.connect(func(action: StringName) -> void:
		if action == &"fix":
			fix())
	add_child(dialog)


func _ready() -> void:
	style_button()


## A big gold button with the play sign: the one thing to press to see the level in the game.
func style_button() -> void:
	var scale: float = EditorInterface.get_editor_scale()
	# A wrapping label asks for its height at no width: give it the dialog's width, or the dialog
	# grows to the whole screen's height.
	summary.custom_minimum_size.x = 520 * scale
	var theme: Theme = EditorInterface.get_editor_theme()
	play_button.icon = theme.get_icon("Play", "EditorIcons")
	play_button.add_theme_font_override("font", theme.get_font("bold", "EditorFonts"))
	play_button.add_theme_font_size_override("font_size", roundi(theme.get_font_size("main_size", "EditorFonts") * 1.1))
	play_button.custom_minimum_size = Vector2(150, 0) * scale
	var dark := Color("1d1608")
	for state in ["font_color", "font_hover_color", "font_pressed_color", "font_focus_color", "font_hover_pressed_color",
			"icon_normal_color", "icon_hover_color", "icon_pressed_color", "icon_focus_color", "icon_hover_pressed_color"]:
		play_button.add_theme_color_override(state, dark)
	play_button.add_theme_color_override("font_disabled_color", dark * Color(1, 1, 1, 0.6))
	play_button.add_theme_color_override("icon_disabled_color", dark * Color(1, 1, 1, 0.6))
	var looks := {"normal": GOLD, "hover": GOLD.lightened(0.18), "pressed": GOLD.darkened(0.15),
		"hover_pressed": GOLD.darkened(0.1), "disabled": GOLD.darkened(0.35)}
	for state in looks:
		play_button.add_theme_stylebox_override(state, box_for(looks[state], scale))
	var focus := StyleBoxFlat.new()
	focus.draw_center = false
	focus.set_corner_radius_all(roundi(7 * scale))
	focus.set_border_width_all(maxi(2, roundi(2 * scale)))
	focus.border_color = Color.WHITE
	focus.set_expand_margin_all(2 * scale)
	play_button.add_theme_stylebox_override("focus", focus)


static func box_for(colour: Color, scale: float) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = colour
	box.set_corner_radius_all(roundi(6 * scale))
	box.content_margin_left = 16 * scale
	box.content_margin_right = 18 * scale
	box.content_margin_top = 4 * scale
	box.content_margin_bottom = 4 * scale
	box.anti_aliasing = true
	return box


func _exit_tree() -> void:
	if worker != null and worker.is_started():
		worker.wait_to_finish()


## The value after NAME on the editor's command line (after --), or "".
static func user_arg(name: String) -> String:
	var args: PackedStringArray = OS.get_cmdline_user_args()
	var at: int = args.find(name)
	return args[at + 1] if at >= 0 and at + 1 < args.size() else ""


## This level project's folder, and its disc version (level.json), or {} when it is not one.
static func this_level() -> Dictionary:
	for folder in DirAccess.get_directories_at("res://levels"):
		var stats: Variant = JSON.parse_string(FileAccess.get_file_as_string("res://levels/%s/level.json" % folder))

		# Ratchet & Clank's levels pack back; another game's do not yet.
		if stats is Dictionary and String(stats.get("game", "rac1")) == "rac1":
			return {"project": ProjectSettings.globalize_path("res://").trim_suffix("/"),
				"version": String(stats.get("version", "pal"))}

	return {}


## The game program beside the states program STATES (openrac-rac1-pal-states -> openrac-rac1-pal), or
## "". Only the program's own name changes: a folder with "-states" in it stays as it is.
static func game_beside(states: String) -> String:
	if states.is_empty():
		return ""

	var name: String = states.get_file()
	return states.left(states.length() - name.length()) + name.replace("-states", "")


## The command for KIND ("check", "check-map" or "play"), or a sentence saying what is missing.
static func command_for(kind: String, level: Dictionary) -> Variant:
	var python: String = user_arg("--openrac-python")
	var tools: String = user_arg("--openrac-tools")

	if level.is_empty():
		return "This isn't a Ratchet & Clank level, so there is nothing to build."

	if python == "" or tools == "":
		return "Open the level from the launcher (Mods, Level editor, Open) first."

	var args: PackedStringArray = [tools.path_join("openrac_editor.py"), kind, level["project"]]

	if kind == "play":
		var states: String = user_arg("--openrac-states")
		var game: String = game_beside(states)

		if user_arg("--openrac-packed") == "" or user_arg("--openrac-data") == "" or game == "":
			return "Open the level from the launcher (Mods, Level editor, Open) to play it."

		args.append_array(["--packed", user_arg("--openrac-packed"), "--game", game,
			"--data", user_arg("--openrac-data"), "--levels", user_arg("--openrac-levels")])

	return {"program": python, "args": args}


## Saves the scenes and builds and plays in the background; problems show in the dialog.
func start() -> void:
	if worker != null and worker.is_started():
		return

	var command: Variant = command_for("play", this_level())

	if command is String:
		show_result(1, PackedStringArray([command]))
		return

	EditorInterface.save_all_scenes()
	play_button.disabled = true
	play_button.text = "Building"
	worker = Thread.new()
	worker.start(run.bind(command))


## Saves, then runs check-map --fix on this level in the background; the scene is read again after.
func fix() -> void:
	if worker != null and worker.is_started():
		return

	var command: Variant = command_for("check-map", this_level())

	if command is String:
		show_result(1, PackedStringArray([command]))
		return

	var args: PackedStringArray = command["args"]
	args.append("--fix")
	command["args"] = args
	EditorInterface.save_all_scenes()
	dialog.hide()
	play_button.disabled = true
	play_button.text = "Fixing"
	worker = Thread.new()
	worker.start(run.bind(command, true))


## On the worker's thread: runs the command and hands its lines back to the editor's thread.
func run(command: Dictionary, fixing: bool = false) -> void:
	var output: Array = []
	var code: int = OS.execute(command["program"], command["args"], output, true)
	var lines := PackedStringArray()

	for chunk in output:
		lines.append_array(String(chunk).split("\n", false))

	finish.call_deferred(code, lines, fixing)


func finish(code: int, lines: PackedStringArray, fixing: bool = false) -> void:
	worker.wait_to_finish()
	worker = null
	play_button.disabled = false
	play_button.text = "Play Level"

	# The fix wrote the level's scene and ground: show them as they are now.
	if fixing:
		var root: Node = EditorInterface.get_edited_scene_root()

		if root != null and not root.scene_file_path.is_empty():
			EditorInterface.reload_scene_from_path(root.scene_file_path)

		if code != 0:
			lines.append("openrac-map-problems")

	# The game is up: its window says so, a dialog would only stand in the way.
	if code == 0 and lines.has("openrac-started"):
		play_button.text = "Game Started"
		play_button.get_tree().create_timer(3.0).timeout.connect(func() -> void:
			if is_instance_valid(play_button) and not play_button.disabled:
				play_button.text = "Play Level")
		return

	show_result(code, lines)


## The result: each refused edit as a row to click, else what was said. Nothing when the game
## simply started.
func show_result(code: int, lines: PackedStringArray) -> void:
	problems.clear()
	var said := PackedStringArray()

	for line in lines:
		if line.begins_with("openrac-problem: "):
			problems.add_item(line.trim_prefix("openrac-problem: "))
		elif line.begins_with("openrac_editor: "):
			said.append(line.trim_prefix("openrac_editor: "))
		elif not line.begins_with("openrac-"):
			said.append(line)

	var map: bool = lines.has("openrac-map-problems")
	var fixed := PackedStringArray()

	for line in said:
		if line.begins_with("fixed: "):
			fixed.append(line.trim_prefix("fixed: "))

	fix_button.visible = map and fixed.is_empty()

	if problems.item_count > 0 and map:
		summary.text = ("Fixed: %s.\n" % "; ".join(fixed) if not fixed.is_empty() else "") \
			+ "The map has %d problems a player would see. %s Click one to find it." % [problems.item_count,
			"These are left for you." if not fixed.is_empty() else "Fix mends what it can."]
	elif problems.item_count > 0:
		summary.text = "%d edits can't be built. Click one, fix it, then play again." % problems.item_count
	elif code == 0:
		summary.text = "\n".join(said) if not said.is_empty() else "Done."
	else:
		summary.text = "\n".join(said) if not said.is_empty() else "Couldn't build the level. Try again."

	problems.visible = problems.item_count > 0
	# Sized for the editor's display scale, never past most of its window.
	dialog.popup_centered_clamped(Vector2(560, 320) * EditorInterface.get_editor_scale(), 0.8)


## Selects the node a problem names ("<node>: <why>"), when the edited scene has it.
func select_problem(index: int) -> void:
	var text: String = problems.get_item_text(index)
	var root: Node = EditorInterface.get_edited_scene_root()

	if root == null or not ":" in text:
		return

	var found: Node = root.find_child(text.get_slice(":", 0).strip_edges(), true, false)

	if found == null:
		return

	EditorInterface.get_selection().clear()
	EditorInterface.get_selection().add_node(found)
	EditorInterface.edit_node(found)
