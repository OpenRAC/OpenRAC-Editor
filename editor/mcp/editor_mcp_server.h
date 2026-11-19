/* SPDX-License-Identifier: MIT */
/* OpenRAC Editor: a Model Context Protocol server, so AI tools can read and edit levels. */

#pragma once

#include "core/io/file_access.h"
#include "core/os/os.h"
#include "core/io/stream_peer_tcp.h"
#include "core/io/tcp_server.h"
#include "core/string/print_string.h"
#include "scene/main/node.h"

class EditorMCPServer : public Node {
	GDCLASS(EditorMCPServer, Node);

	struct Connection {
		Ref<StreamPeerTCP> peer;
		Vector<uint8_t> in;
		Vector<uint8_t> out;
		int out_pos = 0;
		uint64_t started_msec = 0;
		bool responded = false;
		// A job_wait answered when its job ends or its time is up (OpenRAC Editor).
		String wait_job;
		uint64_t wait_until_msec = 0;
		Variant wait_rpc_id;
	};

	// OpenRAC Editor: tools, resources and prompts that addons register from GDScript.
	struct ScriptTool {
		Dictionary definition;
		Callable callable;
	};
	struct ScriptResource {
		Dictionary definition;
		Variant source; // A file path, or a Callable that returns the text.
	};
	struct ScriptPrompt {
		Dictionary definition;
		Callable callable;
	};
	HashMap<String, ScriptTool> script_tools;
	Vector<String> script_tool_order;
	HashMap<String, ScriptResource> script_resources;
	Vector<String> script_resource_order;
	HashMap<String, ScriptPrompt> script_prompts;
	Vector<String> script_prompt_order;

	// OpenRAC Editor: long work (a process, or a script that finishes later), followed by job_status
	// and job_wait.
	struct Job {
		String id;
		String title;
		String state = "running"; // running, done, failed, cancelled
		Vector<String> lines;
		uint64_t line_total = 0;
		Variant result;
		uint64_t started_msec = 0;
		uint64_t ended_msec = 0;
		// A process job.
		Ref<FileAccess> out;
		Ref<FileAccess> err;
		ProcessID pid = 0;
		String out_partial;
		String err_partial;
		PackedStringArray output;
		Callable done;
		int exit_code = 0;
	};
	HashMap<String, Job> jobs;
	uint64_t job_counter = 0;
	// Set by job_wait for _handle_http: the connection waits for this job instead of answering now.
	String deferred_job;
	uint64_t deferred_until_msec = 0;

	Ref<TCPServer> server;
	List<Connection> connections;
	// Set while a request is handled: a tool that runs frames of its own (saving, importing) must
	// not handle the same request again inside it.
	bool polling = false;
	String token;
	int port = 0;
	String session_id;

	// The last lines of output and errors, for the log tool.
	PrintHandlerList print_handler;
	ErrorHandlerList error_handler;
	Vector<String> log_lines;
	uint64_t log_total = 0;

	static void _print_handler(void *p_this, const String &p_string, bool p_error, bool p_rich);
	static void _error_handler(void *p_this, const char *p_func, const char *p_file, int p_line, const char *p_error, const char *p_message, bool p_editor_notify, ErrorHandlerType p_type);
	void _add_log(const String &p_line);

	void _start();
	void _stop();
	void _poll();
	bool _handle_connection(Connection &p_conn);
	String _handle_http(const String &p_method, const String &p_path, const HashMap<String, String> &p_headers, const String &p_body, int &r_status, Vector<String> &r_extra_headers);
	Variant _handle_rpc(const Dictionary &p_request, bool &r_is_notification);
	Dictionary _rpc_error(const Variant &p_id, int p_code, const String &p_message) const;

	Array _tool_list() const;
	Dictionary _script_result(const Variant &p_value) const;
	void _poll_jobs();
	void _read_pipe(Job &p_job, Ref<FileAccess> p_pipe, String &r_partial, bool p_flush);
	void _end_process_job(Job &p_job);
	Dictionary _job_info(const Job &p_job, int p_from_line) const;
	Dictionary _tool_batch(const Dictionary &p_args);
	Dictionary _tool_job_status(const Dictionary &p_args);
	Dictionary _tool_job_wait(const Dictionary &p_args);
	Dictionary _tool_job_cancel(const Dictionary &p_args);
	Array _resource_list() const;
	Dictionary _resource_read(const String &p_uri, String &r_error) const;
	Array _prompt_list() const;
	Dictionary _prompt_get(const String &p_name, const Dictionary &p_args, String &r_error) const;
	Dictionary _call_tool(const String &p_name, const Dictionary &p_args);

