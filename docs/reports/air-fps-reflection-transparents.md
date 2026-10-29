# River reflection cost and generated attribute lifetime (2026-10-30)

Client-only follow-up to `33409824`. Default medium quality previously refreshed the
river on every frame with any camera matrix change. It now refreshes at 12 Hz for
slow movement, capped at 30 Hz for larger movement. Low uses 8/20 Hz, high 20/60 Hz.
Motion accumulates from the last capture (0.5 m / 1 degree); missing output and explicit
refresh requests bypass the budget. Deterministic QA keeps every-frame updates.
Planar reflection, water normals, Fresnel, sun highlights and authored surface maps remain.
The trade-off is slight reflection lag while moving slowly.

Reflection admission now rejects blended/transmissive surfaces, mutable structural
props and geometry below four metres in world-space bounding diameter, as well as the
existing actor/vegetation/prop exclusions. Ordinary static batching remains in place.
No engine uniform-refresh bypass was added: moving lights, camera and pass contexts
still need their correct uniforms.

## Transparent diagnosis

The three real sampled tiles contain only 1–2 transparent river draws, and zero
transparent reflection draws, before and after. Vegetation is already opaque/instanced.
The supplied trace's 45.5% inclusive transparent time contains the nested water
reflection (43.6%); it is not evidence of a large transparent sorting queue. Water
stays in that queue because it intentionally skips the normal/velocity prepass.
This sample does not inventory every actor/overlay in Jonni's longer session.

## Local matched replay

Pinned Chrome for Testing 156, local M1 Max, WebGPU, medium, 960×540, TAA. Source
baseline `fa89bb2b86c5`. Three immutable deployed OCI visual tiles (`t0_0_0`,
`t0_0_-1`, `t0_1_0`), three rounds, two resident at once. Both runs use the same slow
camera oscillation and final matching pose. The table uses the last interval, which
includes the incoming tile's preparation and frames before old-tile retirement.
The harness uses production materials/passes but omits gameplay, vegetation LOD
selection and production tone mapping; its screenshot is not a visual-quality gate.

| Metric | Before | After |
| --- | ---: | ---: |
| Draw calls/frame, mean across all passes | 334.37 | 270.71 |
| Prepass objects/frame, mean | 88.96 | 88.96 |
| Beauty objects/frame, mean | 89.96 | 89.96 |
| Reflection objects/frame, mean | 77.12 | 13.46 |
| Reflection objects on an updated frame, median | 77 | 66 |
| Frames containing a reflection / measured frames | 170 / 171 | 33 / 171 |
| Shadow objects/frame, mean | 63.33 | 63.33 |
| Transparent objects/frame, beauty / reflection | 1.006 / 0 | 1.006 / 0 |
| Resident meshes / render objects at sample | 149 / 324 | 149 / 313 |
| GPU attributes after rounds 1 / 2 / 3 | 459 / 491 / 523 | 443 / 443 / 443 |
| GPU attributes after all tiles released | 118 | 22 |
| Render objects after all tiles released | 13 | 13 |
| Render CPU time, median / mean | 3.30 / 4.74 ms | 3.10 / 4.47 ms |
| Frame interval, median / p90 | 16.7 / 18.2 ms | 16.7 / 17.7 ms |
| GPU validation / page errors | 0 / 0 | 0 / 0 |

Reflection submissions fall 82.5%; total draws fall 19.0%. Local median CPU improves
only 6.1%, with both runs display-limited. Do not extrapolate these numbers into an
Air FPS claim. Jonni's Air re-profile remains the hardware verification.

The leak was real: 32 generated instance-matrix attribute views per residency round.
r186's geometry cleanup tracks node fields from only its first draw context. Later
contexts can introduce more views. Explicit ownership now gathers generated fields
across contexts and releases them on object/geometry retirement, sharing lifetime
by their underlying interleaved buffer. Ordinary geometry fields remain engine-owned.
The new attribute plateau assertion fails on the baseline (523 != 491) and passes
afterward; no assertions were relaxed. Render-object and pipeline plateaus also pass.

`renderer.info.memory.attributesSize` double-counts shared interleaved backing arrays;
in this fixture it reports 2,288,575,348 → 2,288,385,396 bytes at the last interval,
and 263,640 → 8,664 after release. These are renderer accounting values, not unique
GPU allocation bytes. They are stable across matching after-fix residency.

## Reproduce

```sh
source ../sandbox.env
npm --prefix game run typecheck
npm --prefix game test -- tests/unit/reflections.test.ts tests/unit/node-attributes.test.ts tests/unit/render.test.ts tests/unit/streaming-resources.test.ts
PROBE_QUALITY=medium PROBE_MOVING=1 MATERIAL_BASELINE=fa89bb2b86c5 scripts/dev/codex-task wait-cmd --timeout 150 -- python3 scripts/dev/host_slots.py browser -- node game/scripts/probe-material-stream.mjs reflection-before-matched 18
PROBE_QUALITY=medium PROBE_MOVING=1 scripts/dev/codex-task wait-cmd --timeout 150 -- python3 scripts/dev/host_slots.py browser -- node game/scripts/probe-material-stream.mjs reflection-after 18
scripts/dev/codex-task wait-cmd --timeout 350 -- python3 scripts/dev/host_slots.py browser -- node game/scripts/probe-reflections-deployed.mjs
```

The baseline command intentionally fails the added leak assertion. Raw per-frame counts,
CPU timings and immutable asset URLs are under `$TURKU3D_WORK/reports/material-stream/`.
The selected post-deploy check exercises the live Pages medium-quality river in a local
headless browser, asserting reflection presence, its slow-motion rate, zero reflected
transparencies and no GPU/page errors; evidence goes to `reports/reflection-deployed/`.
No automation connects to Jonni's devices. No raw city data or screenshots are committed.

## Deployment verification correction

The first async check ran before Pages propagated the upload: it loaded
`main-Bj4yx3DW.js`, while the uploaded package contained `main-BIUXxnqJ.js`.
It reported 634 reflections per plane in 634 frames and correctly failed the 12 Hz
assertion; there were no GPU or page errors. The one forward fix is to wait for the
exact packaged entry point before opening the browser, then assert the browser loaded
that entry point. Waiting is bounded at 240 seconds within a 350-second async command;
no deployment queue, build gate or rollback is involved. Rendering assertions are unchanged.
