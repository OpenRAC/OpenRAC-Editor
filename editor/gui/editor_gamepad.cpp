/* SPDX-License-Identifier: MIT */
/* OpenRAC Editor: the editor works with a gamepad (Steam Deck, Steam Controller, any pad). */

#include "editor_gamepad.h"

#include "core/input/input.h"
#include "core/input/input_map.h"
#include "core/os/os.h"
#include "editor/docks/dock_tab_container.h"
#include "editor/editor_node.h"
#include "editor/editor_undo_redo_manager.h"
#include "editor/gui/editor_bottom_panel.h"
#include "editor/gui/editor_spin_slider.h"
#include "editor/inspector/editor_inspector.h"
#include "scene/gui/dialogs.h"
#include "editor/settings/editor_settings.h"
#include "editor/editor_string_names.h"
#include "editor/themes/editor_scale.h"
#include "scene/resources/style_box_flat.h"
#include "editor/scene/3d/node_3d_editor_plugin.h"
#include "scene/3d/node_3d.h"
#include "scene/gui/item_list.h"
#include "scene/gui/label.h"
#include "scene/gui/line_edit.h"
#include "scene/gui/popup_menu.h"
#include "scene/gui/rich_text_label.h"
#include "scene/gui/tab_bar.h"
#include "scene/gui/tab_container.h"
#include "scene/gui/text_edit.h"
#include "scene/gui/tree.h"
#include "scene/main/scene_tree.h"
#include "scene/main/window.h"
#include "servers/display/display_server.h"

namespace {
constexpr float STICK_DEAD_ZONE = 0.25;
constexpr float TRIGGER_DEAD_ZONE = 0.15;
constexpr double ARROW_FIRST_REPEAT = 0.4;
constexpr double ARROW_REPEAT = 0.07;
constexpr double SCROLLS_PER_SECOND = 14.0;
constexpr float LOOK_DEGREES_PER_SECOND = 140.0;
constexpr float FAST_FLY = 4.0;
constexpr float TURN_DEGREES = 15.0;
// A number field the pad changes: steps repeat like keys, and go ten times bigger once held long.
constexpr double ADJUST_FIRST_REPEAT = 0.35;
constexpr double ADJUST_REPEAT = 0.06;
constexpr double ADJUST_SPEED_UP = 1.2;
// The right touchpad moves the pointer this part of the window's width from one edge to the other,
// more for a fast swipe; the left one scrolls this many notches, or turns the 3D view this much.
constexpr float POINTER_SPEED = 0.35;
constexpr float POINTER_FAST = 2.5;
constexpr float SCROLL_NOTCHES = 12.0;
constexpr float TOUCH_LOOK_DEGREES = 120.0;

Vector2 dead_zone(const Vector2 &p_stick) {
	const float len = p_stick.length();
	if (len < STICK_DEAD_ZONE) {
		return Vector2();
	}
	// Rescale so the stick starts at 0 just past the dead zone, and goes on to 1.
	return p_stick / len * MIN(1.0f, (len - STICK_DEAD_ZONE) / (1.0f - STICK_DEAD_ZONE));
}

struct AreaOrder {
	bool operator()(const Control *p_a, const Control *p_b) const {
		return p_a->get_screen_position().x < p_b->get_screen_position().x;
	}
};

bool is_text_field(const Control *p_control) {
	return Object::cast_to<LineEdit>(p_control) || Object::cast_to<TextEdit>(p_control);
}

} // namespace

void EditorGamepad::_read_pad() {
	for (int i = 0; i < PAD_MAX; i++) {
		was_down[i] = down[i];
		down[i] = false;
	}
	left_stick = Vector2();
	right_stick = Vector2();
	left_trigger = 0;
	right_trigger = 0;

	Input *input = Input::get_singleton();
	static const JoyButton buttons[PAD_LT] = {
		JoyButton::A,
		JoyButton::B,
		JoyButton::X,
		JoyButton::Y,
		JoyButton::LEFT_SHOULDER,
		JoyButton::RIGHT_SHOULDER,
		JoyButton::BACK,
		JoyButton::START,
		JoyButton::LEFT_STICK,
		JoyButton::DPAD_UP,
		JoyButton::DPAD_DOWN,
		JoyButton::DPAD_LEFT,
		JoyButton::DPAD_RIGHT,
		JoyTouchpad::LEFT_TOUCH,
		JoyTouchpad::RIGHT_TOUCH,
		JoyTouchpad::LEFT_PRESS,
		JoyTouchpad::RIGHT_PRESS,
	};
	// Every pad counts, so the Steam Deck's own controls and a Steam Controller work side by side.
	HashSet<int> pads;
	for (const int pad : pads_seen) {
		pads.insert(pad);
	}
	const TypedArray<int> connected = input->get_connected_joypads();
	for (int p = 0; p < connected.size(); p++) {
		pads.insert(connected[p]);
	}
	for (const int pad : pads) {
		for (int i = 0; i < PAD_LT; i++) {
			down[i] = down[i] || input->is_joy_button_pressed(pad, buttons[i]);
		}
		const Vector2 l(input->get_joy_axis(pad, JoyAxis::LEFT_X), input->get_joy_axis(pad, JoyAxis::LEFT_Y));
		const Vector2 r(input->get_joy_axis(pad, JoyAxis::RIGHT_X), input->get_joy_axis(pad, JoyAxis::RIGHT_Y));
		if (l.length() > left_stick.length()) {
			left_stick = l;
		}
		if (r.length() > right_stick.length()) {
			right_stick = r;
		}
		if (input->is_joy_button_pressed(pad, JoyTouchpad::LEFT_TOUCH)) {
			touch[0] = Vector2(input->get_joy_axis(pad, JoyTouchpad::LEFT_X), input->get_joy_axis(pad, JoyTouchpad::LEFT_Y));
		}
		if (input->is_joy_button_pressed(pad, JoyTouchpad::RIGHT_TOUCH)) {
			touch[1] = Vector2(input->get_joy_axis(pad, JoyTouchpad::RIGHT_X), input->get_joy_axis(pad, JoyTouchpad::RIGHT_Y));
		}
		left_trigger = MAX(left_trigger, input->get_joy_axis(pad, JoyAxis::TRIGGER_LEFT));
		right_trigger = MAX(right_trigger, input->get_joy_axis(pad, JoyAxis::TRIGGER_RIGHT));
	}
	left_stick = dead_zone(left_stick);
	right_stick = dead_zone(right_stick);
	left_trigger = left_trigger < TRIGGER_DEAD_ZONE ? 0.0f : left_trigger;
	right_trigger = right_trigger < TRIGGER_DEAD_ZONE ? 0.0f : right_trigger;
	down[PAD_LT] = left_trigger > 0.5;
	down[PAD_RT] = right_trigger > 0.5;
}

