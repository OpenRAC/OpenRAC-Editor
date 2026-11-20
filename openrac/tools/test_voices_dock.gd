extends SceneTree
## test_voices_dock.py's Godot half: the Voices dock (voices_dock.gd) and its process
## (voices_lab.gd) in the editor, against voice_lab.py on a workspace the Python half made.
## User arguments: the result file, Python, the tools folder, the data folder.

const Lab = preload("res://addons/openrac_library/voices_lab.gd")
const VoicesDock = preload("res://addons/openrac_library/voices_dock.gd")

var steps: Dictionary = {}
var stage: int = 0
var frames: int = 0
var lab: Lab
var dock: VoicesDock
var answers: Array = []


func _process(_delta: float) -> bool:
	frames += 1
	var args: PackedStringArray = OS.get_cmdline_user_args()

	match stage:
		0:
			steps["parse"] = [Lab.parse_line('openrac-voices: {"id": 3, "ok": true, "result": {"a": 1}}'),
				Lab.parse_line('openrac-voices: {"id": null, "ok": false, "error": "not a JSON command"}'),
				Lab.parse_line('openrac-voices-progress: {"id": 4, "stage": "speak", "done": 1, "total": 3, "text": "Take 2"}'),
				Lab.parse_line("Downloading model.safetensors 40%"), Lab.parse_line("openrac-voices: {broken")]
			steps["arguments"] = [Lab.arguments("/t", "/d", "/o", "rac2", "pal"), Lab.arguments("/t", "/d", "", "", "")]
			steps["no_python"] = Lab.new().start("", PackedStringArray([args[2].path_join("voice_lab.py")]))
			lab = Lab.new()
			steps["start"] = lab.start(args[1], Lab.arguments(args[2], args[3], "", "rac1", "ntsc"))
			lab.send("hardware", {}, func(ok: bool, result: Variant, error: String) -> void: answers.append(["hardware", ok, result, error]))
			lab.send("lines", {"character": "Clank"}, func(ok: bool, result: Variant, error: String) -> void: answers.append(["lines", ok, result, error]))
			lab.send("explode", {}, func(ok: bool, result: Variant, error: String) -> void: answers.append(["explode", ok, result, error]))
			steps["busy"] = lab.busy()
			stage = 1
		1:
			lab.poll()
			if answers.size() == 3 or frames > 3000:
				steps["answers"] = answers.map(func(a: Array) -> Array: return [a[0], a[1], a[3]])
				steps["device"] = answers[0][2].get("device", "") if answers.size() > 0 and answers[0][2] is Dictionary else ""
				steps["clank_lines"] = answers[1][2]["total"] if answers.size() > 1 and answers[1][2] is Dictionary else -1
				steps["busy_after"] = lab.busy()
				lab.stop()
				steps["running_after_stop"] = lab.running()
				var late: Array = []
				lab.send("summary", {}, func(ok: bool, _r: Variant, error: String) -> void: late.append([ok, error]))
				steps["after_stop"] = late
				start_dock(args)
				stage = 2
		2:
			if (dock.model_list.item_count > 0 and dock.voice_tree.get_root() != null and dock.line_tree.get_root() != null) or frames > 6000:
				steps["models_listed"] = dock.model_list.item_count
				steps["hardware_text"] = dock.hardware_label.text
				steps["disc_text"] = dock.disc_label.text
				steps["voices"] = dock.voice_tree.get_root().get_child_count() if dock.voice_tree.get_root() != null else -1
				steps["lines"] = dock.line_tree.get_root().get_child_count() if dock.line_tree.get_root() != null else -1
				steps["people"] = range(dock.who_pick.item_count).map(func(i: int) -> String: return dock.who_pick.get_item_text(i))
				steps["languages"] = range(dock.language_pick.item_count).map(func(i: int) -> String: return dock.language_pick.get_item_text(i))
				steps["status"] = dock.status.text
				# Name the first voice from the dock, as a click would.
				if dock.voice_tree.get_root() == null or dock.voice_tree.get_root().get_child_count() == 0:
					stage = 3
					return false
				dock.voice_tree.get_root().get_child(0).select(0)
				dock.name_edit.text = "Big Al"
				dock.name_voice()
				stage = 3
				frames = 0
		3:
			if (not dock.lab.busy() and frames > 30) or frames > 3000:
				steps["named_status"] = dock.status.text
				steps["cutscene"] = cutscene()
				dock.lab.stop()
				root.remove_child(dock)
				dock.free()
				var file := FileAccess.open(OS.get_cmdline_user_args()[0], FileAccess.WRITE)
				file.store_string(JSON.stringify(steps))
				file.close()
				quit()
				return true

	return false


func start_dock(args: PackedStringArray) -> void:
	dock = VoicesDock.new()
	dock.python = args[1]
	dock.tools = args[2]
	dock.data = args[3]
	dock.overrides = args[3].path_join("overrides")
	dock.game = "rac1"
	dock.version = "ntsc"
	root.add_child(dock)
	dock.begin()


## A subtitle's voice line: made beside it, then a second language's file added to the same one.
func cutscene() -> Dictionary:
	var scene: Node = ClassDB.instantiate("Cutscene")
	scene.name = "Intro"
	var actor: Node = ClassDB.instantiate("CutsceneActor")
	actor.name = "Clank"
	scene.add_child(actor)
	var line: Node = ClassDB.instantiate("CutsceneSubtitle")
	line.name = "Line1"
	scene.add_child(line)
	actor.owner = scene
	line.owner = scene
	line.set("start", 2.5)
	line.set("speaker", line.get_path_to(actor))
	line.set("text", {"en": "Hello.", "fr": "Bonjour."})
	root.add_child(scene)
	var sound: Node = VoicesDock.add_voice_line(line, "en", "voices/intro_line1_en.wav")
	var again: Node = VoicesDock.add_voice_line(line, "fr", "voices/intro_line1_fr.wav")
	var result: Dictionary = {"class": sound.get_class(), "same": sound == again, "name": String(sound.name),
		"kind": sound.get("kind"), "start": sound.get("start"), "files": sound.get("files"),
		"source_is_speaker": sound.get_node_or_null(sound.get("source")) == actor, "owned": sound.owner == scene,
		"file": VoicesDock.voice_file_of(line, "en")["relative"]}
	scene.free()
	return result
