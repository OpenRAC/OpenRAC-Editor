/* SPDX-License-Identifier: MIT */
/* OpenRAC Editor: smooth motion: the intro, fading pages and windows, a gliding focus frame. */

#include "editor_motion.h"

#include "core/object/callable_mp.h"
#include "core/os/os.h"
#include "core/version.h"
#include "editor/editor_interface.h"
#include "editor/editor_node.h"
#include "editor/editor_string_names.h"
#include "editor/gui/progress_dialog.h"
#include "editor/settings/editor_settings.h"
#include "editor/themes/editor_icons.gen.h"
#include "editor/themes/editor_scale.h"
#include "scene/animation/tween.h"
#include "scene/gui/container.h"
#include "scene/gui/dialogs.h"
#include "scene/gui/popup.h"
#include "scene/gui/scroll_bar.h"
#include "scene/gui/tab_container.h"
#include "scene/main/scene_tree.h"
#include "scene/main/window.h"
#include "scene/resources/dpi_texture.h"
#include "scene/resources/style_box_flat.h"
#include "servers/display/display_server.h"

namespace {
// The intro's moments, in seconds.
constexpr double LOGO_IN = 0.15;
constexpr double LOGO_IN_END = 0.75;
constexpr double LINE_IN = 0.5;
constexpr double LINE_IN_END = 1.0;
constexpr double VERSION_IN = 0.75;
constexpr double VERSION_IN_END = 1.1;
constexpr double SHORTEST = 1.7; // It stays at least this long, unless skipped.
constexpr double LONGEST = 8.0; // And leaves by then even if the level is still loading.
constexpr double LEAVE = 0.55;

const Color COAL(0.075, 0.067, 0.055);
const Color GOLD(0.96, 0.75, 0.25);

double phase(double p_t, double p_from, double p_to) {
	return CLAMP((p_t - p_from) / (p_to - p_from), 0.0, 1.0);
}

double ease_out(double p_x) {
	return 1.0 - Math::pow(1.0 - p_x, 3.0);
}

double ease_in_out(double p_x) {
	return p_x < 0.5 ? 4.0 * p_x * p_x * p_x : 1.0 - Math::pow(-2.0 * p_x + 2.0, 3.0) / 2.0;
}

Ref<DPITexture> editor_logo(float p_scale) {
	for (int i = 0; i < editor_icons_count; i++) {
		if (String(editor_icons_names[i]) == "OpenRACEditorLogo") {
			return DPITexture::create_from_string(editor_icons_sources[i], p_scale);
		}
	}
	return Ref<DPITexture>();
}
} // namespace

// ---- The intro ----------------------------------------------------------------------------------

EditorIntro::EditorIntro() {
	set_anchors_and_offsets_preset(PRESET_FULL_RECT);
	set_mouse_filter(MOUSE_FILTER_STOP);
	set_focus_mode(FOCUS_NONE);
}

double EditorIntro::_now() const {
	return clock;
}

void EditorIntro::_leave() {
	if (leave_at < 0 || leave_at > _now()) {
		leave_at = MAX(_now(), LOGO_IN_END * 0.5);
	}
}

void EditorIntro::gui_input(const Ref<InputEvent> &p_event) {
	Ref<InputEventMouseButton> mb = p_event;
	if (mb.is_valid() && mb->is_pressed()) {
		_leave();
		accept_event();
	}
}

void EditorIntro::input(const Ref<InputEvent> &p_event) {
	// A key or a pad button skips it.
	if ((Object::cast_to<InputEventKey>(*p_event) || Object::cast_to<InputEventJoypadButton>(*p_event)) && p_event->is_pressed()) {
		_leave();
		get_viewport()->set_input_as_handled();
	}
}