// The window the pad works in: the open popup or dialog, else the editor. None when the editor is
// not the focused app.
Window *EditorGamepad::_active_window() const {
	Window *w = Window::get_focused_window();
	if (!w) {
		// No window has said it has focus (the headless editor never does): ask the system.
		w = get_tree()->get_root();
		if (!w->has_focus()) {
			return nullptr;
		}
	}
	const DisplayServerEnums::WindowID popup = DisplayServer::get_singleton()->window_get_active_popup();
	if (popup != DisplayServerEnums::INVALID_WINDOW_ID) {
		Window *p = ObjectDB::get_instance<Window>(DisplayServer::get_singleton()->window_get_attached_instance_id(popup));
		if (p && p->is_visible()) {
			return p;
		}
	}
	return w;
}

// The system window that holds p_window (a window drawn inside the editor has none of its own).
Window *EditorGamepad::_native_window(Window *p_window) const {
	Window *w = p_window;
	while (w && w->get_window_id() == DisplayServerEnums::INVALID_WINDOW_ID) {
		w = Object::cast_to<Window>(w->get_embedder());
	}
	if (!w) {
		w = get_tree()->get_root();
	}
	return w;
}

Control *EditorGamepad::_focus_owner() const {
	Window *w = _active_window();
	return w ? w->gui_get_focus_owner() : nullptr;
}

Node3DEditorViewport *EditorGamepad::_focused_3d_view() const {
	Node3DEditor *editor = Node3DEditor::get_singleton();
	if (!editor || !editor->is_visible_in_tree()) {
		return nullptr;
	}
	for (uint32_t i = 0; i < Node3DEditor::VIEWPORTS_COUNT; i++) {
		Node3DEditorViewport *view = editor->get_editor_viewport(i);
		if (view && view->get_surface()->has_focus()) {
			return view;
		}
	}
	return nullptr;
}

// The 3D view under p_screen, a point on the screen.
Node3DEditorViewport *EditorGamepad::_3d_view_at(const Vector2 &p_screen) const {
	Node3DEditor *editor = Node3DEditor::get_singleton();
	if (!editor || !editor->is_visible_in_tree()) {
		return nullptr;
	}
	for (uint32_t i = 0; i < Node3DEditor::VIEWPORTS_COUNT; i++) {
		Node3DEditorViewport *view = editor->get_editor_viewport(i);
		Control *surface = view ? view->get_surface() : nullptr;
		if (surface && surface->is_visible_in_tree() && Rect2(Vector2(), surface->get_size()).has_point(surface->get_screen_transform().affine_inverse().xform(p_screen))) {
			return view;
		}
	}
	return nullptr;
}

void EditorGamepad::_send_key(Key p_key, bool p_pressed, bool p_echo) {
	Window *w = _active_window();
	if (!w) {
		return;
	}
	Ref<InputEventKey> k;
	k.instantiate();
	k->set_meta(SNAME("openrac_gamepad"), true); // Device 0, the keyboard's: input actions match only it.
	k->set_window_id(_native_window(w)->get_window_id());
	k->set_keycode(p_key);
	k->set_physical_keycode(p_key);
	k->set_key_label(p_key);
	k->set_pressed(p_pressed);
	k->set_echo(p_echo);
	// Through Input, as a real key, so the action state (ui_up held, and so on) is right too.
	Input::get_singleton()->parse_input_event(k);
}

void EditorGamepad::_tap_key(Key p_key) {
	_send_key(p_key, true);
	_send_key(p_key, false);
}

void EditorGamepad::_send_mouse_button(Control *p_at, const Vector2 &p_local, MouseButton p_button, bool p_pressed, float p_factor) {
	Window *native = _native_window(p_at->get_window());
	const Vector2 pos = p_at->get_screen_transform().xform(p_local) - Vector2(native->get_position());
	Ref<InputEventMouseButton> mb;
	mb.instantiate();
	mb->set_meta(SNAME("openrac_gamepad"), true);
	mb->set_window_id(native->get_window_id());
	mb->set_button_index(p_button);
	mb->set_pressed(p_pressed);
	mb->set_factor(p_factor);
	mb->set_position(pos);
	mb->set_global_position(pos);
	if (p_pressed && p_button <= MouseButton::MIDDLE) {
		mb->set_button_mask(mouse_button_to_mask(p_button));
	}
	// Straight to the window: the real mouse pointer stays where it is.
	native->push_input(mb);
}

void EditorGamepad::_click(Control *p_at, const Vector2 &p_local, MouseButton p_button) {
	_send_mouse_button(p_at, p_local, p_button, true);
	_send_mouse_button(p_at, p_local, p_button, false);
}

// The d-pad (and, outside the 3D view, the left stick) are the arrow keys, with key repeat.
void EditorGamepad::_update_arrows(double p_delta, bool p_use_stick) {
	Key want = Key::NONE;
	if (down[PAD_UP]) {
		want = Key::UP;
	} else if (down[PAD_DOWN]) {
		want = Key::DOWN;
	} else if (down[PAD_LEFT]) {
		want = Key::LEFT;
	} else if (down[PAD_RIGHT]) {
		want = Key::RIGHT;
	} else if (p_use_stick && left_stick.length() > 0.5) {
		if (Math::abs(left_stick.x) > Math::abs(left_stick.y)) {
			want = left_stick.x < 0 ? Key::LEFT : Key::RIGHT;
		} else {
			want = left_stick.y < 0 ? Key::UP : Key::DOWN;
		}
	}

	_keep_in_area();
	Window *w = _active_window();
	Control *focus = w ? w->gui_get_focus_owner() : nullptr;
	Control *area = focus && w == get_tree()->get_root() ? _area_of(focus) : nullptr;
	bool step = want != held_arrow;
	if (step) {
		if (held_arrow != Key::NONE && held_sent) {
			_send_key(held_arrow, false);
		}
		held_arrow = want;
		held_sent = false;
		arrow_repeat_in = ARROW_FIRST_REPEAT;
	} else if (held_arrow != Key::NONE) {
		arrow_repeat_in -= p_delta;
		if (arrow_repeat_in <= 0) {
			arrow_repeat_in += ARROW_REPEAT;
			step = true;
		}
	}
	if (!step || held_arrow == Key::NONE) {
		return;
	}
	// Properties go row by row; elsewhere the arrow key does what it does there.
	if ((held_arrow == Key::UP || held_arrow == Key::DOWN) && focus && !_is_typing(focus) && _inspector_step(focus, held_arrow == Key::DOWN ? 1 : -1)) {
		return;
	}
	Control *any_focus = focus;
	if (Object::cast_to<TextEdit>(any_focus) && !_is_typing(any_focus)) {
		// A many-line field not typed in: the d-pad leaves it, as from a button.
		const Side side = held_arrow == Key::UP ? SIDE_TOP : (held_arrow == Key::DOWN ? SIDE_BOTTOM : (held_arrow == Key::LEFT ? SIDE_LEFT : SIDE_RIGHT));
		if (Control *next = any_focus->find_valid_focus_neighbor(side)) {
			next->grab_focus();
		}
		return;
	}
	arrow_from = area ? focus->get_instance_id() : ObjectID();
	arrow_area = area ? area->get_instance_id() : ObjectID();
	_send_key(held_arrow, true, held_sent);
	held_sent = true;
}

