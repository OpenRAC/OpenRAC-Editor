/* SPDX-License-Identifier: MIT */
/* OpenRAC Editor: the first steps card: how to make a level, in three steps, until "Got it". */

#include "editor_first_steps.h"

#include "core/object/callable_mp.h"
#include "editor/editor_main_screen.h"
#include "editor/editor_node.h"
#include "editor/editor_string_names.h"
#include "editor/gui/editor_motion.h"
#include "editor/settings/editor_settings.h"
#include "editor/themes/editor_scale.h"
#include "scene/gui/box_container.h"
#include "scene/gui/button.h"
#include "scene/gui/label.h"
#include "scene/gui/panel_container.h"
#include "scene/resources/style_box_flat.h"

namespace {
const char *DONE_KEY = "_openrac_first_steps_done";
const Color GOLD(0.96, 0.75, 0.25);
} // namespace

bool EditorFirstSteps::is_wanted() {
	EditorSettings *settings = EditorSettings::get_singleton();
	return !(settings->has_setting(DONE_KEY) && bool(settings->get_setting(DONE_KEY)));
}

void EditorFirstSteps::_got_it() {
	EditorSettings::get_singleton()->set_setting(DONE_KEY, true);
	EditorSettings::get_singleton()->save();
	queue_free();
}

EditorFirstSteps::EditorFirstSteps() {
	set_name("EditorFirstSteps");
	set_anchors_and_offsets_preset(PRESET_FULL_RECT);
	set_mouse_filter(MOUSE_FILTER_IGNORE);
	set_focus_mode(FOCUS_NONE);

	card = memnew(PanelContainer);
	add_child(card);

	VBoxContainer *rows = memnew(VBoxContainer);
	rows->add_theme_constant_override("separation", 6 * EDSCALE);
	card->add_child(rows);

	Label *title = memnew(Label(TTRC("Make your level in 3 steps")));
	title->set_name("Title");
	rows->add_child(title);

	const char *steps[] = {
		TTRC("Drag something from Assets (left) into the level."),
		TTRC("Click it to pick it. Drag its arrows to move it, and change it in Properties (right)."),
		TTRC("Press Play Level (top right) to try it in the game."),
	};
	for (int i = 0; i < 3; i++) {
		HBoxContainer *row = memnew(HBoxContainer);
		row->add_theme_constant_override("separation", 8 * EDSCALE);
		rows->add_child(row);
		Label *number = memnew(Label(itos(i + 1)));
		number->set_name("Number");
		number->set_horizontal_alignment(HORIZONTAL_ALIGNMENT_CENTER);
		number->set_vertical_alignment(VERTICAL_ALIGNMENT_CENTER);
		number->set_custom_minimum_size(Size2(22, 22) * EDSCALE);
		number->set_v_size_flags(SIZE_SHRINK_BEGIN);
		row->add_child(number);
		Label *text = memnew(Label(steps[i]));
		text->set_autowrap_mode(TextServer::AUTOWRAP_WORD_SMART);
		text->set_custom_minimum_size(Size2(280, 0) * EDSCALE);
		text->set_h_size_flags(SIZE_EXPAND_FILL);
		row->add_child(text);
	}

	Label *more = memnew(Label(TTRC("The bar at the bottom always says what you can do where the mouse is.")));
	more->set_autowrap_mode(TextServer::AUTOWRAP_WORD_SMART);
	more->set_custom_minimum_size(Size2(310, 0) * EDSCALE);
	more->set_modulate(Color(1, 1, 1, 0.65));
	rows->add_child(more);

	Button *got_it = memnew(Button(TTRC("Got It")));
	got_it->set_h_size_flags(SIZE_SHRINK_END);
	got_it->connect(SceneStringName(pressed), callable_mp(this, &EditorFirstSteps::_got_it));
	rows->add_child(got_it);
}

void EditorFirstSteps::_notification(int p_what) {
	switch (p_what) {
		case NOTIFICATION_THEME_CHANGED: {
			Ref<StyleBoxFlat> box;
			box.instantiate();
			box->set_bg_color(get_theme_color(SNAME("dark_color_1"), EditorStringName(Editor)) * Color(1, 1, 1, 0.96));
			box->set_border_width_all(Math::round(EDSCALE));
			box->set_border_width(SIDE_LEFT, Math::round(3 * EDSCALE));
			box->set_border_color(GOLD);
			box->set_corner_radius_all(6 * EDSCALE);
			box->set_content_margin_all(12 * EDSCALE);
			box->set_shadow_color(Color(0, 0, 0, 0.35));
			box->set_shadow_size(8 * EDSCALE);
			card->add_theme_style_override(SceneStringName(panel), box);
			const Ref<Font> bold = get_theme_font(SNAME("bold"), EditorStringName(EditorFonts));
			Label *title = Object::cast_to<Label>(card->find_child("Title", true, false));
			if (title) {
				title->add_theme_font_override(SceneStringName(font), bold);
				title->add_theme_color_override(SceneStringName(font_color), GOLD);
			}
			Ref<StyleBoxFlat> disc;
			disc.instantiate();
			disc->set_bg_color(GOLD);
			disc->set_corner_radius_all(11 * EDSCALE);
			for (const Variant &v : card->find_children("Number", "Label", true, false)) {
				Label *number = Object::cast_to<Label>(v.get_validated_object());
				number->add_theme_style_override(CoreStringName(normal), disc);
				number->add_theme_font_override(SceneStringName(font), bold);
				number->add_theme_color_override(SceneStringName(font_color), Color(0.1, 0.09, 0.07));
			}
		} break;

		case NOTIFICATION_READY: {
			set_process(true);
			if (EditorMotion::get_singleton()) {
				EditorMotion::get_singleton()->fade_in(card, Vector2(), 0.35);
			}
		} break;

		case NOTIFICATION_PROCESS: {
			// In the bottom left corner of the level view, wherever the panels leave it.
			Control *screen = EditorNode::get_editor_main_screen();
			if (!screen || !screen->is_visible_in_tree()) {
				card->hide();
				return;
			}
			card->show();
			const Rect2 area = screen->get_global_rect();
			const Vector2 size = card->get_combined_minimum_size();
			card->set_size(size);
			card->set_global_position(Vector2(area.position.x + 14 * EDSCALE, area.get_end().y - size.y - 14 * EDSCALE));
		} break;
	}
}
