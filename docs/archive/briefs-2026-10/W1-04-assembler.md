# W1-04: Tile assembler and packaging: layers to shipped tiles (Phase 1 task 1.10)

Read `docs/briefs/COMMON.md` and `AGENTS.md`. You own `turku3d.package` and the layer contract in COMMON §4.

## Goal

Turn the per-layer outputs into the shipped game data: `.col` collision tiles, visual GLBs with KTX2 textures and
meshopt, `tileset.json` and `manifest.json`. Make `make tile` use the real layers instead of the stub.

## Deliverables

1. **Layer contract implementation**:
   - Reader and writer helpers for `.col.npz` / `.vis.glb` / `MANIFEST.json` in one small module that every layer
     imports. If COMMON §4 is underspecified, write the precise spec into `docs/specs/layers.md` and update COMMON §4
     to point at it.
   - Land this part **first**, as a small early commit with tests, because the other Wave 1 agents import it. Until
     then they follow COMMON §4.
2. **Assembler**:
   - Per collision tile: merge the layers' triangles and primitives, check seams across neighbours, and write `.col`.
   - Per visual tile: merge the layers' GLBs, bind textures by `material_key` (library and per-building facades;
     COMMON §4), then run KTX2 (ETC1S albedo, UASTC+zstd normals) before meshopt.
   - Never use `gltf-transform optimize` defaults: no simplify and no join on collision or curb meshes (see
     `docs/research/engine.md`).
   - Missing layers are allowed: a tile with only `ground` still ships.
3. **Coarse LODs**: 500 m and 1 km parents (merged roofs plus an ortho-draped DTM) so the far view is not empty around
   the AOI. Keep it simple.
4. **`make tile`** uses the layers and keeps the stub only as a test fixture generator. Delete the stub path from
   production code.
5. **Runtime robustness** (Phase 0 follow-ups, in the game's streaming code):
   - Retry collision tiles that fail transiently, with backoff; size or CRC errors are permanent.
   - A physics worker step that throws must not freeze physics: recover or surface the error in `__qaError`.

## Acceptance

- The L0 visual tile is ≤ 8 MB brotli and `.col` ≤ 1 MB. Decoded geometry is identical to the source within 2 mm, and
  a round-trip test proves it.
- `make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"` assembles whatever layers exist and the e2e tests pass.
- Re-running with unchanged layers is a no-op (content-hash cache).
- Fast unit tests with synthetic layers.
