@tool
extends EditorPlugin
## test_editor_gamepad.py's editor half: presses a pretend gamepad's buttons and sticks (through
## Input, as a real pad's driver does) in a headless editor, checks what each did and writes the
## results to the file named after --, then quits.

const MOST_SECONDS: float = 90.0

const SCENARIOS: PackedStringArray = ["first_press_focuses", "dpad_moves", "dpad_repeats", "next_panel", "tabs",
	"main_menu", "context_menu", "text_field", "fly", "turn_and_undo", "carry_and_undo", "carry_put_back", "hint",
	"touch_pointer", "touch_click", "touch_scroll", "touch_look"]

## The touchpads, as the editor's SDL driver sends them (JoyTouchpad in core/input/input_enums.h).
const TOUCH_LEFT := 26
const TOUCH_RIGHT := 27
const PRESS_LEFT := 28
const PRESS_RIGHT := 29
const LEFT_X := 6
const LEFT_Y := 7
const RIGHT_X := 8
const RIGHT_Y := 9

var checks: Array = []
var times: Dictionary = {}
var scenario: String = ""
var out_path: String = ""
var started: int = 0
var finished: bool = false


func _enter_tree() -> void:
	var args: PackedStringArray = OS.get_cmdline_user_args()
	out_path = args[0] if not args.is_empty() else ""
	get_tree().create_timer(MOST_SECONDS).timeout.connect(finish)
	run.call_deferred()


func run() -> void:
	await until(func() -> bool: return EditorInterface.get_edited_scene_root() != null)
	await frames(30)
	started = Time.get_ticks_msec()
	for name in SCENARIOS:
		scenario = name
		var began := Time.get_ticks_msec()
		await call(name)
		await release_all()
		times[name] = Time.get_ticks_msec() - began
	scenario = ""
	finish()


func finish() -> void:
	if finished:
		return
	finished = true
	var file := FileAccess.open(out_path, FileAccess.WRITE)
	file.store_string(JSON.stringify({"checks": checks, "times": times, "total_ms": Time.get_ticks_msec() - started,
		"stopped_in": scenario if not times.has(scenario) else ""}))
	file.close()
	get_tree().quit()


func check(name: String, ok: bool, detail: Variant = "") -> void:
	checks.append({"scenario": scenario, "name": name, "ok": ok, "detail": str(detail)})


func frames(count: int) -> void:
	for _i in range(count):
		await get_tree().process_frame


func until(condition: Callable, most: int = 300) -> bool:
	for _i in range(most):
		if condition.call():
			return true
		await get_tree().process_frame
	return condition.call()


# The pretend pad ---------------------------------------------------------------------------------

func button(index: JoyButton, pressed: bool) -> void:
	var e := InputEventJoypadButton.new()
	e.device = 0
	e.button_index = index
	e.pressed = pressed
	Input.parse_input_event(e)


func axis(index: JoyAxis, value: float) -> void:
	var e := InputEventJoypadMotion.new()
	e.device = 0
	e.axis = index
	e.axis_value = value
	Input.parse_input_event(e)


func tap(index: JoyButton) -> void:
	button(index, true)
	await frames(3)
	button(index, false)
	await frames(3)


func release_all() -> void:
	for b in [JOY_BUTTON_A, JOY_BUTTON_B, JOY_BUTTON_X, JOY_BUTTON_Y, JOY_BUTTON_LEFT_SHOULDER, JOY_BUTTON_RIGHT_SHOULDER,
			JOY_BUTTON_BACK, JOY_BUTTON_START, JOY_BUTTON_LEFT_STICK, JOY_BUTTON_DPAD_UP, JOY_BUTTON_DPAD_DOWN,
			JOY_BUTTON_DPAD_LEFT, JOY_BUTTON_DPAD_RIGHT]:
		button(b, false)
	for b in [TOUCH_LEFT, TOUCH_RIGHT, PRESS_LEFT, PRESS_RIGHT]:
		button(b, false)
	for a in [JOY_AXIS_LEFT_X, JOY_AXIS_LEFT_Y, JOY_AXIS_RIGHT_X, JOY_AXIS_RIGHT_Y, JOY_AXIS_TRIGGER_LEFT, JOY_AXIS_TRIGGER_RIGHT]:
		axis(a, 0.0)
	await frames(3)
	# Close whatever a scenario left open.
	for w in popups():
		w.hide()
	await frames(2)


