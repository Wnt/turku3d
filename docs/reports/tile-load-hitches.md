## Result: DONE

The required Air reproduction and validation are complete. One forward slice reduces the measured
120-second driving maximum from 349.99 ms before the earlier fixes, through 66.67 ms on the published
build, to **35.27 ms**. The final run has no frames over 50 ms. This is one repeatable route measurement,
not a guarantee for every world asset or Chrome/GPU scheduling condition.

## What changed

Previously delivered fixes finalize receive/cast-shadow flags before warmup, preserve renderer pass state
through r186 asynchronous shader generation, retain the shared GTAO graph, suppress image-producing
precompilation updates and use two Meshopt workers. Publication keeps those warmed shader variants.
The local M1 Max WebGPU fixture reduced first-visible CPU draw time from 38.7 to 4.1 ms and publication
surface/AO rebuilds from 2/1 to 0/0; that fixture is diagnostic, not the Air acceptance measurement.

This single forward slice also yields shadow geometry transforms, index partitioning and buffer compaction
through the existing preparation budget, rather than building a whole tile atomically. The Air probe
measured an individual shadow preparation call at 27.5 ms. Streaming awaits the completed hidden proxy
before shader preparation/publication; synchronous standalone callers retain identical geometry behavior.
The river beauty material now participates in warmup, while transparent water stays excluded from the
opaque prepass. No draft request ordering changed.

Capture tooling now handles public multiplayer startup, clears autosaves after unloading, extends the
real car's route as graphs arrive, and records a trace clock marker after recording starts. The runtime
is capture-only and does not ship in gameplay.

### Air evidence, 2026-10-28

M1 MacBook Air 8 GB, headed Chrome 156, WebGPU. All three primary runs use public **production dist**
builds, cold browser cache/storage, fixed time `2026-10-15T11:00:00Z`, one isolated multiplayer room,
the same frozen draft inputs and the identical 120-second real-physics driving script. Start near
(174, 258); finish near (-365, 496), crossing three visual tile columns. No in-run teleporting.
Requested viewport is 1600x913 CSS/DPR2; game quality/adaptive resolution stays active. Actual recorded
canvas ranges: baseline 1270x725–2160x1232; published 1906x1087–2541x1450; forward
1779x1015–2541x1450. Therefore these are gameplay results with automatic resolution, not a fixed
3200x1826 rendering benchmark. Arrival timing/physics vary slightly between runs.

| Light capture, same 120 s scenario | Before fixes | Published Pages | Forward slice |
|---|---:|---:|---:|
| RAF p50 / p95 (ms) | 16.665 / 18.640 | 16.665 / 18.635 | 16.665 / 18.625 |
| Longest RAF interval (ms) | 349.990 | 66.665 | 35.270 |
| Frames >100 ms | 7 | 0 | 0 |
| Frames >50 ms | 14 | 1 | 0 |
| Physics publication age p50 / p95 / max (ms) | 6.11 / 17.65 / 24.17 | 5.76 / 17.59 / 33.72 | 6.17 / 17.42 / 40.30 |
| Heap first → last (MiB) | 361.39 → 316.73 | 377.05 → 317.61 | 302.34 → 317.73 |
| Heap change / peak (MiB) | -44.66 / 534.38 | -59.44 / 446.97 | +15.39 / 461.09 |
| Shader builders total / in-frame | 480 / 261 | 132 / 40 | 131 / 37 |
| Driven distance (m, 1 Hz samples) | 652.19 | 645.87 | 645.75 |
| Page errors | 0 | 0 | 0 |

Heap includes residency, instrumentation retention and GC phase; these endpoints do not establish a
leak rate. Physics age uses the worker's actual publication timestamp. The recorder's obsolete
receipt hook does not observe this game's physics API, so its zero step counts/stale receipt age are
not used. Message round-trip samples are unavailable in these captures.

The exact published comparison is Pages tree `f90011324ba2952008f8eae1926e5f864cf4e90d`, release source
`7847e0e9071b849b822b0188eb88f1d4049b7e69`, served through the sandbox's Air tunnel. The historical
pre-fix release cannot consume current facade-kit v3 assets. The before build is explicitly a
**counterfactual**: archive that same current release, restore `compileModel.ts`, `post.ts`, `water.ts`
and `tiles.ts` from `148a553a`, remove preparation/release callbacks and disable Meshopt workers.
Current schema/facade support and draft request ordering remain intact. `baseline.mjs` reproduces it;
this is not a claim to have measured the historical deployed release with today's incompatible assets.
Forward Air build is the renderer source committed as `79547de3`, before rebase onto concurrent
scheduling merges. The final delivery retains those renderer changes and integrates current main.

A separate traced pre-fix diagnostic (nonpublic UI, same input route) records 12 intervals >100 ms.
Eleven contain 111–499 ms of synchronous node construction; one 716.68 ms interval has two GPU-thread
tasks totaling about 705 ms and little main-thread work. GC overlaps some intervals by 6–15 ms and is
not their dominant cost. Exact interval attribution is retained in local `drive-hitches.json`.
Texture-upload wrappers do not explain these shader stalls; KTX2/parse/collision costs are not proven
zero. A traced exact-Pages drive has no interval >100 ms, max 99.79 ms: its largest main task runs
microtasks for 93.8 ms. The light probe separately identifies shadow preparation at 27.5 ms; neither
observation proves shadow work explains the entire traced microtask task. Trace/allocation overhead
is why those diagnostic maxima are kept separate from the primary light table. CPU sample stacks
were absent in this recorder trace; attribution uses explicit work timestamps and trace task spans.

Frozen inputs: 345 files, 16 served cells, digest
`20a4f8ff079b2b9530fe33d0cb5fbe05b524ac7b51ade898df74f6b5853c3350`
(SHA256 over sorted relative UTF-8 paths, NUL, and each binary SHA256 digest).
Local-only evidence under `$TURKU3D_WORK/reports/tile-hitches/`:
`air-before-light`, `air-pages-light`, `air-forward-light` (primary),
`air-before-drive-final` and `air-pages-controlled` (traced diagnostics).
No city data, traces or screenshots are committed. The owned Air session was stopped after validation.

## How to verify

```sh
source ../sandbox.env
npm --prefix game run typecheck
npm --prefix game test -- tests/unit/compile-model.test.ts tests/unit/visual-tiles.test.ts \
  tests/unit/shadow-batch.test.ts tests/unit/preparation-budget.test.ts
# Frozen service, dist/baseline setup and Air lifecycle: tools/perf/air-scenarios/tile-hitches.md
node tools/perf/air-scenarios/run.mjs http://127.0.0.1:5348 \
  'http://localhost:5340/?backend=webgpu&time=2026-10-15T11:00:00Z&mp=ws://localhost:5349/' \
  "$TURKU3D_WORK/reports/tile-hitches/repeat" drive 120
node tools/perf/air-scenarios/summarize.mjs "$TURKU3D_WORK/reports/tile-hitches/repeat" drive
```

## Gate output

Focused four unit files: 10 tests passed in 0.425 s; typecheck passed. Final rebase-selected checks and
Pages/smoke/async evidence are recorded by `codex-task finish` and the sandbox result report. The
selection covers river inclusion/opaque exclusion, yielded shadow preparation, ownership/disposal and
triangle/bounds preservation. No assertions were weakened. The asynchronous selection covers adjacent
reflection, shadow-material, vegetation, ground and coarse-model lifecycle consumers.

## Follow-ups

No further source slice is requested. The measured normal-driving target passes on this route. Broader
world assets or a fixed full 3200x1826 quality target would need a separate measurement.