// The panel p_control is in: a dock's page, the bottom panel's page, or a 3D view.
Control *EditorGamepad::_area_of(Control *p_control) const {
	for (Node *n = p_control; n; n = n->get_parent()) {
		Control *c = Object::cast_to<Control>(n);
		if (!c || Object::cast_to<Window>(n)) {
			return nullptr;
		}
		if (Object::cast_to<DockTabContainer>(c->get_parent()) || Object::cast_to<Node3DEditorViewport>(c->get_parent())) {
			return c;
		}
	}
	return nullptr;
}

// Up and down in Properties go row by row, in the order they are listed (a section's header, then
// what is in it), not by where the nearest box is: a section's box holds its rows, so that would
// skip them or stop.
bool EditorGamepad::_inspector_step(Control *p_focus, int p_dir) {
	EditorInspector *inspector = nullptr;
	for (Node *n = p_focus; n && !inspector; n = n->get_parent()) {
		inspector = Object::cast_to<EditorInspector>(n);
	}
	if (!inspector) {
		return false;
	}
	LocalVector<Control *> order;
	LocalVector<Node *> stack;
	stack.push_back(inspector);
	while (!stack.is_empty()) {
		Node *n = stack[stack.size() - 1];
		stack.remove_at(stack.size() - 1);
		Control *c = Object::cast_to<Control>(n);
		if (c && !c->is_visible_in_tree()) {
			continue;
		}
		if (c && c != inspector && c->get_focus_mode_with_override() == Control::FOCUS_ALL) {
			order.push_back(c);
		}
		for (int i = n->get_child_count() - 1; i >= 0; i--) {
			stack.push_back(n->get_child(i));
		}
	}
	int at = -1;
	for (uint32_t i = 0; i < order.size(); i++) {
		if (order[i] == p_focus || order[i]->is_ancestor_of(p_focus)) {
			at = i;
		}
	}
	if (at < 0) {
		if (p_focus == inspector && !order.is_empty()) {
			order[0]->grab_focus(); // Properties itself has focus (Tab in a dialog): its first row.
			return true;
		}
		return false;
	}
	const real_t top = order[at]->get_global_rect().position.y;
	const real_t slack = 2 * EDSCALE;
	int to = -1;
	if (p_dir > 0) {
		for (int i = at + 1; i < (int)order.size() && to < 0; i++) {
			if (order[i]->get_global_rect().position.y > top + slack) {
				to = i;
			}
		}
	} else {
		for (int i = at - 1; i >= 0 && to < 0; i--) {
			if (order[i]->get_global_rect().position.y < top - slack) {
				to = i;
			}
		}
		// The first (left) one of that row.
		while (to > 0 && Math::abs(order[to - 1]->get_global_rect().position.y - order[to]->get_global_rect().position.y) <= slack) {
			to--;
		}
	}
	if (to >= 0) {
		order[to]->grab_focus();
	}
	return true; // At the first or last row it stays.
}

// The d-pad stays in its panel: at the end of Properties it does not jump to the bottom panel.
void EditorGamepad::_keep_in_area() {
	Control *from = ObjectDB::get_instance<Control>(arrow_from);
	Control *area = ObjectDB::get_instance<Control>(arrow_area);
	arrow_from = ObjectID();
	if (!from || !area || !from->is_visible_in_tree() || from->get_window() != get_tree()->get_root()) {
		return;
	}
	Control *now = get_tree()->get_root()->gui_get_focus_owner();
	if (now && now != from && now->get_window() == from->get_window() && _area_of(now) != area) {
		from->grab_focus();
	}
}

// The right stick scrolls what has focus, as the mouse wheel would over it.
void EditorGamepad::_scroll(Control *p_focus, double p_delta) {
	if (!p_focus || right_stick.is_zero_approx()) {
		scroll_left = 0;
		return;
	}
	const bool vertical = Math::abs(right_stick.y) >= Math::abs(right_stick.x);
	const float amount = vertical ? right_stick.y : right_stick.x;
	scroll_left += Math::abs(amount) * SCROLLS_PER_SECOND * p_delta;
	if (scroll_left < 1.0) {
		return;
	}
	const float factor = Math::floor(scroll_left);
	scroll_left -= factor;
	MouseButton button;
	if (vertical) {
		button = amount < 0 ? MouseButton::WHEEL_UP : MouseButton::WHEEL_DOWN;
	} else {
		button = amount < 0 ? MouseButton::WHEEL_LEFT : MouseButton::WHEEL_RIGHT;
	}
	_send_mouse_button(p_focus, p_focus->get_size() / 2, button, true, factor);
	_send_mouse_button(p_focus, p_focus->get_size() / 2, button, false, factor);
}

void EditorGamepad::_release_arrow() {
	if (held_arrow != Key::NONE && held_sent) {
		_send_key(held_arrow, false);
	}
	held_arrow = Key::NONE;
	held_sent = false;
}

EditorSpinSlider *EditorGamepad::_adjusting() const {
	return adjusting.is_valid() ? ObjectDB::get_instance<EditorSpinSlider>(adjusting) : nullptr;
}

// A on a number field: the d-pad changes it, as dragging it with the mouse would.
void EditorGamepad::_start_adjust(EditorSpinSlider *p_slider) {
	_release_arrow();
	adjusting = p_slider->get_instance_id();
	adjust_original = p_slider->get_value();
	adjust_dir = 0;
	adjust_held = 0.0;
	p_slider->set_pad_adjusting(true);
}

void EditorGamepad::_end_adjust(bool p_keep) {
	EditorSpinSlider *slider = _adjusting();
	adjusting = ObjectID();
	if (!slider) {
		return;
	}
	slider->set_pad_adjusting(false);
	if (!p_keep && slider->get_value() != adjust_original) {
		slider->set_value(adjust_original); // The inspector merges this into the same undo step.
	}
}

void EditorGamepad::_update_adjust(EditorSpinSlider *p_slider, double p_delta) {
	int dir = 0;
	if (down[PAD_LEFT] || left_stick.x < -0.5) {
		dir = -1;
	} else if (down[PAD_RIGHT] || left_stick.x > 0.5) {
		dir = 1;
	}
	if (dir != adjust_dir) {
		adjust_dir = dir;
		adjust_held = 0.0;
		adjust_repeat_in = ADJUST_FIRST_REPEAT;
		if (dir == 0) {
			return;
		}
	} else if (dir == 0) {
		return;
	} else {
		adjust_held += p_delta;
		adjust_repeat_in -= p_delta;
		if (adjust_repeat_in > 0) {
			return;
		}
		adjust_repeat_in += ADJUST_REPEAT;
	}

	// The same step as the up and down keys in the typed field: whole numbers for a fine float.
	double step = p_slider->get_step();
	if (step <= 0) {
		step = EDITOR_GET("interface/inspector/default_float_step");
	}
	if (step < 1 && Math::is_equal_approx(1.0 / step, Math::round(1.0 / step))) {
		step = 1.0;
	}
	// A short range (0 to 1, say) goes in a hundred steps instead.
	const double range = p_slider->get_max() - p_slider->get_min();
	if (range > 0 && step > range / 20) {
		step = MAX(p_slider->get_step(), range / 100);
	}
	if (down[PAD_LT]) {
		step *= 0.1;
	} else if (down[PAD_RT] || adjust_held > ADJUST_SPEED_UP) {
		step *= 10.0;
	}
	p_slider->set_value(p_slider->get_value() + dir * step);
}