# What the editor shows ---------------------------------------------------------------------------

func focus() -> Control:
	return get_tree().root.gui_get_focus_owner()


func level_tree() -> Tree:
	var dock: Node = EditorInterface.get_base_control().find_children("*", "SceneTreeDock", true, false)[0]
	return dock.find_children("*", "Tree", true, false)[0]


func view() -> Control:
	return EditorInterface.get_editor_viewport_3d(0).get_parent() as Control


func popups() -> Array:
	var open: Array = []
	for w in get_tree().root.find_children("*", "Window", true, false):
		if w.visible and (w is PopupMenu or w is AcceptDialog):
			open.append(w)
	return open


func moby() -> Node3D:
	return EditorInterface.get_edited_scene_root().find_child("Vendor_0000", true, false)


func pick_moby() -> void:
	EditorInterface.get_selection().clear()
	EditorInterface.get_selection().add_node(moby())
	await frames(3)


func focus_view() -> void:
	# The view's surface: the control that takes the view's input.
	var surface: Control = null
	for c in EditorInterface.get_base_control().find_children("*", "Control", true, false):
		if c.get_class() == "Control" and c.focus_mode == Control.FOCUS_ALL and c.get_parent().get_class() == "Node3DEditorViewport":
			surface = c
			break
	surface.grab_focus()
	await frames(2)


# The scenarios -----------------------------------------------------------------------------------

func first_press_focuses() -> void:
	var owner := focus()
	if owner:
		owner.release_focus()
	await frames(2)
	await tap(JOY_BUTTON_A)
	check("a press with nothing focused focuses the Level list", focus() == level_tree(), focus())


func dpad_moves() -> void:
	var tree := level_tree()
	tree.grab_focus()
	tree.set_selected(tree.get_root(), 0)
	await frames(2)
	await tap(JOY_BUTTON_DPAD_DOWN)
	var after: TreeItem = tree.get_selected()
	check("d-pad down goes to the next row", after != null and after != tree.get_root(), after)
	await tap(JOY_BUTTON_DPAD_UP)
	check("d-pad up comes back", tree.get_selected() == tree.get_root(), tree.get_selected())


func dpad_repeats() -> void:
	var tree := level_tree()
	tree.get_root().set_collapsed_recursive(false)
	tree.grab_focus()
	tree.set_selected(tree.get_root(), 0)
	await frames(2)
	button(JOY_BUTTON_DPAD_DOWN, true)
	await get_tree().create_timer(0.8).timeout
	button(JOY_BUTTON_DPAD_DOWN, false)
	await frames(3)
	var rows := 0
	var it: TreeItem = tree.get_root()
	while it and it != tree.get_selected():
		it = it.get_next_visible()
		rows += 1
	check("holding the d-pad goes down more than one row", rows >= 3, rows)


func next_panel() -> void:
	var tree := level_tree()
	tree.grab_focus()
	await frames(2)
	var seen: Array = [focus()]
	for _i in range(6):
		await tap(JOY_BUTTON_BACK)
		if focus() and not seen.has(focus()):
			seen.append(focus())
	check("View visits several panels", seen.size() >= 3, seen)
	var in_view := false
	for c in seen:
		if c and c.get_parent() and c.get_parent().get_class() == "Node3DEditorViewport":
			in_view = true
	check("one of them is the 3D view", in_view, seen)
	# And it comes round again.
	tree.grab_focus()
	await frames(2)
	for _i in range(seen.size()):
		await tap(JOY_BUTTON_BACK)
	check("View comes back round to the Level list", focus() == tree, focus())


