@tool
extends Node3D
## The beams an electric gate (class 838, Rilgar) draws while it is shut (state 1), which the game's
## code draws and no class model holds (effects.py writes their tables, effects/electric_gate.json).
## rc1/moby.gd adds them to a gate: five beams across it, 1 to 5 above it, each a thin outer and a
## thicker inner quad, their colours pulsing (rc1/electric_gate.gdshader). They move with the
## cutscene preview's time when there is a cutscene, else with the editor's own. Display only: the
## node is not saved.
##
## The game draws the beams of every shut gate in the group of the first gate it updates; Rilgar's
## gates are all in one group, so here every shut gate draws its own.

const SHADER: Shader = preload("res://rc1/electric_gate.gdshader")
const Nanotech: Script = preload("res://rc1/nanotech.gd")

## The beams, from 1 to 5 above the gate.
const BEAMS: int = 5

## The gate it belongs to.
var gate: Node3D

var material: ShaderMaterial
var own_seconds: float = 0.0

## The cutscene whose preview time the beams follow.
var cutscene: Node

## The time and the gate's transform last sent (rc1/nanotech.gd): unchanged ones are not sent.
var shown_seconds: float = -1.0
var shown_transform: Transform3D = Transform3D(Basis(), Vector3.INF)

## The tables of every level read so far, by their file.
static var tables_of: Dictionary = {}


## Adds the beams to the gate, or nothing when its level has no tables.
static func attach(to: Node3D, effects_dir: String) -> Node3D:
	var tables: Dictionary = read_tables(effects_dir)

	if tables.is_empty():
		return null

	var effect := new()
	effect.name = "ElectricGateEffect"
	effect.gate = to
	effect.top_level = true
	to.add_child(effect)
	effect.build(tables, effects_dir)
	Nanotech.watch_screen(effect, (effect.get_node("Beams") as MeshInstance3D).get_aabb().grow(1.0))
	return effect


## effects/electric_gate.json, read once per level; empty when it is not there.
static func read_tables(effects_dir: String) -> Dictionary:
	var path: String = effects_dir.path_join("electric_gate.json")

	if not tables_of.has(path):
		var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path)) if FileAccess.file_exists(path) else null
		tables_of[path] = parsed if parsed is Dictionary else {}

	return tables_of[path]


func _process(delta: float) -> void:
	follow()
	var seconds: float = Nanotech.effect_seconds(self, delta)

	if seconds != shown_seconds:
		shown_seconds = seconds
		material.set_shader_parameter("seconds", seconds)


## The gate's place and turn, without its scale (the game draws the beams with the rows of its
## rotation, at its position).
func follow() -> void:
	if gate != null and is_instance_valid(gate) and gate.global_transform != shown_transform:
		shown_transform = gate.global_transform
		global_transform = shown_transform.orthonormalized()


func build(tables: Dictionary, effects_dir: String) -> void:
	material = ShaderMaterial.new()
	material.shader = SHADER
	material.set_shader_parameter("picture", Nanotech.load_picture(effects_dir.path_join(String(tables.get("picture", "")))))

	for key in ["outer", "inner"]:
		var pair: Array = tables[key + "_colours"]
		material.set_shader_parameter(key + "_from", rgba(pair[0]))
		material.set_shader_parameter(key + "_to", rgba(pair[1]))

	var node := MeshInstance3D.new()
	node.name = "Beams"
	node.mesh = beams_mesh(tables)
	node.material_override = material
	node.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(node)
	follow()


static func rgba(c: Array) -> Vector4:
	return Vector4(c[0], c[1], c[2], c[3])


## Each beam's inner quad, then its outer one, raised by the beam's number plus one; UV2 holds
## the beam and whether the quad is the inner one.
static func beams_mesh(tables: Dictionary) -> ArrayMesh:
	var vertices := PackedVector3Array()
	var uvs := PackedVector2Array()
	var beams := PackedVector2Array()

	for beam in range(BEAMS):
		for key in ["inner", "outer"]:
			for k in [0, 1, 2, 1, 3, 2]:
				var p: Array = tables[key][k]
				var uv: Array = tables["uvs"][k]
				vertices.append(Vector3(p[0], p[1], p[2] + beam + 1))
				uvs.append(Vector2(uv[0], uv[1]))
				beams.append(Vector2(beam, 1.0 if key == "inner" else 0.0))

	var arrays: Array = []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = vertices
	arrays[Mesh.ARRAY_TEX_UV] = uvs
	arrays[Mesh.ARRAY_TEX_UV2] = beams
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return mesh