void EditorIntro::_notification(int p_what) {
	switch (p_what) {
		case NOTIFICATION_READY: {
			// Sharp at any size: the logo is drawn from its SVG at the size it is shown.
			logo = editor_logo(get_size().x * 0.5f / 2039.0f); // The README's logo, half the editor wide.
			set_process(true);
			set_process_input(true);
			if (ProgressDialog::get_singleton()) {
				ProgressDialog::get_singleton()->set_quiet(true);
			}
		} break;

		case NOTIFICATION_EXIT_TREE: {
			if (ProgressDialog::get_singleton()) {
				ProgressDialog::get_singleton()->set_quiet(false);
			}
		} break;

		case NOTIFICATION_PROCESS: {
			// While the editor loads, frames come seldom: a long wait moves the show on only a little.
			const uint64_t now_usec = OS::get_singleton()->get_ticks_usec();
			if (last_usec != 0) {
				clock += MIN((now_usec - last_usec) / 1000000.0, 0.1);
			}
			last_usec = now_usec;
			const double t = _now();
			if (leave_at >= 0 && ProgressDialog::get_singleton()) {
				ProgressDialog::get_singleton()->set_quiet(false); // Anything loading from here on shows as usual.
			}
			if (leave_at < 0) {
				// It leaves once the level is open (and not before the logo had its moment).
				const bool ready = EditorNode::get_singleton()->get_edited_scene() != nullptr || t > LONGEST;
				if (ready && t >= SHORTEST) {
					leave_at = t;
				}
			}
			if (leave_at >= 0 && t > leave_at + LEAVE) {
				queue_free();
				return;
			}
			if (leave_at >= 0) {
				set_mouse_filter(MOUSE_FILTER_IGNORE); // The editor can be used while it fades.
			}
			queue_redraw();
		} break;

		case NOTIFICATION_DRAW: {
			const double t = _now();
			const double gone = leave_at >= 0 ? ease_in_out(phase(t, leave_at, leave_at + LEAVE)) : 0.0;
			const Size2 size = get_size();
			const Vector2 center = size / 2;
			const float alpha = 1.0 - gone;

			draw_rect(Rect2(Vector2(), size), Color(COAL, alpha));

			// A warm glow that breathes in behind the logo.
			const double glow = ease_out(phase(t, 0.0, 1.2));
			for (int i = 6; i >= 1; i--) {
				const float r = size.y * (0.12 + 0.07 * i) * (0.85 + 0.15 * glow);
				draw_circle(center, r, Color(GOLD, 0.022 * glow * alpha * (7 - i) / 6.0), true, -1, true);
			}

			if (logo.is_null()) {
				return;
			}
			// The logo rises and comes up; when leaving it grows a little as it fades (into the editor).
			const double in = ease_out(phase(t, LOGO_IN, LOGO_IN_END));
			const float grow = 0.94 + 0.06 * in + 0.08 * gone;
			const Size2 logo_size = logo->get_size() * grow;
			const Vector2 logo_pos = center - logo_size / 2 + Vector2(0, -size.y * 0.04 + 18 * EDSCALE * (1.0 - in));
			draw_texture_rect(logo, Rect2(logo_pos, logo_size), false, Color(1, 1, 1, in * alpha));

			// A gold line opens out from the middle under the logo.
			const double line = ease_in_out(phase(t, LINE_IN, LINE_IN_END));
			const float line_y = logo_pos.y + logo_size.y + 20 * EDSCALE;
			const float half = logo_size.x * 0.4 * line;
			const int pieces = 24;
			for (int i = 0; i < pieces; i++) {
				// Brightest in the middle, fading out to its ends.
				const float a = 1.0 - Math::abs((i + 0.5f) / pieces * 2.0f - 1.0f);
				const float x0 = center.x - half + (2 * half) * i / pieces;
				draw_rect(Rect2(x0, line_y, 2 * half / pieces + 0.5f, Math::round(2 * EDSCALE)), Color(GOLD, a * alpha * line));
			}

			// The version fades in, quietly, under the line.
			const double version_in = ease_out(phase(t, VERSION_IN, VERSION_IN_END));
			if (version_in > 0) {
				const Ref<Font> main = get_theme_font(SNAME("main"), EditorStringName(EditorFonts));
				const int small = Math::round(12 * EDSCALE);
				const String version = OPENRAC_EDITOR_VERSION;
				const float vw = main->get_string_size(version, HORIZONTAL_ALIGNMENT_LEFT, -1, small).x;
				draw_string(main, Vector2(center.x - vw / 2, line_y + (30 + 6 * (1.0 - version_in)) * EDSCALE), version, HORIZONTAL_ALIGNMENT_LEFT, -1, small, Color(0.7, 0.67, 0.6, 0.8 * alpha * version_in));
			}

			// What is loading, quietly at the bottom.
			const String loading = ProgressDialog::get_singleton() && leave_at < 0 ? ProgressDialog::get_singleton()->get_state_text() : String();
			if (!loading.is_empty()) {
				const Ref<Font> main = get_theme_font(SNAME("main"), EditorStringName(EditorFonts));
				const int small = Math::round(12 * EDSCALE);
				const float lw = main->get_string_size(loading, HORIZONTAL_ALIGNMENT_LEFT, -1, small).x;
				draw_string(main, Vector2(center.x - lw / 2, size.y - 40 * EDSCALE), loading, HORIZONTAL_ALIGNMENT_LEFT, -1, small, Color(0.7, 0.67, 0.6, 0.6 * alpha * in));
			}
		} break;
	}
}

