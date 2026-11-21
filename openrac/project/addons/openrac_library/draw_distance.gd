@tool
extends RefCounted
## The editor's light mode (Editor Settings > Interface > Editor > Display > Light Mode): ties and
## shrubs are drawn only as far as the game draws them (their rc1_draw_distance), so a slow machine
## draws far less of a big level. Set on the renderer only: nothing of it is saved, and the nodes'
## own visibility range comes back when the light mode is off.

## The groups under Game whose instances carry rc1_draw_distance.
const GROUPS: PackedStringArray = ["Ties", "Shrubs"]

## The scenes drawn at the game's distances now, by instance id.
static var lit: Dictionary = {}


## Whether the editor runs in its light mode (the editor sets EditorInterface's meta).
static func light_mode() -> bool:
	return bool(EditorInterface.get_meta("openrac_light", false))


## The edited level's ties and shrubs drawn as far as the game does in the light mode, else as
## their nodes say. Called deferred: the scene is the one edited by then.
static func apply_edited() -> void:
	var root: Node = EditorInterface.get_edited_scene_root()
	var light: bool = light_mode()

	# Nothing new: the scene is drawn this way already.
	if root == null or light == lit.has(root.get_instance_id()):
		return

	if light:
		lit[root.get_instance_id()] = true
	else:
		lit.erase(root.get_instance_id())

	var game: Node = root.get_node_or_null("Game")

	if game == null:
		return

	for group in GROUPS:
		var holder: Node = game.get_node_or_null(group)

		if holder == null:
			continue

		for placed in holder.get_children():
			var distance: float = float(placed.get_meta("rc1_draw_distance", 0.0)) if light else 0.0

			for mesh in placed.find_children("*", "GeometryInstance3D", true, false):
				var shape := mesh as GeometryInstance3D
				var end: float = distance if distance > 0.0 else shape.visibility_range_end
				RenderingServer.instance_geometry_set_visibility_range(shape.get_instance(),
					shape.visibility_range_begin, end, shape.visibility_range_begin_margin,
					shape.visibility_range_end_margin, int(shape.visibility_range_fade_mode) as RenderingServer.VisibilityRangeFadeMode)