// Puts focus on the first thing in p_area a pad can use: a list first, then anything but a text
// field (focusing a text field starts typing), then anything.
void EditorGamepad::_focus_into(Control *p_area) {
	Control *first_list = nullptr;
	Control *first_other = nullptr;
	Control *first_any = nullptr;
	LocalVector<Node *> stack;
	stack.push_back(p_area);
	while (!stack.is_empty() && !first_list) {
		Node *n = stack[0];
		stack.remove_at(0);
		Control *c = Object::cast_to<Control>(n);
		if (c && !c->is_visible_in_tree()) {
			continue;
		}
		if (EditorInspector *inspector = Object::cast_to<EditorInspector>(n)) {
			// Properties: its first field, not the buttons above it.
			LocalVector<Node *> inner;
			inner.push_back(inspector);
			for (uint32_t i = 0; i < inner.size() && !first_list; i++) {
				Control *ic = Object::cast_to<Control>(inner[i]);
				if (ic && !ic->is_visible_in_tree()) {
					continue;
				}
				if (ic && ic != inspector && ic->get_focus_mode_with_override() == Control::FOCUS_ALL) {
					first_list = ic;
				}
				for (int j = 0; j < inner[i]->get_child_count(); j++) {
					inner.push_back(inner[i]->get_child(j));
				}
			}
			continue;
		}
		if (c && c->get_focus_mode_with_override() == Control::FOCUS_ALL) {
			if (Object::cast_to<Tree>(c) || Object::cast_to<ItemList>(c) || Object::cast_to<Node3DEditorViewport>(c->get_parent())) {
				first_list = c;
			} else if (!first_other && !is_text_field(c)) {
				first_other = c;
			} else if (!first_any) {
				first_any = c;
			}
		}
		for (int i = 0; i < n->get_child_count(); i++) {
			stack.push_back(n->get_child(i));
		}
	}
	Control *target = first_list ? first_list : (first_other ? first_other : first_any);
	if (target) {
		target->grab_focus();
	}
}

// A one-line field types once it is being edited; a many-line one (which has no such mode, and
// would eat the d-pad) once A was pressed on it.
bool EditorGamepad::_is_typing(const Control *p_control) const {
	const LineEdit *le = Object::cast_to<LineEdit>(p_control);
	const TextEdit *te = Object::cast_to<TextEdit>(p_control);
	return (le && le->is_editing()) || (te && te->is_editable() && te->get_instance_id() == typing_in);
}

Control *EditorGamepad::_find_list(Node *p_area) const {
	LocalVector<Node *> stack;
	stack.push_back(p_area);
	for (uint32_t i = 0; i < stack.size(); i++) {
		Node *n = stack[i];
		Control *c = Object::cast_to<Control>(n);
		if (c && !c->is_visible_in_tree()) {
			continue;
		}
		if (c && c->get_focus_mode_with_override() == Control::FOCUS_ALL && (Object::cast_to<Tree>(c) || Object::cast_to<ItemList>(c))) {
			return c;
		}
		for (int j = 0; j < n->get_child_count(); j++) {
			stack.push_back(n->get_child(j));
		}
	}
	return nullptr;
}

// A dialog that opens with a text field in focus would have the pad typing in it: its list (files,
// sections) gets the focus instead, or the field waits for A.
void EditorGamepad::_window_opened(Window *p_window) {
	Control *focus = p_window->gui_get_focus_owner();
	if (focus && !is_text_field(focus)) {
		return;
	}
	if (Control *list = _find_list(p_window)) {
		list->grab_focus();
		return;
	}
	if (!Object::cast_to<AcceptDialog>(p_window)) {
		// A popup that is only a field (renaming a row): type in it straight away.
		if (focus && _is_typing(focus)) {
			_open_steam_keyboard();
		}
		return;
	}
	if (LineEdit *le = Object::cast_to<LineEdit>(focus)) {
		le->unedit();
	} else if (!focus) {
		_focus_anything(p_window);
	}
}

bool EditorGamepad::_focus_anything(Window *p_window) {
	if (p_window == get_tree()->get_root()) {
		_next_area(1);
	} else {
		for (int i = 0; i < p_window->get_child_count() && !p_window->gui_get_focus_owner(); i++) {
			if (Control *c = Object::cast_to<Control>(p_window->get_child(i))) {
				_focus_into(c);
			}
		}
	}
	return p_window->gui_get_focus_owner() != nullptr;
}

// View goes to the next panel: each open dock (Level list, Properties, ...), the 3D view and the
// bottom panel, left to right.
void EditorGamepad::_next_area(int p_step) {
	LocalVector<Control *> areas;
	Control *bottom = nullptr;
	TypedArray<Node> tabs = get_tree()->get_root()->find_children("*", "DockTabContainer", true, false);
	for (int i = 0; i < tabs.size(); i++) {
		DockTabContainer *dtc = Object::cast_to<DockTabContainer>(tabs[i]);
		Control *current = dtc ? dtc->get_current_tab_control() : nullptr;
		if (!current || !current->is_visible_in_tree()) {
			continue;
		}
		if (Object::cast_to<EditorBottomPanel>(dtc)) {
			bottom = current;
		} else {
			areas.push_back(current);
		}
	}
	if (Node3DEditor *editor = Node3DEditor::get_singleton()) {
		for (uint32_t i = 0; i < Node3DEditor::VIEWPORTS_COUNT; i++) {
			Node3DEditorViewport *view = editor->get_editor_viewport(i);
			if (view && view->get_surface()->is_visible_in_tree()) {
				areas.push_back(view->get_surface());
			}
		}
	}
	// Buttons that ask for it, such as Play Level on the toolbar.
	for (Node *n : get_tree()->get_nodes_in_group(SNAME("editor_gamepad_area"))) {
		Control *c = Object::cast_to<Control>(n);
		if (c && c->is_visible_in_tree() && c->get_window() == get_tree()->get_root()) {
			areas.push_back(c);
		}
	}
	areas.sort_custom<AreaOrder>();
	if (bottom) {
		areas.push_back(bottom);
	}
	if (areas.is_empty()) {
		return;
	}

	Control *focus = get_tree()->get_root()->gui_get_focus_owner();
	int current = -1;
	for (uint32_t i = 0; i < areas.size() && focus; i++) {
		if (areas[i] == focus || areas[i]->is_ancestor_of(focus)) {
			current = i;
			break;
		}
	}
	const int n = areas.size();
	int next = current < 0 ? (p_step > 0 ? 0 : n - 1) : (current + p_step + n) % n;
	// A panel with nothing to focus (only a message) is passed over.
	for (int tries = 0; tries < n; tries++, next = (next + p_step + n) % n) {
		_focus_into(areas[next]);
		Control *now = get_tree()->get_root()->gui_get_focus_owner();
		if (now && (areas[next] == now || areas[next]->is_ancestor_of(now))) {
			return;
		}
	}
}

