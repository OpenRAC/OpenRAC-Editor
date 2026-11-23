# Terrain tools

OpenRAC Editor edits a level's ground as a mesh, not a height map: rocks can hang over the water,
cliffs can be undercut, and the light is baked into the ground's colours as the game's own levels
have it. The tools are in the **Terrain** panel, as MCP tools (`terrain_*`, [MCP.md](MCP.md)), and on
the command line:

```sh
python3 openrac/tools/openrac_editor.py terrain OP levels/level_NN/terrain.glb [options]
```

They need numpy (`python3 -m pip install numpy`).

Every change copies `terrain.glb` to `.terrain-backups/` first (the last 20 are kept); `undo` takes
the newest back. Places are the game's: x and y across, z up, about a metre a unit.

## Painting in the 3D view

Press **Terrain brush** above the 3D view (or **Brush mode** in the Terrain panel). The 3D view then
paints instead of selecting: a circle follows the mouse on the ground, its colour shows the brush
(green raise, red lower, blue smooth or flatten, white paint). Click, or hold the left button and
drag; a drag is one change, so one Undo takes it back. A right drag and the wheel still move the
camera. `[` and `]` make the brush smaller and bigger, Esc leaves the mode. Pick the brush, picture,
size and strength in the Terrain panel.

## What each one does

| Op | What it does |
|---|---|
| `info` | Triangles per 100 m², shares of flat ground, walls and overhangs, light spread, share in shadow, texture tile size, textures used, and advice against Pokitaru's numbers |
| `sculpt --mode raise\|lower\|smooth\|roughen\|flatten` | A brush on the mesh (`--at X Y Z --radius R --strength S --hardness H`; `--then X Y Z`, repeated, adds the spots of a drag); raise and lower move along the surface, so walls and overhangs work too |
| `paint --texture textures/terrain_0003.png` | The faces in the brush get that picture (the game draws one per triangle). Filters: `--slope-min/--slope-max` in degrees (0 flat, 90 wall, 180 ceiling), `--z-min/--z-max`; `--noise N --coverage C` paints in patches |
| `uv --tile 3` | Box projection per face: floors from above, walls from the side, pictures upright, one tile every 3 units (Pokitaru's size) |
| `rock-add --at X Y Z --radius R --height H` | Adds a rock to `terrain-rocks.json`: a narrow foot (`--undercut`), a shoulder (`--bulge`), `--taper`, `--roughness`, `--warp` (how far the outline wanders), `--stretch SX SY`, `--yaw`, `--seed` |
| `build [--step 2] [--budget N] [--collision FILE]` | The rocks become the node `Rocks`: a closed mesh (surface nets over a density field), textured by slope and mapped, and optionally a collision mesh |
| `bake-light [--sun AZ EL] [--region X0 Y0 X1 Y1]` | Sun with shadows, ambient occlusion from 24 sky directions, cool sky and warm bounce, into the vertex colours (0 to 2, 1 is normal) |

## The recipe

`terrain-rocks.json` next to `terrain.glb`:

```json
{"sea": 223, "blend": 4, "tile": 3, "lip": 4,
 "textures": {"top": "textures/terrain_0003.png", "lip": "textures/terrain_0012.png",
              "wall": "textures/terrain_0017.png", "creeper": "textures/terrain_0011.png",
              "under": "textures/terrain_0019.png", "shore": "textures/terrain_0027.png"},
 "rocks": [{"x": 620, "y": 520, "z0": 205, "z1": 265, "radius": 30, "undercut": 0.35, "bulge": 0.2}],
 "caves": [{"at": [640, 520, 230], "radius": 8}]}
```

`sea` drops the faces deep under water and puts the shore picture at the waterline. `blend` melts
neighbouring rocks together. The pictures above are Pokitaru's; another level names its own.
`top_slope` (0.75) is how upright a face must be to get the top picture (lower: grass further down
rounded tops), `creepers` (0.42) the share of walls under creepers, and `tiles` a kind's own picture
size, e.g. `{"wall": 5}`. `"uv": "wrap"` wraps the walls' pictures round each rock (no seams
where a box projection changes axis); `smooth_passes` (2) evens out lone triangles of one picture
in another; `lip_top_area` (3) is how much top a lip needs above it, and `min_top_area` (0) how much top a 3-unit cell needs to get grass at all (small ledges stay rock, no scattered green stripes). `bake-light --ambient 0.2`
keeps walls in shade from going black. Each rock is only worked out inside its own box, so one recipe can hold a
whole archipelago.

## Baked light for the whole scene

`bake-light` can light your own models with the terrain, in one bake, so they shade each other
(the house shades its deck, the deck its stilts, a cliff the house) and torches light both:

```
terrain_tools.py bake-light terrain.glb --models models/house.glb models/props.glb \
    --subdivide 2.2 --glow 0.9 --model-tint 0.5 --keep-tint --lights lights.json --ambient 0.2
```

- `--subdivide S` splits the models' faces to S units first: vertex light needs vertices, so a
  big wall gets the shadow of a roof only when it is split.
- `--glow G` keeps vertices brighter than G as they are (flames, lamp glass, glowing signs).
- `--model-tint T` sets the models' colour under the light (the port draws model colours at half
  the terrain's scale: 0.5 matches it).
- `lights.json`: `[{"at": [x, y, z], "colour": [1.5, 1.0, 0.5], "radius": 9}]`. Each falls off to
  nothing at its radius and is blocked by anything between it and the surface.

Baked light never flickers: it is the same every frame, at no cost in the game.

## Good to know

- Pokitaru, measured: 54 triangles per 100 m², 17% flat, 30% walls, 25% overhangs, light spread
  0.44 with 41% of the ground in shadow, a picture every 3 units. `info` compares with these.
- Bake the light last: sculpting and building change the shape, painting does not need a new bake.
- A terrain node must not be named `Terrain_NNN` (the game's own pieces): the port would cull it as
  that piece. `Rocks` is fine.
- New land needs collision: `build --collision` writes it, sized with `--collision-budget`. The
  level's collision must still fit in the place the game had for it.