func tabs() -> void:
	# A dock place with more than one tab.
	var container: TabContainer = null
	for c in EditorInterface.get_base_control().find_children("*", "DockTabContainer", true, false):
		if c.is_visible_in_tree() and c.get_tab_count() >= 2 and c.get_class() != "EditorBottomPanel":
			container = c
			break
	if container == null:
		check("a dock place has tabs", false)
		return
	var before := container.current_tab
	var inside: Control = null
	for c in container.get_current_tab_control().find_children("*", "Control", true, false):
		if c.is_visible_in_tree() and c.focus_mode == Control.FOCUS_ALL:
			inside = c
			break
	inside.grab_focus()
	await frames(2)
	await tap(JOY_BUTTON_RIGHT_SHOULDER)
	check("RB goes to the next tab", container.current_tab != before, container.current_tab)
	check("and focus goes into it", focus() != null and container.get_current_tab_control().is_ancestor_of(focus()), focus())
	await tap(JOY_BUTTON_LEFT_SHOULDER)
	check("LB comes back", container.current_tab == before, container.current_tab)


func main_menu() -> void:
	level_tree().grab_focus()
	await frames(2)
	await tap(JOY_BUTTON_START)
	await frames(3)
	var open := popups()
	check("Start opens the menu", open.size() == 1 and open[0] is PopupMenu, open)
	if open.is_empty():
		return
	var first: PopupMenu = open[0]
	await tap(JOY_BUTTON_DPAD_RIGHT)
	await frames(3)
	var now := popups()
	check("right goes to the next menu", now.size() == 1 and now[0] != first, now)
	await tap(JOY_BUTTON_B)
	await frames(3)
	check("B closes it", popups().is_empty(), popups())


func context_menu() -> void:
	var tree := level_tree()
	tree.grab_focus()
	var item: TreeItem = tree.get_root().get_first_child()
	tree.set_selected(item, 0)
	await frames(3)
	await tap(JOY_BUTTON_Y)
	await frames(3)
	var open := popups()
	check("Y opens the right-click menu", open.size() == 1 and open[0] is PopupMenu and open[0].item_count > 0, open)
	await tap(JOY_BUTTON_B)
	await frames(3)
	check("B closes it", popups().is_empty(), popups())
	check("and focus stays on the list", focus() == tree, focus())


func text_field() -> void:
	var field: LineEdit = null
	for c in EditorInterface.get_base_control().find_children("*", "LineEdit", true, false):
		if c.is_visible_in_tree() and c.editable:
			field = c
			break
	if field == null:
		check("a text field is on screen", false)
		return
	# Reached with the d-pad, as a pad does: the field is not typed in yet.
	button(JOY_BUTTON_DPAD_DOWN, true)
	await frames(3)
	field.grab_focus()
	await frames(2)
	button(JOY_BUTTON_DPAD_DOWN, false)
	await frames(3)
	check("a field reached with the d-pad does not start typing", not field.is_editing())
	await tap(JOY_BUTTON_A)
	check("A starts typing", field.is_editing())
	await tap(JOY_BUTTON_B)
	check("B stops", not field.is_editing())


func fly() -> void:
	await focus_view()
	var camera := EditorInterface.get_editor_viewport_3d(0).get_camera_3d()
	var start := camera.global_transform
	axis(JOY_AXIS_LEFT_Y, -1.0)
	await frames(30)
	axis(JOY_AXIS_LEFT_Y, 0.0)
	await frames(20)
	var moved := camera.global_position - start.origin
	var forward := -start.basis.z
	forward.y = 0
	check("the left stick flies forward", moved.length() > 0.1 and moved.normalized().dot(forward.normalized()) > 0.9, moved)
	check("level, not up or down", absf(moved.y) < 0.01, moved)
	var y0 := camera.global_position.y
	axis(JOY_AXIS_TRIGGER_RIGHT, 1.0)
	await frames(20)
	axis(JOY_AXIS_TRIGGER_RIGHT, 0.0)
	await frames(20)
	check("RT goes up", camera.global_position.y > y0 + 0.05, camera.global_position.y - y0)
	var look0 := -camera.global_transform.basis.z
	axis(JOY_AXIS_RIGHT_X, 1.0)
	await frames(20)
	axis(JOY_AXIS_RIGHT_X, 0.0)
	await frames(20)
	var look1 := -camera.global_transform.basis.z
	check("the right stick turns the view", look0.angle_to(look1) > 0.1, look0.angle_to(look1))
	check("the left stick did not move the focus", focus() and focus().get_parent().get_class() == "Node3DEditorViewport", focus())