// LB/RB switch the tabs around what has focus (docks, Settings pages, and so on).
void EditorGamepad::_switch_tab(Control *p_focus, int p_step) {
	for (Node *n = p_focus; n; n = n->get_parent()) {
		if (TabContainer *tc = Object::cast_to<TabContainer>(n)) {
			if (tc->get_tab_count() < 2 || !tc->is_visible_in_tree()) {
				continue;
			}
			bool moved = p_step > 0 ? tc->select_next_available() : tc->select_previous_available();
			if (!moved) {
				// Round again from the other end.
				const int from = tc->get_current_tab();
				tc->set_current_tab(p_step > 0 ? 0 : tc->get_tab_count() - 1);
				if (tc->is_tab_disabled(tc->get_current_tab()) || tc->is_tab_hidden(tc->get_current_tab())) {
					p_step > 0 ? tc->select_next_available() : tc->select_previous_available();
				}
				moved = tc->get_current_tab() != from;
			}
			if (moved && tc->get_current_tab_control()) {
				_focus_into(tc->get_current_tab_control());
			}
			return;
		}
		if (TabBar *tb = Object::cast_to<TabBar>(n)) {
			if (tb->get_tab_count() >= 2) {
				if (!(p_step > 0 ? tb->select_next_available() : tb->select_previous_available())) {
					tb->set_current_tab(p_step > 0 ? 0 : tb->get_tab_count() - 1);
				}
				return;
			}
		}
		if (Object::cast_to<Window>(n)) {
			break;
		}
	}
	// No tabs around: look for a tab bar in the same window (a dialog with pages).
	Window *w = p_focus ? p_focus->get_window() : nullptr;
	if (!w) {
		return;
	}
	TypedArray<Node> bars = w->find_children("*", "TabBar", true, false);
	for (int i = 0; i < bars.size(); i++) {
		TabBar *tb = Object::cast_to<TabBar>(bars[i]);
		if (tb && tb->is_visible_in_tree() && tb->get_tab_count() >= 2 && tb->get_window() == w) {
			p_step > 0 ? tb->select_next_available() : tb->select_previous_available();
			return;
		}
	}
}

void EditorGamepad::_open_main_menu() {
	EditorNode::get_singleton()->open_main_menu();
}

// Y is the right mouse button: lists and text fields have their own menu key, the rest get a right
// click in their middle.
void EditorGamepad::_context_menu(Control *p_focus) {
	if (!p_focus) {
		return;
	}
	if (Object::cast_to<Tree>(p_focus) || Object::cast_to<ItemList>(p_focus) || Object::cast_to<RichTextLabel>(p_focus) || is_text_field(p_focus)) {
		_tap_key(Key::MENU);
		return;
	}
	_click(p_focus, p_focus->get_size() / 2, MouseButton::RIGHT);
}

void EditorGamepad::_open_steam_keyboard() {
	OS *os = OS::get_singleton();
	if (os->has_environment("SteamDeck") || os->has_environment("SteamAppId") || os->has_environment("SteamGameId")) {
		os->shell_open("steam://open/keyboard");
	}
}

LocalVector<Node3D *> EditorGamepad::_selected_3d() const {
	LocalVector<Node3D *> nodes;
	EditorSelection *selection = EditorNode::get_singleton()->get_editor_selection();
	Node *scene = EditorNode::get_singleton()->get_edited_scene();
	for (Node *n : selection->get_top_selected_node_list()) {
		Node3D *n3d = Object::cast_to<Node3D>(n);
		if (n3d && n3d != scene && n3d->is_inside_tree()) {
			nodes.push_back(n3d);
		}
	}
	return nodes;
}

void EditorGamepad::_start_carry(Node3DEditorViewport *p_view) {
	carried.clear();
	for (Node3D *n : _selected_3d()) {
		carried.push_back({ n->get_instance_id(), n->get_global_transform() });
	}
	if (!carried.is_empty()) {
		carry_view = p_view;
		carry_start_view = p_view->get_view_transform();
	}
}

void EditorGamepad::_end_carry(bool p_keep) {
	if (carried.is_empty()) {
		return;
	}
	EditorUndoRedoManager *ur = EditorUndoRedoManager::get_singleton();
	bool any = false;
	for (const Carried &c : carried) {
		Node3D *n = ObjectDB::get_instance<Node3D>(c.id);
		if (!n) {
			continue;
		}
		if (!p_keep) {
			n->set_global_transform(c.original);
			continue;
		}
		if (!any) {
			ur->create_action(TTR("Move Objects"));
			any = true;
		}
		ur->add_do_method(n, "set_global_transform", n->get_global_transform());
		ur->add_undo_method(n, "set_global_transform", c.original);
	}
	if (any) {
		ur->commit_action(false);
	}
	carried.clear();
	carry_view = nullptr;
}

void EditorGamepad::_turn_selection(float p_degrees) {
	LocalVector<Node3D *> nodes = _selected_3d();
	if (nodes.is_empty()) {
		return;
	}
	EditorUndoRedoManager *ur = EditorUndoRedoManager::get_singleton();
	ur->create_action(TTR("Turn Objects"));
	const Basis turn(Vector3(0, 1, 0), Math::deg_to_rad(p_degrees));
	for (Node3D *n : nodes) {
		Transform3D t = n->get_global_transform();
		ur->add_undo_method(n, "set_global_transform", t);
		t.basis = turn * t.basis;
		ur->add_do_method(n, "set_global_transform", t);
	}
	ur->commit_action();
}

void EditorGamepad::_pointer_event(Window *p_native, const Vector2 &p_relative, MouseButton p_button, bool p_pressed) {
	const Vector2 local = pointer - Vector2(p_native->get_position());
	Input *input = Input::get_singleton();
	if (p_button == MouseButton::NONE) {
		Ref<InputEventMouseMotion> mm;
		mm.instantiate();
		mm->set_meta(SNAME("openrac_gamepad"), true);
		mm->set_window_id(p_native->get_window_id());
		mm->set_position(local);
		mm->set_global_position(local);
		mm->set_relative(p_relative);
		mm->set_relative_screen_position(p_relative);
		mm->set_button_mask(input->get_mouse_button_mask());
		input->parse_input_event(mm);
		// And the system's pointer, so it is drawn where it is.
		if (DisplayServer::get_singleton()->has_feature(DisplayServerEnums::FEATURE_MOUSE_WARP)) {
			DisplayServer::get_singleton()->warp_mouse(local);
		}
		return;
	}
	Ref<InputEventMouseButton> mb;
	mb.instantiate();
	mb->set_meta(SNAME("openrac_gamepad"), true);
	mb->set_window_id(p_native->get_window_id());
	mb->set_position(local);
	mb->set_global_position(local);
	mb->set_button_index(p_button);
	mb->set_pressed(p_pressed);
	BitField<MouseButtonMask> mask = input->get_mouse_button_mask();
	if (p_pressed) {
		mask.set_flag(mouse_button_to_mask(p_button));
	} else {
		mask.clear_flag(mouse_button_to_mask(p_button));
	}
	mb->set_button_mask(mask);
	input->parse_input_event(mb);
}

