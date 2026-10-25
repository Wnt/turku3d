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

## 2. Working rules (non-negotiable)

1. **Fix things where they belong.** You may edit any part of the system, so do not leave workarounds in your own area
   for a problem elsewhere. Remove the code you replace in the same change, and leave no commented-out code.
2. **No feature flags** unless you are A/B-measuring two real alternatives. Then register the flag (owner, reason,
   removal condition counted in landed merges, at most 10) and remove it with the losing path once decided.
3. **Dead code is deleted**, not kept "just in case".
4. **Green before done.** Run the quality gate (`scripts/dev/gate` once Wave 0 lands; until then the area's `make
   *-test` targets) for what you touched. If you cannot make it green, finish with `## Result: BLOCKED`, the failing
   command and its output. Never weaken a test or a check to pass.
5. **Fast tests.** Run only the tests relevant to your change, in parallel. Unit tests use small synthetic fixtures, not
   real tiles. The target is 5–20 s for the gate on a typical change. If yours is slower, fix that too.
6. **Commit** on your branch with clear messages, and end each message with
   `Co-Authored-By: Codex (gpt-6-astra) <noreply@openai.com>`. Do not merge to `main`: the orchestrator lands branches.
7. **Isolation.** Use only your sandbox's ports, data/build/cache dirs and browser profile. Never kill processes you
   did not start, and never use `pkill -f` / `pgrep -f` with a pattern that appears in your own command line.
8. **No paid services** and no new API keys. Secrets live in `~/.config/turku3d/`, are never copied into the repo and
   are never printed. Codex image calls may use the weekly quota up to 100 %; the runners already check it.
9. **Language: English for everything in this project**: code, comments, docs, commit messages, progress updates
   and your final report. This overrides any personal default language setting. Match the surrounding style and keep
   each area's README current with exact commands.

## 3. Coordinates and tiles (from `pipeline/turku3d/config.py`, do not redefine)

- EPSG:3877 + N2000. Origin O = E 23459500, N 6704500, H 0. Engine axes: x = E − E0, y = H, z = −(N − N0).
- Visual tile L0 = 250 m (`t0_i_j`). Collision tile = 62.5 m (16 per visual tile). Phase 1 AOI = the 4 visual tiles
  `t0_0_0 t0_0_1 t0_1_0 t0_1_1` plus a 50 m margin.
- `.col` collision format: `docs/specs/col-format.md`. Materials: `docs/specs/materials.md`, generated into JSON/TS.
  Tiles and manifest: `docs/specs/tiles.md`.

## 4. Layer contract (Wave 1)

Pipeline steps produce **layers**. The assembler (`turku3d.package`, brief W1-04) composes them into the shipped
`.col`, GLB and tileset. A layer never writes shipped artifacts itself.

```
~/turku3d/data/build/<sandbox-or-main>/layers/<layer>/<collision_tile_id>.col.npz
    positions float64 (N,3) world-local (engine axes), indices uint32 (M,3), material uint8 (M,)
    primitives: optional structured array (kind box|capsule|cylinder, center xyz, half-extents/radius/height, yaw, material)
~/turku3d/data/build/<sandbox-or-main>/layers/<layer>/<visual_tile_id>.vis.glb
    glTF 2.0, world-local metres, one mesh per object, extras: {layer, object_id, material_key}
    UV convention: ground = planar E/N metres; facades = u metres along the facade from its start vertex,
    v metres up from the facade base; roofs = planar E/N metres
~/turku3d/data/build/<sandbox-or-main>/layers/<layer>/MANIFEST.json   (inputs + params + code-version hash, tiles written)
```

Layers in Phase 1: `ground` (W1-01), `structures` (W1-02: stairs, walls, fences, ramps, quays), `buildings` (W1-03),
`vegetation` (optional), `props` (optional). Seam rule: vertices on a collision-tile edge must be bitwise identical in
neighbouring tiles. Each layer owns that rule and the validator checks it.

Texture assets are looked up by `material_key`:

- Library materials: `textures/library/<material_key>/{albedo,normal,orm}.ktx2`.
- Per-building facades: `~/turku3d/data/textures/facades/<building_id>/<facade_index>.{ktx2,json}`.
- The assembler binds them. A missing texture falls back to the library material named by the building spec.

## 5. Finishing

Your final message (the run's result) has these sections:

- `## Result: DONE` or `## Result: BLOCKED`
- `## What changed`
- `## How to verify` (exact commands)
- `## Gate output` (summary + timing)
- `## Follow-ups` (only real, concrete items)