func turn_and_undo() -> void:
	await pick_moby()
	await focus_view()
	var before := moby().global_transform
	await tap(JOY_BUTTON_LEFT_SHOULDER)
	var turned := moby().global_transform
	var angle := before.basis.x.angle_to(turned.basis.x)
	check("LB turns the picked object by 15 degrees", absf(rad_to_deg(angle) - 15.0) < 0.5, rad_to_deg(angle))
	check("in place", turned.origin.is_equal_approx(before.origin), turned.origin)
	await tap(JOY_BUTTON_X) # X in the view is carry: a tap carries nowhere.
	EditorInterface.get_editor_undo_redo().get_history_undo_redo(EditorInterface.get_editor_undo_redo().get_object_history_id(moby())).undo()
	await frames(2)
	if not moby().global_transform.is_equal_approx(before):
		# The tap above may have made its own step; undo it too.
		EditorInterface.get_editor_undo_redo().get_history_undo_redo(EditorInterface.get_editor_undo_redo().get_object_history_id(moby())).undo()
		await frames(2)
	check("undo turns it back", moby().global_transform.is_equal_approx(before), moby().global_transform)


func carry_and_undo() -> void:
	await pick_moby()
	await focus_view()
	var before := moby().global_position
	button(JOY_BUTTON_X, true)
	await frames(3)
	axis(JOY_AXIS_LEFT_X, 1.0)
	await frames(25)
	axis(JOY_AXIS_LEFT_X, 0.0)
	await frames(15)
	button(JOY_BUTTON_X, false)
	await frames(3)
	var after := moby().global_position
	check("holding X carries the picked object with the view", after.distance_to(before) > 0.1, after - before)
	var ur := EditorInterface.get_editor_undo_redo().get_history_undo_redo(EditorInterface.get_editor_undo_redo().get_object_history_id(moby()))
	check("as one undo step", ur.get_current_action_name() == "Move Objects", ur.get_current_action_name())
	ur.undo()
	await frames(2)
	check("undo puts it back", moby().global_position.is_equal_approx(before), moby().global_position)


func carry_put_back() -> void:
	await pick_moby()
	await focus_view()
	var before := moby().global_position
	button(JOY_BUTTON_X, true)
	await frames(3)
	axis(JOY_AXIS_LEFT_Y, -1.0)
	await frames(20)
	axis(JOY_AXIS_LEFT_Y, 0.0)
	await frames(5)
	check("it moves while carried", moby().global_position.distance_to(before) > 0.1)
	await tap(JOY_BUTTON_B)
	check("B puts it back", moby().global_position.is_equal_approx(before), moby().global_position)
	button(JOY_BUTTON_X, false)
	await frames(3)
	check("and letting go of X then moves nothing", moby().global_position.is_equal_approx(before), moby().global_position)


func bar() -> Control:
	var found := EditorInterface.get_base_control().find_children("*", "EditorGamepadBar", true, false)
	return found[0] if not found.is_empty() else null


func hint() -> void:
	level_tree().grab_focus()
	await tap(JOY_BUTTON_DPAD_DOWN)
	check("the bar at the bottom shows what the buttons do", bar() != null and bar().is_visible_in_tree(), bar())
	check("across the editor's width", bar() != null and bar().size.x > 600, bar().size if bar() else null)
	# The mouse takes over: the bar goes.
	var click := InputEventMouseButton.new()
	click.button_index = MOUSE_BUTTON_LEFT
	click.pressed = true
	click.position = Vector2(5, 5)
	Input.parse_input_event(click)
	click = click.duplicate()
	click.pressed = false
	Input.parse_input_event(click)
	await frames(3)
	check("a mouse click hides it", not bar().is_visible_in_tree())


# The touchpads ------------------------------------------------------------------------------------

func mouse_to(at: Vector2) -> void:
	var e := InputEventMouseMotion.new()
	e.position = at
	e.global_position = at
	Input.parse_input_event(e)
	await frames(2)