void EditorGamepad::_pointer_wheel(Window *p_native, MouseButton p_button, float p_factor) {
	const Vector2 local = pointer - Vector2(p_native->get_position());
	for (int pressed = 1; pressed >= 0; pressed--) {
		Ref<InputEventMouseButton> mb;
		mb.instantiate();
		mb->set_meta(SNAME("openrac_gamepad"), true);
		mb->set_window_id(p_native->get_window_id());
		mb->set_position(local);
		mb->set_global_position(local);
		mb->set_button_index(p_button);
		mb->set_factor(p_factor);
		mb->set_pressed(pressed);
		mb->set_button_mask(Input::get_singleton()->get_mouse_button_mask());
		Input::get_singleton()->parse_input_event(mb);
	}
}

// The Steam Deck's two touchpads, as on a laptop: the right one is the mouse (press it to click,
// hold it pressed to drag), the left one scrolls what is under the pointer (or turns the 3D view)
// and a press on it is a right click.
void EditorGamepad::_update_touchpads(Window *p_window) {
	Window *native = _native_window(p_window);
	const Size2 size = native->get_size();

	for (int side = 0; side < 2; side++) {
		const Button touched = side == 0 ? PAD_LEFT_TOUCH : PAD_RIGHT_TOUCH;
		if (_pressed(touched)) {
			last_touch[side] = touch[side];
			if (side == 1 || !down[PAD_RIGHT_TOUCH]) {
				// Start from where the mouse is now.
				pointer = Vector2(native->get_position()) + native->get_mouse_position();
			}
		}
		if (!down[touched]) {
			continue;
		}
		const Vector2 moved = touch[side] - last_touch[side];
		last_touch[side] = touch[side];
		if (moved.is_zero_approx()) {
			continue;
		}
		touch_quiet_frames = 3;
		if (side == 1) {
			// Faster swipes go further, as with a mouse.
			const float speed = MIN(1.0f, moved.length() / 0.1f);
			const Vector2 px = moved * 0.5 * size.x * POINTER_SPEED * (1.0 + (POINTER_FAST - 1.0) * speed);
			const Vector2 before = pointer;
			const Rect2 rect(Vector2(native->get_position()), size - Size2(1, 1));
			pointer = (pointer + px).clamp(rect.position, rect.get_end());
			_pointer_event(native, pointer - before, MouseButton::NONE, false);
			continue;
		}
		Node3DEditorViewport *view = _3d_view_at(pointer);
		if (view) {
			view->gamepad_fly(Vector3(), moved * 0.5 * TOUCH_LOOK_DEGREES * 2, 0);
			continue;
		}
		// Content follows the finger: a finger going up shows what is further down.
		scroll_touch += moved * 0.5 * SCROLL_NOTCHES * 2;
		for (int axis = 0; axis < 2; axis++) {
			const float amount = scroll_touch[axis];
			if (Math::abs(amount) < 1.0) {
				continue;
			}
			const float notches = Math::floor(Math::abs(amount));
			scroll_touch[axis] -= SIGN(amount) * notches;
			MouseButton wheel;
			if (axis == 1) {
				wheel = amount < 0 ? MouseButton::WHEEL_DOWN : MouseButton::WHEEL_UP;
			} else {
				wheel = amount < 0 ? MouseButton::WHEEL_RIGHT : MouseButton::WHEEL_LEFT;
			}
			_pointer_wheel(native, wheel, notches);
		}
	}
	if (!down[PAD_LEFT_TOUCH]) {
		scroll_touch = Vector2();
	}

	// Presses: a left click, held for a drag, and a right click.
	if (_pressed(PAD_RIGHT_PRESS) || _released(PAD_RIGHT_PRESS)) {
		if (_pressed(PAD_RIGHT_PRESS) && !down[PAD_RIGHT_TOUCH]) {
			pointer = Vector2(native->get_position()) + native->get_mouse_position();
		}
		_pointer_event(native, Vector2(), MouseButton::LEFT, down[PAD_RIGHT_PRESS]);
	}
	if (_pressed(PAD_LEFT_PRESS) || _released(PAD_LEFT_PRESS)) {
		if (!down[PAD_RIGHT_TOUCH]) {
			pointer = Vector2(native->get_position()) + native->get_mouse_position();
		}
		_pointer_event(native, Vector2(), MouseButton::RIGHT, down[PAD_LEFT_PRESS]);
	}
	if (touch_quiet_frames > 0) {
		touch_quiet_frames--;
	}
}

// In the 3D view: the left stick flies, the right stick looks, LT/RT go down and up, A picks what
// is under the cross, holding X carries the picked objects along, LB/RB turn them, Y looks at them.
void EditorGamepad::_update_3d(Node3DEditorViewport *p_view, double p_delta) {
	const float fast = down[PAD_LEFT_STICK] ? FAST_FLY : 1.0f;
	const Vector3 move(left_stick.x, right_trigger - left_trigger, -left_stick.y);
	p_view->gamepad_fly(move * fast, right_stick * LOOK_DEGREES_PER_SECOND * p_delta, p_delta);

	if (!carried.is_empty() && carry_view == p_view) {
		// Move the carried objects as if fixed to the view: along with it, and turning with it.
		const Transform3D now = p_view->get_view_transform();
		const Vector3 f0 = carry_start_view.basis.xform(Vector3(0, 0, -1));
		const Vector3 f1 = now.basis.xform(Vector3(0, 0, -1));
		const real_t turn = Math::atan2(f1.x, f1.z) - Math::atan2(f0.x, f0.z);
		const Transform3D carry = Transform3D(Basis(Vector3(0, 1, 0), turn), now.origin) * Transform3D(Basis(), -carry_start_view.origin);
		for (const Carried &c : carried) {
			if (Node3D *n = ObjectDB::get_instance<Node3D>(c.id)) {
				n->set_global_transform(carry * c.original);
			}
		}
	}

	if (_pressed(PAD_A)) {
		_click(p_view->get_surface(), p_view->get_surface()->get_size() / 2, MouseButton::LEFT);
	}
	if (_pressed(PAD_X)) {
		_start_carry(p_view);
	}
	if (_released(PAD_X)) {
		_end_carry(true);
	}
	if (_pressed(PAD_B)) {
		if (!carried.is_empty()) {
			_end_carry(false); // Put them back.
		} else {
			EditorNode::get_singleton()->get_editor_selection()->clear();
		}
	}
	if (_pressed(PAD_LB)) {
		_turn_selection(TURN_DEGREES);
	}
	if (_pressed(PAD_RB)) {
		_turn_selection(-TURN_DEGREES);
	}
	if (_pressed(PAD_Y)) {
		p_view->focus_selection();
	}
}