// ---- The gliding focus frame --------------------------------------------------------------------

EditorFocusGlide::EditorFocusGlide() {
	set_mouse_filter(MOUSE_FILTER_IGNORE);
	set_focus_mode(FOCUS_NONE);
	set_anchors_and_offsets_preset(PRESET_FULL_RECT);
	set_process(true);
}

void EditorFocusGlide::glide_to(Control *p_control) {
	if (!p_control) {
		want = false;
		return;
	}
	const Rect2 r = Rect2(p_control->get_global_position() - get_global_position(), p_control->get_size() * p_control->get_global_transform().get_scale());
	if (!want || shown_alpha < 0.05) {
		jump = true; // Appears where the focus is, instead of flying in from where it last was.
	}
	want = true;
	target_rect = r.grow(2 * EDSCALE);
}

void EditorFocusGlide::_notification(int p_what) {
	switch (p_what) {
		case NOTIFICATION_PROCESS: {
			const double delta = get_process_delta_time();
			const bool on = want && EditorMotion::is_enabled();
			const float to_alpha = on ? 1.0 : 0.0;
			const float old_alpha = shown_alpha;
			shown_alpha = Math::move_toward(shown_alpha, to_alpha, (float)delta * 6.0f);
			const Rect2 old_rect = shown_rect;
			if (jump) {
				shown_rect = target_rect;
				jump = false;
			} else {
				// Glides: quick at first, settling softly.
				const float k = 1.0 - Math::exp(-delta * 16.0);
				shown_rect.position = shown_rect.position.lerp(target_rect.position, k);
				shown_rect.size = shown_rect.size.lerp(target_rect.size, k);
			}
			if (old_alpha != shown_alpha || !old_rect.is_equal_approx(shown_rect)) {
				queue_redraw();
			}
		} break;

		case NOTIFICATION_DRAW: {
			if (shown_alpha <= 0.0) {
				return;
			}
			const Color accent = GOLD;
			const float radius = 5 * EDSCALE;
			// A soft glow around a crisp frame.
			for (int i = 3; i >= 1; i--) {
				Ref<StyleBoxFlat> glow;
				glow.instantiate();
				glow->set_draw_center(false);
				glow->set_border_width_all(Math::round(2 * EDSCALE));
				glow->set_border_color(Color(accent, 0.09 * shown_alpha * (4 - i)));
				glow->set_corner_radius_all(radius + i * 2 * EDSCALE);
				glow->set_anti_aliased(true);
				draw_style_box(glow, shown_rect.grow(i * 2 * EDSCALE));
			}
			Ref<StyleBoxFlat> frame;
			frame.instantiate();
			frame->set_draw_center(false);
			frame->set_border_width_all(MAX(2, Math::round(2 * EDSCALE)));
			frame->set_border_color(Color(accent, shown_alpha));
			frame->set_corner_radius_all(radius);
			frame->set_anti_aliased(true);
			draw_style_box(frame, shown_rect);
		} break;
	}
}

// ---- Motion -------------------------------------------------------------------------------------

EditorMotion *EditorMotion::singleton = nullptr;

EditorMotion::EditorMotion() {
	singleton = this;
	set_name("EditorMotion");
}

EditorMotion::~EditorMotion() {
	if (singleton == this) {
		singleton = nullptr;
	}
	ScrollBar::set_smooth_wheel(false);
}

bool EditorMotion::is_enabled() {
	return singleton && singleton->enabled;
}

void EditorMotion::_settings_changed() {
	enabled = EDITOR_GET("interface/editor/display/animations");
	ScrollBar::set_smooth_wheel(enabled);
}