## A swipe on one touchpad (right or left) from FROM to TO, -1 to 1, in STEPS frames.
func swipe(right: bool, from: Vector2, to: Vector2, steps: int = 12, lift: bool = true) -> void:
	axis(RIGHT_X if right else LEFT_X, from.x)
	axis(RIGHT_Y if right else LEFT_Y, from.y)
	button(TOUCH_RIGHT if right else TOUCH_LEFT, true)
	await frames(2)
	for i in range(1, steps + 1):
		var at := from.lerp(to, float(i) / steps)
		axis(RIGHT_X if right else LEFT_X, at.x)
		axis(RIGHT_Y if right else LEFT_Y, at.y)
		await frames(1)
	if lift:
		button(TOUCH_RIGHT if right else TOUCH_LEFT, false)
		await frames(2)


func touch_pointer() -> void:
	await mouse_to(Vector2(300, 300))
	await swipe(true, Vector2(-0.4, 0), Vector2(0.4, 0))
	var at := get_tree().root.get_mouse_position()
	check("a swipe right on the right touchpad moves the pointer right", at.x > 400 and absf(at.y - 300) < 2, at)
	await swipe(true, Vector2(0, 0.3), Vector2(0, -0.3))
	var up := get_tree().root.get_mouse_position()
	check("and up moves it up", up.y < at.y - 50, up)


func touch_click() -> void:
	# A button of the test's own on top of the editor, the pointer over it, then presses.
	var target := Button.new()
	target.text = "Test"
	target.position = Vector2(300, 300)
	target.size = Vector2(160, 48)
	target.top_level = true
	target.z_index = 100
	EditorInterface.get_base_control().add_child(target)
	var counts := {"left": 0, "right": 0}
	target.pressed.connect(func() -> void: counts["left"] += 1)
	target.gui_input.connect(func(e: InputEvent) -> void:
		if e is InputEventMouseButton and e.button_index == MOUSE_BUTTON_RIGHT and e.pressed:
			counts["right"] += 1)
	await frames(3)
	await mouse_to(target.get_global_transform_with_canvas() * (target.size / 2))
	button(PRESS_RIGHT, true)
	await frames(3)
	check("pressing the right touchpad holds the left mouse button", Input.get_mouse_button_mask() & MOUSE_BUTTON_MASK_LEFT != 0)
	button(PRESS_RIGHT, false)
	await frames(3)
	check("and lets go", Input.get_mouse_button_mask() & MOUSE_BUTTON_MASK_LEFT == 0)
	check("a press clicks what is under the pointer", counts["left"] == 1, counts)
	button(PRESS_LEFT, true)
	await frames(2)
	button(PRESS_LEFT, false)
	await frames(3)
	check("pressing the left touchpad is a right click", counts["right"] == 1, counts)
	target.queue_free()
	await frames(2)


func touch_scroll() -> void:
	await pick_moby()
	var inspector := EditorInterface.get_inspector()
	await frames(5)
	var wheels := [0]
	var seen := func(e: InputEvent) -> void:
		if e is InputEventMouseButton and e.button_index == MOUSE_BUTTON_WHEEL_DOWN and e.pressed:
			wheels[0] += int(e.factor)
	inspector.gui_input.connect(seen)
	await mouse_to(inspector.get_global_transform_with_canvas() * (inspector.size / 2))
	await swipe(false, Vector2(0, 0.6), Vector2(0, -0.6))
	inspector.gui_input.disconnect(seen)
	check("a finger up the left touchpad scrolls the Properties down", wheels[0] >= 8, wheels[0])


func touch_look() -> void:
	var surface: Control = null
	for c in EditorInterface.get_base_control().find_children("*", "Control", true, false):
		if c.get_class() == "Control" and c.focus_mode == Control.FOCUS_ALL and c.get_parent().get_class() == "Node3DEditorViewport":
			surface = c
			break
	await mouse_to(surface.get_global_transform_with_canvas() * (surface.size / 2))
	var camera := EditorInterface.get_editor_viewport_3d(0).get_camera_3d()
	var look0 := -camera.global_transform.basis.z
	await swipe(false, Vector2(-0.5, 0), Vector2(0.5, 0))
	await frames(20)
	var look1 := -camera.global_transform.basis.z
	check("the left touchpad over the 3D view turns it", look0.angle_to(look1) > 0.2, look0.angle_to(look1))
