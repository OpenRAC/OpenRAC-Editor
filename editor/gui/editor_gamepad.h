/* SPDX-License-Identifier: MIT */
/* OpenRAC Editor: the editor works with a gamepad (Steam Deck, Steam Controller, any pad). */

#pragma once

#include "core/input/input_enums.h"
#include "core/math/transform_3d.h"
#include "core/os/keyboard.h"
#include "core/templates/hash_set.h"
#include "core/templates/local_vector.h"
#include "scene/main/node.h"

#include "scene/gui/control.h"

class Label;
class Node3D;
class EditorSpinSlider;
class Node3DEditorViewport;
class Window;

// OpenRAC Editor: the bar at the bottom of the editor that says, while the pad is used, what each
// button does where the focus is: each button drawn as on the pad, then its word.
class EditorGamepadBar : public Control {
	GDCLASS(EditorGamepadBar, Control);

	struct Item {
		String button;
		String text;
	};
	Vector<Item> items;
	String key;

	Color _button_colour(const String &p_button) const;

protected:
	void _notification(int p_what);

public:
	// Pairs of a button (A, B, X, Y, LB, View, ...; LB+RB for two) and what it does.
	void set_items(const Vector<String> &p_pairs);
	virtual Size2 get_minimum_size() const override;
};

// OpenRAC Editor: the whole editor works with a gamepad (Steam Deck, Steam Controller, any pad).
// It reads the pad every frame and acts like a keyboard (arrows, Enter, Escape), so every list,
// button, menu and field that works with keys works with the pad. On top of that: LB/RB switch
// tabs, View goes to the next panel, Start opens the menu, Y opens the right-click menu, the right
// stick scrolls, and in the 3D view the sticks fly the view and move the picked objects.
class EditorGamepad : public Node {
	GDCLASS(EditorGamepad, Node);

	enum Button {
		PAD_A,
		PAD_B,
		PAD_X,
		PAD_Y,
		PAD_LB,
		PAD_RB,
		PAD_VIEW,
		PAD_START,
		PAD_LEFT_STICK,
		PAD_UP,
		PAD_DOWN,
		PAD_LEFT,
		PAD_RIGHT,
		PAD_LEFT_TOUCH,
		PAD_RIGHT_TOUCH,
		PAD_LEFT_PRESS,
		PAD_RIGHT_PRESS,
		PAD_LT,
		PAD_RT,
		PAD_MAX,
	};

	HashSet<int> pads_seen; // Pads that sent input, connected or not (a test's pad).
	bool down[PAD_MAX] = {};
	bool was_down[PAD_MAX] = {};
	Vector2 left_stick;
	Vector2 right_stick;
	float left_trigger = 0.0;
	float right_trigger = 0.0;

	// The touchpads (0 left, 1 right): where the finger is, -1 to 1, and where it was last frame.
	Vector2 touch[2];
	Vector2 last_touch[2];
	Vector2 pointer; // The right touchpad's pointer, on the screen.
	Vector2 scroll_touch; // The left touchpad's scroll not yet sent, in wheel notches.
	int touch_quiet_frames = 0; // The pointer's own (warped) motion is not the mouse taking over.
	bool touched_last = false; // The touchpads were used last, not the buttons.

	// The arrow key held by the d-pad or the left stick, and when it repeats.
	Key held_arrow = Key::NONE;
	bool held_sent = false; // The held arrow's key went out (Properties step without one).
	// Where an arrow (d-pad) started: it moves within that panel only; View goes to the next one.
	ObjectID arrow_from;
	ObjectID arrow_area;
	double arrow_repeat_in = 0.0;
	double scroll_left = 0.0;

	bool active = false; // The pad was used last, not the mouse or keyboard.
	ObjectID last_window; // The window the pad worked in last frame: a dialog that just opened gets a pad-friendly focus.
	EditorGamepadBar *bar = nullptr;
	int keyboard_check_frames = 0;
	ObjectID typing_in; // The many-line field A started typing in.

	// Objects carried with the view while X is held.
	struct Carried {
		ObjectID id;
		Transform3D original;
	};
	LocalVector<Carried> carried;
	Node3DEditorViewport *carry_view = nullptr;
	Transform3D carry_start_view;

	// A number field the pad is changing (A on it): d-pad left and right, LT fine, RT fast.
	ObjectID adjusting;
	double adjust_original = 0.0;
	int adjust_dir = 0;
	double adjust_repeat_in = 0.0;
	double adjust_held = 0.0;

	void _read_pad();
	bool _pressed(Button p_button) const { return down[p_button] && !was_down[p_button]; }
	bool _released(Button p_button) const { return !down[p_button] && was_down[p_button]; }

	Window *_active_window() const;
	Window *_native_window(Window *p_window) const;
	Control *_focus_owner() const;
	Node3DEditorViewport *_focused_3d_view() const;
	Node3DEditorViewport *_3d_view_at(const Vector2 &p_screen) const;

	void _send_key(Key p_key, bool p_pressed, bool p_echo = false);
	void _tap_key(Key p_key);
	void _send_mouse_button(Control *p_at, const Vector2 &p_local, MouseButton p_button, bool p_pressed, float p_factor = 1.0);
	void _click(Control *p_at, const Vector2 &p_local, MouseButton p_button);

	void _update_arrows(double p_delta, bool p_use_stick);
	Control *_area_of(Control *p_control) const;
	void _keep_in_area();
	bool _inspector_step(Control *p_focus, int p_dir);
	void _scroll(Control *p_focus, double p_delta);
	void _next_area(int p_step);
	void _switch_tab(Control *p_focus, int p_step);
	void _open_main_menu();
	void _context_menu(Control *p_focus);
	void _focus_into(Control *p_area);
	bool _is_typing(const Control *p_control) const;
	Control *_find_list(Node *p_area) const;
	void _window_opened(Window *p_window);
	bool _focus_anything(Window *p_window);
	void _open_steam_keyboard();

	void _update_touchpads(Window *p_window);
	void _pointer_event(Window *p_native, const Vector2 &p_relative, MouseButton p_button, bool p_pressed);
	void _pointer_wheel(Window *p_native, MouseButton p_button, float p_factor);

	EditorSpinSlider *_adjusting() const;
	void _start_adjust(EditorSpinSlider *p_slider);
	void _end_adjust(bool p_keep);
	void _update_adjust(EditorSpinSlider *p_slider, double p_delta);
	void _release_arrow();

	void _update_3d(Node3DEditorViewport *p_view, double p_delta);
	void _start_carry(Node3DEditorViewport *p_view);
	void _end_carry(bool p_keep);
	void _turn_selection(float p_degrees);
	LocalVector<Node3D *> _selected_3d() const;

	void _set_active(bool p_active);
	void _update_hint(Control *p_focus, Node3DEditorViewport *p_view);

protected:
	void _notification(int p_what);
	virtual void input(const Ref<InputEvent> &p_event) override;

public:
	void set_bar(EditorGamepadBar *p_bar) { bar = p_bar; }

	EditorGamepad();
};
