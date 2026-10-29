# Streamed material and render-pass cost (2026-10-30)

Client-only response to the Air's late-session slowdown. Generator source and world data are unchanged.

Styled PBR/TSL materials now share graphs across resident tiles with identical source properties and final canonical texture identities. Different roof/facade images, UVs, samplers and authored parameters remain distinct. Tile owners release shared graphs only after their last user; original glTF materials are restored for the existing texture/resource owner. The existing bounded compiled-program cache remains in use.

Compatible opaque static meshes merge within 62.5 m spatial buckets, preserving transforms, facade vertex attributes and semantic index ranges. Mutable props, instances and transparent meshes retain their existing draw ownership. Retirement restores original resource ownership immediately and defers GPU disposal safely. Facade fields are repacked after merging to stay below WebGPU's eight vertex-buffer limit.

The normal/velocity prepass uses shared geometric-normal materials for ordinary opaque surfaces. Cutouts, displacement and custom vertex/depth programs retain their authored path. Beauty shading, temporal AA and GTAO remain enabled. Low quality refreshes river reflections at at most 20 Hz even while moving; other tiers retain full-rate camera motion. World, player, ground binding and ground QA share one KTX2 transcoder per renderer.

## Local comparison

Pinned headless Chrome for Testing, M1 Max, WebGPU, 960×540, low quality, temporal AA. Three real immutable OCI tiles (`t0_0_0`, `t0_0_-1`, `t0_1_0`) loaded in three rounds; two resident at once, with actual shader construction, shadows, reflection, render frames and disposal. Baseline source: `998f38a7`. This isolates streaming/rendering from navigation and does not reproduce the Air's entire 209-second session. The camera is fixed; the moving-camera reflection-rate trade-off is not included in the measured gain.

| Metric | Before | After |
| --- | ---: | ---: |
| Resident material identities, final matching pair | 88 | 87 |
| Resident meshes, including shadow batches | 163 | 149 |
| Live renderer objects | 357 | 315 |
| Cached pipelines | 63 | 52 |
| Node builders over nine loads | 323 | 247 |
| Recurring builders/minute, rounds 2–3 | 498 | 351 |
| Last interval median render CPU time | 3.5 ms | 2.9 ms |
| Last interval median / p90 frame time | 16.7 / 16.8 ms | 16.7 / 16.8 ms |
| Render objects after all tiles released | 13 | 13 |
| GPU validation / page errors | 0 / 0 | 0 / 0 |

Both runs plateau at matching residency. The baseline already includes earlier resource-lifetime and compiled-pipeline fixes; this slice reduces the remaining work rather than claiming a new leak was reproduced. Unique tile roof/ground textures explain why material identity counts fall less than graph build counts. The 13 surviving render objects belong to sky/postprocessing. Source material and facade-map preservation have focused unit coverage. Repeated batches exercise semantic row preservation and render-object release.

Raw frame intervals, CPU timings, per-round counts, immutable asset URLs and a local image are under the task sandbox's `work/reports/material-stream/`. The first batching attempt failed GPU validation because merging unpacked facade attributes; repacking fixed it and the passing run above includes that correction. No screenshots or raw city data are committed.

## Reproduce

```sh
source ../sandbox.env
npm --prefix game run typecheck
npm --prefix game test -- tests/unit/render.test.ts tests/unit/streaming-resources.test.ts tests/unit/static-batch.test.ts tests/unit/shadow-materials.test.ts tests/unit/ground-binding.test.ts
MATERIAL_BASELINE=998f38a7 scripts/dev/codex-task wait-cmd --timeout 160 -- python3 scripts/dev/host_slots.py browser -- node game/scripts/probe-material-stream.mjs before 18
scripts/dev/codex-task wait-cmd --timeout 160 -- python3 scripts/dev/host_slots.py browser -- node game/scripts/probe-material-stream.mjs after 18
```

The probe saves immutable asset URLs on its first run so subsequent comparisons reuse the same tile contents. It asserts stable material/render-object counts for repeated residency and a bounded pipeline cache. Hardware frame rate remains to be re-profiled by Jonni on his Air.
