@tool
extends EditorInspectorPlugin
## The Planet section in the Properties of a level (the top object in the Level list): the name the
## ship's menu shows when the level is added as a new planet (openrac/tools/planet.py). It is kept in
## project.godot ([openrac] planet/name), as the editor has no Project Settings window.

const NAME_SETTING: String = "openrac/planet/name"
const ID_SETTING: String = "openrac/planet/id"

## What the ship's menu shows at most (planet.py MOST_NAME).
const MOST_NAME: int = 40


func _can_handle(object: Object) -> bool:
	return is_level(object as Node)


## Whether NODE is a level: the root of a level scene (it has rc1_level).
static func is_level(node: Node) -> bool:
	if node == null or not node.is_inside_tree():
		return false

	return node.has_meta("rc1_level") and node == node.get_tree().edited_scene_root


func _parse_begin(object: Object) -> void:
	add_custom_control(section())


## Why NAME can't be a planet's name, or "" when it can (planet.py planet_name checks the same).
static func name_problem(name: String) -> String:
	if name.is_empty() or name.length() > MOST_NAME:
		return "A planet's name is 1 to %d characters." % MOST_NAME

	for i in name.length():
		var c: int = name.unicode_at(i)

		if c < 32 or c > 126:
			return "The game's font has plain letters A-Z, digits, spaces and punctuation only: \"%s\" is not one." % name[i]

	return ""


## Saves NAME as the planet's name; why not, or "".
static func save_name(name: String) -> String:
	name = name.strip_edges()
	var problem: String = name_problem(name)

	if not problem.is_empty():
		return problem

	sync_id()
	ProjectSettings.set_setting(NAME_SETTING, name)
	return "" if ProjectSettings.save() == OK else "project.godot could not be saved."


## Takes the planet's id from project.godot on disk: planet.py writes it there when the planet is
## first added, while the editor is open. Without this, the editor's next save of its settings would
## write the old empty id back, and the next add would make a second planet.
static func sync_id() -> void:
	var file := ConfigFile.new()

	if file.load("res://project.godot") != OK:
		return

	var id: String = String(file.get_value("openrac", "planet/id", ""))

	if id != String(ProjectSettings.get_setting(ID_SETTING, "")):
		ProjectSettings.set_setting(ID_SETTING, id)


func section() -> Control:
	var box := VBoxContainer.new()
	var title := Label.new()
	title.text = "Planet Name"
	title.tooltip_text = "The name the ship's menu shows when you add this level as a new planet."
	title.mouse_filter = Control.MOUSE_FILTER_PASS
	box.add_child(title)

	var field := LineEdit.new()
	field.text = String(ProjectSettings.get_setting(NAME_SETTING, ""))
	field.placeholder_text = "Letters, digits and spaces, %d at most" % MOST_NAME
	field.max_length = MOST_NAME
	box.add_child(field)

	var note := Label.new()
	note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	note.add_theme_color_override("font_color", EditorInterface.get_editor_theme().get_color("error_color", "Editor"))
	note.visible = false
	box.add_child(note)

	var apply := func() -> void:
		if field.text.strip_edges() == String(ProjectSettings.get_setting(NAME_SETTING, "")):
			note.visible = false
			return

		note.text = save_name(field.text)
		note.visible = not note.text.is_empty()

	field.text_submitted.connect(func(_text: String) -> void: apply.call())
	field.focus_exited.connect(apply)
	return box
