/**************************************************************************/
/*  editor_main_screen.cpp                                                */
/**************************************************************************/
/*                         This file is part of:                          */
/*                             GODOT ENGINE                               */
/*                        https://godotengine.org                         */
/**************************************************************************/
/* Copyright (c) 2014-present Godot Engine contributors (see AUTHORS.md). */
/* Copyright (c) 2007-2014 Juan Linietsky, Ariel Manzur.                  */
/*                                                                        */
/* Permission is hereby granted, free of charge, to any person obtaining  */
/* a copy of this software and associated documentation files (the        */
/* "Software"), to deal in the Software without restriction, including    */
/* without limitation the rights to use, copy, modify, merge, publish,    */
/* distribute, sublicense, and/or sell copies of the Software, and to     */
/* permit persons to whom the Software is furnished to do so, subject to  */
/* the following conditions:                                              */
/*                                                                        */
/* The above copyright notice and this permission notice shall be         */
/* included in all copies or substantial portions of the Software.        */
/*                                                                        */
/* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,        */
/* EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF     */
/* MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. */
/* IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY   */
/* CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,   */
/* TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE      */
/* SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.                 */
/**************************************************************************/

#include "editor_main_screen.h"

#include "core/io/config_file.h"
#include "core/object/callable_mp.h"
#include "editor/editor_node.h"
#include "editor/plugins/editor_plugin.h"
#include "scene/gui/box_container.h"

void EditorMainScreen::_notification(int p_what) {
	switch (p_what) {
		case NOTIFICATION_READY: {
			set_accessibility_region(true);
			if (!editor_table.is_empty()) {
				select(EDITOR_3D);
			}
		} break;
	}
}

void EditorMainScreen::save_layout_to_config(Ref<ConfigFile> p_config_file, const String &p_section) const {
	const int selected_main_editor_idx = get_selected_index();
	if (selected_main_editor_idx != -1) {
		p_config_file->set_value(p_section, "selected_main_editor_idx", selected_main_editor_idx);
	} else {
		p_config_file->set_value(p_section, "selected_main_editor_idx", Variant());
	}
}

void EditorMainScreen::load_layout_from_config(Ref<ConfigFile> p_config_file, const String &p_section) {
	int selected_main_editor_idx = p_config_file->get_value(p_section, "selected_main_editor_idx", -1);
	if (selected_main_editor_idx >= 0 && selected_main_editor_idx < editor_table.size()) {
		callable_mp(this, &EditorMainScreen::select).call_deferred(selected_main_editor_idx);
	}
}

int EditorMainScreen::_get_current_main_editor() const {
	for (int i = 0; i < editor_table.size(); i++) {
		if (editor_table[i] == selected_plugin) {
			return i;
		}
	}

	return 0;
}

void EditorMainScreen::select_next() {
	int editor = _get_current_main_editor();

	editor = editor == editor_table.size() - 1 ? 0 : editor + 1;

	select(editor);
}

void EditorMainScreen::select_prev() {
	int editor = _get_current_main_editor();

	editor = editor == 0 ? editor_table.size() - 1 : editor - 1;

	select(editor);
}

void EditorMainScreen::select_by_name(const String &p_name) {
	ERR_FAIL_COND(p_name.is_empty());

	for (int i = 0; i < editor_table.size(); i++) {
		if (editor_table[i]->get_plugin_name() == p_name) {
			select(i);
			return;
		}
	}

	ERR_FAIL_MSG("The editor name '" + p_name + "' was not found.");
}

void EditorMainScreen::select(int p_index) {
	if (EditorNode::get_singleton()->is_changing_scene()) {
		return;
	}

	ERR_FAIL_INDEX(p_index, editor_table.size());

	EditorPlugin *new_editor = editor_table[p_index];
	ERR_FAIL_NULL(new_editor);

	if (selected_plugin == new_editor) {
		return;
	}

	if (selected_plugin) {
		selected_plugin->make_visible(false);
	}

	selected_plugin = new_editor;
	selected_plugin->make_visible(true);
	selected_plugin->selected_notify();
	set_accessibility_name(selected_plugin->get_plugin_name());

	EditorData &editor_data = EditorNode::get_editor_data();
	int plugin_count = editor_data.get_editor_plugin_count();
	for (int i = 0; i < plugin_count; i++) {
		editor_data.get_editor_plugin(i)->notify_main_screen_changed(selected_plugin->get_plugin_name());
	}
}

int EditorMainScreen::get_selected_index() const {
	for (int i = 0; i < editor_table.size(); i++) {
		if (selected_plugin == editor_table[i]) {
			return i;
		}
	}
	return -1;
}

int EditorMainScreen::get_plugin_index(EditorPlugin *p_editor) const {
	int screen = -1;
	for (int i = 0; i < editor_table.size(); i++) {
		if (p_editor == editor_table[i]) {
			screen = i;
			break;
		}
	}
	return screen;
}

EditorPlugin *EditorMainScreen::get_selected_plugin() const {
	return selected_plugin;
}

EditorPlugin *EditorMainScreen::get_plugin_by_name(const String &p_plugin_name) const {
	ERR_FAIL_COND_V(!main_editor_plugins.has(p_plugin_name), nullptr);
	return main_editor_plugins[p_plugin_name];
}

VBoxContainer *EditorMainScreen::get_control() const {
	return main_screen_vbox;
}

void EditorMainScreen::add_main_plugin(EditorPlugin *p_editor) {
	editor_table.push_back(p_editor);
	main_editor_plugins.insert(p_editor->get_plugin_name(), p_editor);
}

void EditorMainScreen::remove_main_plugin(EditorPlugin *p_editor) {
	if (selected_plugin == p_editor) {
		selected_plugin = nullptr;
	}

	editor_table.erase(p_editor);
	main_editor_plugins.erase(p_editor->get_plugin_name());
}

EditorMainScreen::EditorMainScreen() {
	main_screen_vbox = memnew(VBoxContainer);
	main_screen_vbox->set_name("MainScreen");
	main_screen_vbox->set_v_size_flags(Control::SIZE_EXPAND_FILL);
	main_screen_vbox->add_theme_constant_override("separation", 0);
	add_child(main_screen_vbox);
}