	// Tools.
	Dictionary _tool_editor_state(const Dictionary &p_args);
	Dictionary _tool_scene_open(const Dictionary &p_args);
	Dictionary _tool_scene_save(const Dictionary &p_args);
	Dictionary _tool_tree(const Dictionary &p_args);
	Dictionary _tool_node_get(const Dictionary &p_args);
	Dictionary _tool_node_set(const Dictionary &p_args);
	Dictionary _tool_node_call(const Dictionary &p_args);
	Dictionary _tool_node_create(const Dictionary &p_args);
	Dictionary _tool_node_delete(const Dictionary &p_args);
	Dictionary _tool_node_reparent(const Dictionary &p_args);
	Dictionary _tool_node_duplicate(const Dictionary &p_args);
	Dictionary _tool_select(const Dictionary &p_args);
	Dictionary _tool_class_info(const Dictionary &p_args);
	Dictionary _tool_undo(const Dictionary &p_args);
	Dictionary _tool_redo(const Dictionary &p_args);
	Dictionary _tool_view_get(const Dictionary &p_args);
	Dictionary _tool_view_set(const Dictionary &p_args);
	Dictionary _tool_screenshot(const Dictionary &p_args);
	Dictionary _tool_eval(const Dictionary &p_args);
	Dictionary _tool_run_gdscript(const Dictionary &p_args);
	Dictionary _tool_log(const Dictionary &p_args);
	Dictionary _tool_files(const Dictionary &p_args);
	Dictionary _tool_resource_get(const Dictionary &p_args);
	Dictionary _tool_level_find(const Dictionary &p_args);

	// Helpers.
	Node *_scene_root() const;
	Node *_get_node(const Variant &p_path, String &r_error) const;
	String _node_path(const Node *p_node) const;
	Dictionary _node_summary(const Node *p_node) const;
	Variant _to_json(const Variant &p_value, int p_depth = 0) const;
	Variant _from_json(const Variant &p_json, Variant::Type p_type, String &r_error) const;
	Variant::Type _property_type(Object *p_object, const StringName &p_property) const;
	Dictionary _object_properties(Object *p_object, const Array &p_only, bool p_all) const;
	void _collect_owned(Node *p_node, Node *p_owner, List<Node *> &r_list) const;
	static String _meta_by_suffix(const Node *p_node, const String &p_suffix, Variant &r_value);

	Dictionary _text_result(const Variant &p_data, bool p_error = false) const;
	Dictionary _error_result(const String &p_message) const;

	static EditorMCPServer *singleton;

protected:
	void _notification(int p_what);
	static void _bind_methods();

public:
	static String get_info_path();
	static EditorMCPServer *get_singleton() { return singleton; }

	// OpenRAC Editor: what addons add (openrac_library/mcp_*.gd).
	void register_tool(const String &p_name, const String &p_description, const Dictionary &p_input_schema, const Callable &p_callable, const Dictionary &p_annotations = Dictionary());
	void unregister_tool(const String &p_name);
	void register_resource(const String &p_uri, const String &p_name, const String &p_description, const String &p_mime_type, const Variant &p_source);
	void unregister_resource(const String &p_uri);
	void register_prompt(const String &p_name, const String &p_description, const Array &p_arguments, const Callable &p_callable);
	void unregister_prompt(const String &p_name);
	String job_create(const String &p_title);
	void job_progress(const String &p_id, const String &p_line);
	void job_finish(const String &p_id, const Variant &p_result, bool p_failed = false);
	String job_run_process(const String &p_title, const String &p_program, const PackedStringArray &p_arguments, const Callable &p_done = Callable());
	bool job_running(const String &p_id) const;

	EditorMCPServer();
	~EditorMCPServer();
};