void EditorGamepad::_set_active(bool p_active) {
	if (active == p_active) {
		return;
	}
	active = p_active;
	if (bar) {
		bar->set_visible(active);
	}
	// Menus without "Ctrl+A" and the like, and the eye (and other buttons) in list rows in reach.
	PopupMenu::set_keys_hidden(active);
	Tree::set_keys_reach_buttons(active);
	if (!active) {
		_end_adjust(true);
		if (Node3DEditor *editor = Node3DEditor::get_singleton()) {
			for (uint32_t i = 0; i < Node3DEditor::VIEWPORTS_COUNT; i++) {
				if (Node3DEditorViewport *view = editor->get_editor_viewport(i)) {
					view->set_gamepad_aim(false);
				}
			}
		}
	}
}

// The bottom bar says what the buttons do where the focus is.
void EditorGamepad::_update_hint(Control *p_focus, Node3DEditorViewport *p_view) {
	if (!bar) {
		return;
	}
	Vector<String> items;
	if (_adjusting()) {
		items = { "D-pad", TTR("Change"), "LT", TTR("Hold: fine"), "RT", TTR("Hold: fast"), "A", TTR("Done"), "B", TTR("Cancel"), "X", TTR("Type") };
	} else if (touched_last) {
		items = { TTR("Right pad"), TTR("Point, press to click, hold to drag"), TTR("Left pad"), p_view ? TTR("Look, press for more") : TTR("Scroll, press for more") };
	} else if (p_view) {
		items = { "L", TTR("Fly"), "R", TTR("Look"), "LT+RT", TTR("Down, up"), "A", TTR("Pick"), "X", TTR("Hold: carry"), "LB+RB", TTR("Turn"), "Y", TTR("Look at it"), "B", TTR("Drop pick"), "View", TTR("Next panel") };
	} else if (is_text_field(p_focus) && _is_typing(p_focus)) {
		items = { "A", TTR("Done"), "B", TTR("Cancel"), "X", TTR("Keyboard"), "D-pad", TTR("Leave"), "View", TTR("Next field") };
	} else if (is_text_field(p_focus)) {
		items = { "A", TTR("Type"), "B", TTR("Back"), "Y", TTR("More"), "View", TTR("Next field"), "Start", TTR("Menu") };
	} else if (Object::cast_to<EditorSpinSlider>(p_focus)) {
		items = { "A", TTR("Change"), "B", TTR("Back"), "Y", TTR("More"), "X", TTR("Undo"), "LT+X", TTR("Redo"), "View", TTR("Next panel"), "Start", TTR("Menu") };
	} else {
		items = { "A", TTR("Choose"), "B", TTR("Back"), "Y", TTR("More"), "X", TTR("Undo"), "LT+X", TTR("Redo"), "LB+RB", TTR("Tabs"), "R", TTR("Scroll"), "View", TTR("Next panel"), "Start", TTR("Menu") };
	}
	bar->set_items(items);
}

void EditorGamepad::input(const Ref<InputEvent> &p_event) {
	if (Object::cast_to<InputEventJoypadButton>(*p_event) || Object::cast_to<InputEventJoypadMotion>(*p_event)) {
		pads_seen.insert(p_event->get_device());
		return;
	}
	// The mouse or the keyboard takes over again: hide the pad's help. (Not the pointer the right
	// touchpad moves: the system says that motion too.)
	if (!active || p_event->has_meta(SNAME("openrac_gamepad")) || touch_quiet_frames > 0 || down[PAD_RIGHT_TOUCH]) {
		return;
	}
	Ref<InputEventMouseMotion> mm = p_event;
	if ((mm.is_valid() && mm->get_relative().length() > 2) || Object::cast_to<InputEventMouseButton>(*p_event) || (Object::cast_to<InputEventKey>(*p_event) && p_event->is_pressed())) {
		_set_active(false);
	}
}

void EditorGamepad::_notification(int p_what) {
	switch (p_what) {
		case NOTIFICATION_READY: {
			// The pad is read here, not through the input actions: drop the actions' pad buttons
			// so a press is not seen twice (and the d-pad does not move every window at once).
			InputMap *map = InputMap::get_singleton();
			for (const Variant &name : map->get_actions()) {
				const StringName action = name;
				const List<Ref<InputEvent>> *events = map->action_get_events(action);
				if (!events) {
					continue;
				}
				LocalVector<Ref<InputEvent>> pad_events;
				for (const Ref<InputEvent> &e : *events) {
					if (Object::cast_to<InputEventJoypadButton>(*e) || Object::cast_to<InputEventJoypadMotion>(*e)) {
						pad_events.push_back(e);
					}
				}
				for (const Ref<InputEvent> &e : pad_events) {
					map->action_erase_event(action, e);
				}
			}
			set_process(true);
			set_process_input(true);
		} break;

		case NOTIFICATION_PROCESS: {
			_read_pad();
			const double delta = get_process_delta_time();
			Window *window = _active_window();
			bool any = !left_stick.is_zero_approx() || !right_stick.is_zero_approx() || left_trigger > 0 || right_trigger > 0;
			for (int i = 0; i < PAD_MAX && !any; i++) {
				any = down[i];
			}
			if (!window) {
				// The editor is in the background: let go of everything.
				_update_arrows(delta, false);
				_end_carry(true);
				_end_adjust(true);
				return;
			}
			if (any) {
				_set_active(true);
			}

			Control *focus = window->gui_get_focus_owner();
			bool pressed_any = false;
			for (int i = 0; i < PAD_MAX && !pressed_any; i++) {
				const bool touchpad = i >= PAD_LEFT_TOUCH && i <= PAD_RIGHT_PRESS;
				pressed_any = !touchpad && _pressed(Button(i));
			}
			if (pressed_any || !left_stick.is_zero_approx() || !right_stick.is_zero_approx()) {
				touched_last = false;
			} else if (down[PAD_LEFT_TOUCH] || down[PAD_RIGHT_TOUCH]) {
				touched_last = true;
			}
			_update_touchpads(window);
			if (window->get_instance_id() != last_window) {
				last_window = window->get_instance_id();
				if (active && window != get_tree()->get_root() && !Object::cast_to<PopupMenu>(window)) {
					_window_opened(window);
					focus = window->gui_get_focus_owner();
				}
			}
			// Up or down leaves a one-line field being typed in, as from any other row.
			if (LineEdit *le = Object::cast_to<LineEdit>(focus)) {
				if (le->is_editing() && (_pressed(PAD_UP) || _pressed(PAD_DOWN))) {
					le->unedit();
				}
			}
			// A menu takes keys without focus; elsewhere the first press only shows where the focus is.
			if (pressed_any && !focus && !Object::cast_to<PopupMenu>(window) && _focus_anything(window)) {
				return;
			}

			Node3DEditorViewport *view = window == get_tree()->get_root() ? _focused_3d_view() : nullptr;
			if (!view && !carried.is_empty()) {
				_end_carry(true);
			}
			if (Node3DEditor *editor = Node3DEditor::get_singleton()) {
				for (uint32_t i = 0; i < Node3DEditor::VIEWPORTS_COUNT; i++) {
					if (Node3DEditorViewport *v = editor->get_editor_viewport(i)) {
						v->set_gamepad_aim(active && v == view);
					}
				}
			}

			EditorSpinSlider *slider = _adjusting();
			if (slider && (slider != focus || view)) {
				_end_adjust(true);
				slider = nullptr;
			}
			if (slider) {
				_release_arrow();
				_update_adjust(slider, delta);
			} else {
				_update_arrows(delta, view == nullptr);
			}
			if (view) {
				_update_3d(view, delta);
			} else if (slider) {
				if (_pressed(PAD_A)) {
					_end_adjust(true);
				} else if (_pressed(PAD_B)) {
					_end_adjust(false);
				} else if (_pressed(PAD_X)) {
					_end_adjust(true);
					slider->setup_and_show(); // Type the number.
					keyboard_check_frames = 3;
				}
			} else {
				_scroll(focus, delta);
				EditorSpinSlider *number = Object::cast_to<EditorSpinSlider>(focus);
				TextEdit *lines = Object::cast_to<TextEdit>(focus);
				if (typing_in.is_valid() && (!lines || lines->get_instance_id() != typing_in)) {
					typing_in = ObjectID(); // Focus left the field typed in.
				}
				if (_pressed(PAD_A) && number && !number->is_read_only()) {
					_start_adjust(number);
				} else if (_pressed(PAD_A) && lines && !_is_typing(lines)) {
					if (lines->is_editable()) {
						typing_in = lines->get_instance_id();
						_open_steam_keyboard();
					}
				} else if (_pressed(PAD_A) && !lines) {
					_tap_key(Key::ENTER);
					keyboard_check_frames = 3;
				}
				if (_pressed(PAD_B) && lines && _is_typing(lines)) {
					typing_in = ObjectID(); // Stop typing, stay on the field.
				} else if (_pressed(PAD_B)) {
					_tap_key(Key::ESCAPE);
				}
				if (_pressed(PAD_X)) {
					if (is_text_field(focus)) {
						_open_steam_keyboard();
					} else if (down[PAD_LT]) {
						EditorNode::get_singleton()->redo();
					} else {
						EditorNode::get_singleton()->undo();
					}
				}
				if (_pressed(PAD_Y)) {
					_context_menu(focus);
				}
				if (_pressed(PAD_LB)) {
					_switch_tab(focus, -1);
				}
				if (_pressed(PAD_RB)) {
					_switch_tab(focus, 1);
				}
			}
			if (_pressed(PAD_VIEW)) {
				if (window == get_tree()->get_root()) {
					_next_area(down[PAD_LT] ? -1 : 1);
				} else {
					_tap_key(Key::TAB); // In a dialog: the next field.
				}
			}
			if (_pressed(PAD_START)) {
				_open_main_menu();
			}

			// A field that A started typing in gets the Steam keyboard.
			if (keyboard_check_frames > 0 && --keyboard_check_frames == 0) {
				Control *now = _focus_owner();
				LineEdit *le = Object::cast_to<LineEdit>(now);
				if ((le && le->is_editing()) || Object::cast_to<TextEdit>(now)) {
					_open_steam_keyboard();
				}
			}
			if (active) {
				_update_hint(_focus_owner(), view);
			}
		} break;
	}
}

