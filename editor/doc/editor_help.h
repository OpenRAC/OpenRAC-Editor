/**************************************************************************/
/*  editor_help.h                                                         */
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

#pragma once

#include "core/os/thread.h"
#include "editor/doc/doc_tools.h"
#include "editor/plugins/editor_plugin.h"
#include "scene/gui/dialogs.h"
#include "scene/gui/popup.h"
#include "scene/gui/rich_text_label.h"
#include "scene/gui/text_edit.h"
#include "scene/main/timer.h"

class EditorFileSystemDirectory;

// OpenRAC Editor: the class reference page lived in the script editor, which is gone.
// Only the shared doc data stays; tooltips (EditorHelpBit) read from it.
class EditorHelp {
	inline static DocTools *doc = nullptr;
	inline static DocTools *ext_doc = nullptr;

	inline static int doc_generation_count = 0;
	inline static String doc_version_hash;
	inline static Thread worker_thread;
	inline static Thread loader_thread; // Only load scripts here to avoid deadlocking with main thread.

	inline static SafeFlag _script_docs_loaded = SafeFlag(false);
	inline static LocalVector<DocData::ClassDoc> _docs_to_add;
	inline static LocalVector<String> _docs_to_remove;
	inline static LocalVector<String> _docs_to_remove_by_path;

	static void _wait_for_thread(Thread &p_thread = worker_thread);
	static void _load_doc_thread(void *p_udata);
	static void _gen_doc_thread(void *p_udata);
	static void _gen_extensions_docs();
	static void _process_postponed_docs();
	static void _load_script_doc_cache_thread(void *p_udata);
	static void _regen_script_doc_thread(void *p_udata);
	static void _finish_regen_script_doc_thread(void *p_udata);
	static void _reload_scripts_documentation(EditorFileSystemDirectory *p_dir);
	static void _delete_script_doc_cache();
	static void _compute_doc_version_hash();

public:
	static void generate_doc(bool p_use_cache = true, bool p_use_script_cache = true);
	static void cleanup_doc();
	static void load_script_doc_cache();
	static void regenerate_script_doc_cache();
	static void save_script_doc_cache();
	static String get_cache_full_path();
	static String get_script_doc_cache_full_path();

	// Adding scripts to DocData directly may make script doc cache inconsistent. Use methods below when adding script docs.
	// Usage during startup can also cause deadlocks.
	static DocTools *get_doc_data();
	// Method forwarding to underlying DocTools to keep script doc cache consistent.
	static DocData::ClassDoc *get_doc(const String &p_class_name);
	static void add_doc(const DocData::ClassDoc &p_class_doc);
	static void remove_doc(const String &p_class_name);
	static void remove_script_doc_by_path(const String &p_path);
	static bool has_doc(const String &p_class_name);

	static void load_xml_buffer(const uint8_t *p_buffer, int p_size);
	static void remove_class(const String &p_class);

	static void init_gdext_pointers();
};

class EditorHelpBit : public VBoxContainer {
	GDCLASS(EditorHelpBit, VBoxContainer);

	enum SymbolHint {
		SYMBOL_HINT_NONE,
		SYMBOL_HINT_INHERITANCE, // [ < ParentClass[ < ...]]
		SYMBOL_HINT_ASSIGNABLE, // [: Type][ = value]
		SYMBOL_HINT_SIGNATURE, // (arguments)[ -> Type][ qualifiers]
	};

	struct DocType {
		String type;
		String enumeration;
		bool is_bitfield = false;
	};

	struct ArgumentData {
		String name;
		DocType doc_type;
		String default_value;
	};

	struct HelpData {
		String description;
		String deprecated_message;
		String experimental_message;
		DocType doc_type;
		String value;
		Vector<ArgumentData> arguments;
		ArgumentData rest_argument;
		String qualifiers;
		String resource_path;
	};

	inline static HashMap<StringName, HelpData> doc_class_cache;
	inline static HashMap<StringName, HashMap<StringName, HelpData>> doc_enum_cache;
	inline static HashMap<StringName, HashMap<StringName, HelpData>> doc_constant_cache;
	inline static HashMap<StringName, HashMap<StringName, HelpData>> doc_property_cache;
	inline static HashMap<StringName, HashMap<StringName, HelpData>> doc_theme_item_cache;
	inline static HashMap<StringName, HashMap<StringName, HelpData>> doc_method_cache;
	inline static HashMap<StringName, HashMap<StringName, HelpData>> doc_signal_cache;
	inline static HashMap<StringName, HashMap<StringName, HelpData>> doc_annotation_cache;

	RichTextLabel *title = nullptr;
	RichTextLabel *content = nullptr;

	bool use_class_prefix = false;

	String symbol_doc_link;
	String symbol_class_name;
	String symbol_type;
	String symbol_name;
	SymbolHint symbol_hint = SYMBOL_HINT_NONE;

	HelpData help_data;

	float content_min_height = 0.0;
	float content_max_height = 0.0;

	static HelpData _get_class_help_data(const StringName &p_class_name);
	static HelpData _get_enum_help_data(const StringName &p_class_name, const StringName &p_enum_name);
	static HelpData _get_constant_help_data(const StringName &p_class_name, const StringName &p_constant_name);
	static HelpData _get_property_help_data(const StringName &p_class_name, const StringName &p_property_name);
	static HelpData _get_theme_item_help_data(const StringName &p_class_name, const StringName &p_theme_item_name);
	static HelpData _get_method_help_data(const StringName &p_class_name, const StringName &p_method_name);
	static HelpData _get_signal_help_data(const StringName &p_class_name, const StringName &p_signal_name);
	static HelpData _get_annotation_help_data(const StringName &p_class_name, const StringName &p_annotation_name);

	void _add_type_to_title(const DocType &p_doc_type);
	void _update_labels();
	void _go_to_help(const String &p_what);
	void _go_to_url(const String &p_what);
	void _meta_clicked(const String &p_select);

protected:
	static void _bind_methods();
	void _notification(int p_what);

public:
	static String get_as_plain_text(const String &p_symbol, const String &p_prologue = String());

	void parse_symbol(const String &p_symbol, const String &p_prologue = String());
	void set_custom_text(const String &p_type, const String &p_name, const String &p_description);

	void set_content_height_limits(float p_min, float p_max);
	void update_content_height();

	EditorHelpBit(const String &p_symbol = String(), const String &p_prologue = String(), bool p_use_class_prefix = false, bool p_allow_selection = true, bool p_in_tooltip = false);
};

// Standard tooltips do not allow you to hover over them.
// This class is intended as a temporary workaround.
class EditorHelpBitTooltip : public PopupPanel {
	GDCLASS(EditorHelpBitTooltip, PopupPanel);

	static bool _is_tooltip_visible;

	Timer *timer = nullptr;
	uint64_t _enter_tree_time = 0;
	bool _is_mouse_inside_tooltip = false;
	bool _is_shortcut_pressed = false;

	static Control *_make_invisible_control();

	void _start_timer();
	void _target_gui_input(const Ref<InputEvent> &p_event);
	void _shortcut_pressed(Control *p_target);

protected:
	void _notification(int p_what);

public:
	// The returned control is an orphan node, which is to make the standard tooltip invisible.
	[[nodiscard]] static Control *make_tooltip(Control *p_target, const String &p_symbol, const String &p_prologue = String(), bool p_use_class_prefix = false, bool p_shortcut = false);

	void popup_under_position(const Point2 &p_point);

	bool is_shortcut_pressed() const { return _is_shortcut_pressed; }

	EditorHelpBitTooltip(Control *p_target, bool p_shortcut = false);
};

