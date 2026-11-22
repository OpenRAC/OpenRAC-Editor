/* SPDX-License-Identifier: MIT */
/* OpenRAC Editor: smooth motion: the intro, fading pages and windows, a gliding focus frame. */

#pragma once

#include "scene/gui/control.h"
#include "scene/main/node.h"

class DPITexture;
class TabContainer;
class Window;

// OpenRAC Editor: the intro when the editor opens: the OpenRAC Editor logo (the README's) comes up
// out of the dark over a gold line, then all of it fades away into the editor. Any key, click or
// pad button skips it.
class EditorIntro : public Control {
	GDCLASS(EditorIntro, Control);

	Ref<DPITexture> logo;
	uint64_t last_usec = 0;
	double clock = 0.0; // Its own time: a long freeze while loading does not skip the show.
	double leave_at = -1.0; // On its clock; -1 until the editor is ready.

	double _now() const;
	void _finish();

protected:
	void _notification(int p_what);
	virtual void gui_input(const Ref<InputEvent> &p_event) override;
	virtual void input(const Ref<InputEvent> &p_event) override;

public:
	EditorIntro();
};

// OpenRAC Editor: while a gamepad is used, a gold frame glides from one focused control to the next,
// so the eye follows where the focus went.
class EditorFocusGlide : public Control {
	GDCLASS(EditorFocusGlide, Control);

	Rect2 shown_rect;
	Rect2 target_rect;
	float shown_alpha = 0.0;
	bool want = false;
	bool jump = true;

protected:
	void _notification(int p_what);

public:
	// The control to frame (nullptr: none), in this control's window.
	void glide_to(Control *p_control);

	EditorFocusGlide();
};

// OpenRAC Editor: the editor's motion: pages of tabs fade and slide in, popups and windows fade in,
// lists scroll smoothly. All of it follows Settings > Editor > Animations.
class EditorMotion : public Node {
	GDCLASS(EditorMotion, Node);

	static EditorMotion *singleton;
	bool enabled = true;

	void _node_added(Node *p_node);
	void _watch(Node *p_node);
	void _tab_changed(int p_tab, ObjectID p_tabs);
	void _window_shown(ObjectID p_window);
	void _settings_changed();

protected:
	void _notification(int p_what);

public:
	static EditorMotion *get_singleton() { return singleton; }
	static bool is_enabled();
	// Fades p_control in (and slides it p_from pixels), from nothing to as it is.
	void fade_in(Control *p_control, const Vector2 &p_from = Vector2(), double p_seconds = 0.18);

	EditorMotion();
	~EditorMotion();
};