EditorGamepad::EditorGamepad() {
	set_name("EditorGamepad");
}

void EditorGamepadBar::set_items(const Vector<String> &p_pairs) {
	const String new_key = String("|").join(p_pairs);
	if (new_key == key) {
		return;
	}
	key = new_key;
	items.clear();
	for (int i = 0; i + 1 < p_pairs.size(); i += 2) {
		items.push_back({ p_pairs[i], p_pairs[i + 1] });
	}
	update_minimum_size();
	queue_redraw();
}

// The face buttons in their usual colours, the rest grey.
Color EditorGamepadBar::_button_colour(const String &p_button) const {
	if (p_button == "A") {
		return Color(0.36, 0.72, 0.29);
	}
	if (p_button == "B") {
		return Color(0.86, 0.27, 0.24);
	}
	if (p_button == "X") {
		return Color(0.24, 0.53, 0.87);
	}
	if (p_button == "Y") {
		return Color(0.93, 0.73, 0.16);
	}
	return Color(0.42, 0.42, 0.44);
}

Size2 EditorGamepadBar::get_minimum_size() const {
	return Size2(0, Math::round(30 * EDSCALE));
}

void EditorGamepadBar::_notification(int p_what) {
	if (p_what != NOTIFICATION_DRAW) {
		return;
	}
	const Ref<Font> bold = get_theme_font(SNAME("bold"), EditorStringName(EditorFonts));
	const Ref<Font> main = get_theme_font(SNAME("main"), EditorStringName(EditorFonts));
	const int size = get_theme_font_size(SNAME("main_size"), EditorStringName(EditorFonts));
	const Color text = get_theme_color(SNAME("font_color"), EditorStringName(Editor));
	draw_rect(Rect2(Vector2(), get_size()), get_theme_color(SNAME("dark_color_2"), EditorStringName(Editor)));
	draw_rect(Rect2(0, 0, get_size().x, Math::round(EDSCALE)), get_theme_color(SNAME("accent_color"), EditorStringName(Editor)) * Color(1, 1, 1, 0.6));

	const real_t h = get_size().y;
	const real_t glyph_h = Math::round(20 * EDSCALE);
	const real_t pad = Math::round(6 * EDSCALE);
	real_t x = Math::round(12 * EDSCALE);
	const real_t baseline = (h + main->get_ascent(size) - main->get_descent(size)) / 2;
	for (const Item &item : items) {
		// Each button of a pair ("LB+RB") as its own glyph.
		for (const String &name : item.button.split("+")) {
			const bool face = name.length() == 1 && String("ABXY").contains(name);
			const Size2 label_size = bold->get_string_size(name, HORIZONTAL_ALIGNMENT_LEFT, -1, size);
			const real_t w = face ? glyph_h : MAX(glyph_h, label_size.x + pad * 2);
			const Rect2 r(x, (h - glyph_h) / 2, w, glyph_h);
			if (face) {
				draw_circle(r.get_center(), glyph_h / 2, _button_colour(name), true, -1, true);
			} else {
				Ref<StyleBoxFlat> pill;
				pill.instantiate();
				pill->set_bg_color(_button_colour(name));
				pill->set_corner_radius_all(glyph_h / 2);
				pill->set_anti_aliased(true);
				draw_style_box(pill, r);
			}
			bold->draw_string(get_canvas_item(), Point2(r.position.x + (w - label_size.x) / 2, baseline), name, HORIZONTAL_ALIGNMENT_LEFT, -1, size, Color(1, 1, 1));
			x += w + Math::round(3 * EDSCALE);
		}
		x += Math::round(3 * EDSCALE);
		main->draw_string(get_canvas_item(), Point2(x, baseline), item.text, HORIZONTAL_ALIGNMENT_LEFT, -1, size, text);
		x += main->get_string_size(item.text, HORIZONTAL_ALIGNMENT_LEFT, -1, size).x + Math::round(18 * EDSCALE);
	}
}