void EditorMotion::fade_in(Control *p_control, const Vector2 &p_from, double p_seconds) {
	if (!enabled || !p_control || !p_control->is_inside_tree()) {
		return;
	}
	Ref<Tween> tween = p_control->create_tween();
	tween->set_parallel(true);
	Color modulate = p_control->get_modulate();
	modulate.a = 1.0;
	p_control->set_modulate(Color(modulate, 0.0));
	tween->tween_property(p_control, NodePath("modulate"), modulate, p_seconds)->set_trans(Tween::TRANS_CUBIC)->set_ease(Tween::EASE_OUT);
	if (!p_from.is_zero_approx()) {
		const Vector2 at = p_control->get_position();
		p_control->set_position(at + p_from);
		tween->tween_property(p_control, NodePath("position"), at, p_seconds)->set_trans(Tween::TRANS_CUBIC)->set_ease(Tween::EASE_OUT);
		// Back to where its container wants it, should that have changed meanwhile.
		if (Container *parent = Object::cast_to<Container>(p_control->get_parent())) {
			tween->chain()->tween_callback(Callable(parent, "queue_sort"));
		}
	}
}

void EditorMotion::_tab_changed(int p_tab, ObjectID p_tabs) {
	TabContainer *tabs = ObjectDB::get_instance<TabContainer>(p_tabs);
	if (!tabs || !tabs->is_visible_in_tree()) {
		return;
	}
	Control *page = tabs->get_current_tab_control();
	const int previous = tabs->get_previous_tab();
	// The new page slides in from the side its tab is on.
	const float side = previous < 0 ? 0.0f : (p_tab > previous ? 1.0f : -1.0f);
	fade_in(page, Vector2(side * 14 * EDSCALE, 0), 0.2);
}

void EditorMotion::_window_shown(ObjectID p_window) {
	Window *w = ObjectDB::get_instance<Window>(p_window);
	if (!w || !w->is_visible()) {
		return;
	}
	// Its contents fade in (and a dialog's rise a little).
	const bool dialog = Object::cast_to<AcceptDialog>(w) != nullptr;
	for (int i = 0; i < w->get_child_count(true); i++) {
		if (Control *c = Object::cast_to<Control>(w->get_child(i, true))) {
			if (c->is_visible()) {
				fade_in(c, dialog ? Vector2(0, 10 * EDSCALE) : Vector2(), dialog ? 0.2 : 0.12);
			}
		}
	}
}

void EditorMotion::_watch(Node *p_node) {
	if (TabContainer *tabs = Object::cast_to<TabContainer>(p_node)) {
		const Callable changed = callable_mp(this, &EditorMotion::_tab_changed).bind(tabs->get_instance_id());
		if (!tabs->is_connected("tab_changed", changed)) {
			tabs->connect("tab_changed", changed);
		}
		return;
	}
	Window *w = Object::cast_to<Window>(p_node);
	if (w && (Object::cast_to<Popup>(w) || Object::cast_to<AcceptDialog>(w))) {
		const Callable shown = callable_mp(this, &EditorMotion::_window_shown).bind(w->get_instance_id());
		if (!w->is_connected(SceneStringName(visibility_changed), shown)) {
			w->connect(SceneStringName(visibility_changed), shown);
		}
	}
}

void EditorMotion::_node_added(Node *p_node) {
	// The editor's own parts only, not the level being edited.
	if (p_node->is_part_of_edited_scene()) {
		return;
	}
	_watch(p_node);
}

void EditorMotion::_notification(int p_what) {
	switch (p_what) {
		case NOTIFICATION_READY: {
			_settings_changed();
			EditorSettings::get_singleton()->connect("settings_changed", callable_mp(this, &EditorMotion::_settings_changed));
			get_tree()->connect("node_added", callable_mp(this, &EditorMotion::_node_added));
			// What is there already.
			LocalVector<Node *> stack;
			stack.push_back(get_tree()->get_root());
			while (!stack.is_empty()) {
				Node *n = stack[stack.size() - 1];
				stack.remove_at(stack.size() - 1);
				if (n->is_part_of_edited_scene()) {
					continue;
				}
				_watch(n);
				for (int i = 0; i < n->get_child_count(true); i++) {
					stack.push_back(n->get_child(i, true));
				}
			}
		} break;
	}
}
