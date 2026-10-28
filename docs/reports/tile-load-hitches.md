## Result: BLOCKED

The code-side slice and local reproduction are complete. Required matched Air baseline/validation and
`codex-task finish` delivery remain pending. Jonni is playing in the orchestrator-owned Air session;
wait for its release message. No Air session was taken or interrupted. No Air performance target is claimed.

## What changed

- Final receive/cast-shadow settings and shadow proxies now precede hidden-model GPU preparation.
  Publication preserves shader-affecting flags, including vegetation's selected LOD flags. Cancelled
  unpublished models release derived shadow geometry; residency excludes proxy triangle duplicates.
- r186 asynchronous shader stages receive a view of their captured target, MRT, flow context and render
  context. The live renderer restores its own state immediately. A scoped context-manager adapter selects
  the nesting depth observed by the actual post/reflection pass; r186 includes that context in shader keys.
- Normal, GTAO, beauty and reflection image updates skip native precompilation. Previously a surface's
  AO dependency could redraw the resident world at a different depth during warmup.
- The shared GTAO effect retains its setup graph for its renderer/camera. r186 otherwise replaces its
  fullscreen material graph for every streamed surface builder, causing repeated synchronous AO builds.
  Normal and AO image passes use the base flow context. Native sample-count updates remain active.
- Draft glTF and character decoding share two Meshopt workers. Without `useWorkers`, the decoder's async
  API performs WASM decompression in main-thread microtasks. Three representative frozen tiles decoded
  5.0–8.8 MiB each in 5.8–8.8 ms on this Mac before parsing.
- Capture-only tools support the draft-only world, freeze served payloads and external images, and record
  texture uploads, synchronous node builds and publication alongside RAF/physics/heap evidence.

A real WebGPU synthetic reproduction on the M1 Max demonstrates the cache invalidation:

| 640x480, medium, CSM + post | Original | Fixed |
|---|---:|---:|
| receiveShadow during warmup → publication | false → true | true → true |
| First-visible streamed-surface shader builds | 2 | 0 |
| First-visible shared AO shader builds | 1 | 0 |
| First visible draw CPU time, one run (ms) | 38.7 | 4.1 |

The original path also reads null/default pass state in asynchronous generation stages. The final fixture
asserts cache reuse with the actual renderer and rejects publication-time surface/AO rebuilding. It is
not an Air benchmark or a GPU execution-time comparison. The archived original source is recorded in
`$TURKU3D_WORK/reports/tile-hitches/baseline-commit.txt`; its local fixture uses the same initialized floor.

The final local 75-second Aura flight, Vite dev, WebGPU, medium, 1600x913 CSS/DPR2, fixed autumn time:

| Metric | Final local workers run |
|---|---:|
| RAF p50 / p95 / max | 16.665 / 16.670 / 50.000 ms |
| Frames over 100 ms | 0 |
| Physics publication age p50 / p95 / max | 6.80 / 17.24 / 29.63 ms |
| Reported heap first → last | 307.0 → 353.1 MiB (+46.0 MiB) |
| Page errors | 0 |
| AO shader builders during flight | 0 |

This is a low camera flight with streaming focus, not a driving acceptance run. Adaptive output changes
from 2541x1450 to a 1270x725 floor, with a 1397x797 probe. The heap snapshots include asset residency,
retained capture data and GC phase; they do not establish a leak rate. An exploratory run before the
Meshopt worker change still reached 117 ms. An earlier traced run before AO retention reached 200 ms
and produced 264 in-frame builders, including repeated AO builds. Recording modes differ, so those
explorations are not a controlled whole-flight before/after speedup claim.

Frozen served input snapshot identity: `ae432b0eeca3cfc2ee093087855f702759f0531d5db99d8654617178d045b427`
(relative paths plus content SHA256 digests), including 16 explicitly downloaded served route cells.
No city data, traces or screenshots are committed. Evidence is under
`$TURKU3D_WORK/reports/tile-hitches/`: `local-matched-before.json`, `local-after-stable-ao.json`,
`local-flight-frozen/`, `local-flight-stable-ao/` and `local-flight-workers/`.

## How to verify

```sh
source ../sandbox.env
npm --prefix game run typecheck
npm --prefix game test -- tests/unit/compile-model.test.ts tests/unit/visual-tiles.test.ts \
  tests/unit/shadow-batch.test.ts tests/unit/preparation-budget.test.ts tests/unit/coarse-tiles.test.ts \
  tests/unit/render.test.ts tests/unit/vegetation-lod.test.ts tests/unit/reflections.test.ts \
  tests/unit/shadow-materials.test.ts tests/unit/ground-binding.test.ts
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE"
# Another sourced terminal, through the browser scheduler:
python3 scripts/dev/host_slots.py browser -- node tools/perf/air-scenarios/local-warmup.mjs \
  "http://localhost:$TURKU3D_PORT_BASE/" "$TURKU3D_WORK/reports/tile-hitches/local-warmup.json"
```

Frozen draft service, local/Air flight acquisition and summarization commands are in
`tools/perf/air-scenarios/tile-hitches.md`. Use `TURKU3D_RENDER_TRACE=0` only for light local exploration;
Air attribution uses the default trace mode. The sandbox copy of the external recorder uses the previously
documented String.raw probe correction; external shared recorder files are untouched.

## Gate output

Ten affected unit files: 46 tests passed. The final rebased unit/type selections ran in parallel
within a hard 30-second budget and finished in 2.513 seconds. Diff whitespace checks passed. The real-WebGPU
fixture passes both surface and AO publication cache assertions. Final real-tile local flight completes
with no page errors. No assertions were weakened. The reflection unit fixture now supplies initialized
render-context state and additionally asserts the observed depth selection.

Before delivery, rebase and rerun selected unit/type checks through `codex-task finish` within its hard
30-second budget. The Air comparison is still required; there has been no merge, Pages upload or
post-deployment async selection for this unfinished slice.

## Follow-ups

After the Air release message, freeze current-main baseline source, run the repeatable Air flight and
driving scenario with the existing frozen draft inputs, then run identical scenarios on the rebased fix.
Attribute every frame over 100 ms and report p50/p95/max, physics age and heap growth. The Air's normal
driving target remains no frame over 50 ms. Correct measured remaining sources, then use
`codex-task finish` to merge/deploy and start the selected bounded async run.
