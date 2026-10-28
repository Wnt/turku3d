# Common rules for every Codex brief

Every brief in this directory starts with "Read docs/briefs/COMMON.md". Once Wave 0 has landed, AGENTS.md becomes the
single policy file and this document keeps only the layer contracts (§3–§4).

## 1. Goal and context

- The project is a GTA V-fidelity open world of Turku that runs in desktop Chrome: three.js r186 (`three/webgpu`), Rapier
  0.21 in a physics worker, Vite, and 3DTilesRendererJS.
- The first goal is a **playable office block as soon as possible**: Läntinen Rantakatu 15, E 23459668 N 6704254.
  You walk and drive around it, and curbs and stairs are felt both on foot and in a car. You cannot pass through
  buildings. Facades look like the real ones. It is played with an Xbox Elite Series 2 controller.
- Read `docs/ARCHITECTURE.md` (decisions D1–D8, §4 Phase 1 task table with acceptance criteria) and `docs/STATUS.md`.
  The research reports in `docs/research/` hold verified commands and pitfalls.
- Everything now runs on this Mac (M1 Max, 10 cores, 64 GB). Data root: `~/turku3d/data`. Raw data is read-only for
  you and shared. Write into your own sandbox dirs (Wave 0 tooling provides them). osgallery-dev is legacy: no new
  work there.

## 2. Working rules

The single policy source is [AGENTS.md](../../AGENTS.md), including the English-language rule,
isolation, verification, commits and run results. Tool commands: [scripts/dev/README.md](../../scripts/dev/README.md).
Send a progress message only when a stage finishes or the plan changes, not while polling or waiting.

Use `codex-task finish` to rebase, run agent-selected affected tests within 30 seconds,
merge into main and deploy Pages immediately. Five-second curl smoke and one selective
async run (ten minutes maximum) follow deployment. Read AGENTS.md for the current
commands, active-agent register and forward-fix policy; this brief adds no verification
or delivery requirements.

## 3. Coordinates and tiles (from `pipeline/turku3d/config.py`, do not redefine)

- EPSG:3877 + N2000. Origin O = E 23459500, N 6704500, H 0. Engine axes: x = E − E0, y = H, z = −(N − N0).
- Visual tile L0 = 250 m (`t0_i_j`). Collision tile = 62.5 m (16 per visual tile). Phase 1 AOI = the 4 visual tiles
  `t0_0_0 t0_0_1 t0_1_0 t0_1_1` plus a 50 m margin.
- `.col` collision format: `docs/specs/col-format.md`. Materials: `docs/specs/materials.md`, generated into JSON/TS.
  Tiles and manifest: `docs/specs/tiles.md`.

## 4. Layer contract (Wave 1)

Pipeline steps produce **layers**. The assembler (`turku3d.package`, brief W1-04) composes them into the shipped
`.col`, GLB and tileset. A layer never writes shipped artifacts itself. The precise v1 schema and shared Python helpers are in
[`docs/specs/layers.md`](../specs/layers.md) (`turku3d.layers`). Sandbox paths use
`$TURKU3D_WORK/build/layers/<layer>/`.

```
$TURKU3D_WORK/build/layers/<layer>/<collision_tile_id>.col.npz
    positions float64 (N,3) world-local (engine axes), indices uint32 (M,3), material uint8 (M,)
    primitives: optional structured array (kind box|capsule|cylinder, center xyz, half-extents/radius/height, yaw, material)
$TURKU3D_WORK/build/layers/<layer>/<visual_tile_id>.vis.glb
    glTF 2.0, world-local metres, one mesh per object, extras: {layer, object_id, material_key}
    UV convention: ground = planar E/N metres; facades = u metres along the facade from its start vertex,
    v metres up from the facade base; roofs = planar E/N metres
$TURKU3D_WORK/build/layers/<layer>/MANIFEST.json   (inputs + params + code-version hash, tiles written)
```

Layers in Phase 1: `ground` (W1-01), `structures` (W1-02: stairs, walls, fences, ramps, quays), `buildings` (W1-03),
`vegetation` (optional), `props` (optional). Seam rule: vertices on a collision-tile edge must be bitwise identical in
neighbouring tiles. Each layer owns that rule and the validator checks it.

Texture assets are looked up by `material_key`:

- Library materials: `textures/library/<material_key>/{albedo,normal,orm}.ktx2`.
- Per-building facades: `$TURKU3D_WORK/textures/facades/<building_id>/<facade_index>.{ktx2,json}`.
- The assembler binds them. A missing texture falls back to the library material named by the building spec.
