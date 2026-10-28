# Draft tile loading on the Air

Use `air-session` exclusively; never attach to another task's Chrome. The existing 75-second Aura
flight moves the streaming focus across draft cells at low altitude. The optional drive uses real
physics. The capture-only runtime supports the draft-only world and records synchronous shader
builds, texture uploads and shadow batching/publication, alongside every RAF and physics publication age.
The recorder's Chrome trace supplies parse, worker, GC and pipeline attribution. No game entry imports
these tools.

Freeze the served inputs once, then use that exact snapshot for both versions. `snapshot.mjs` retains
hashed model/collision URLs and external glTF images. Keep the files and screenshots outside git.
Missing served tiles fail explicitly; do not compare changing live inputs.

```sh
source ../sandbox.env
node tools/perf/air-scenarios/snapshot.mjs https://turku.lab.madekivi.fi/coarse/ \
  "$TURKU3D_WORK/air-inputs/coarse" \
  t0_0_0 t0_0_1 t0_0_2 t0_0_3 t0_1_0 t0_1_1 t0_1_2 t0_1_3 \
  t0_-1_0 t0_-1_1 t0_-1_2 t0_-1_3 t0_-2_0 t0_-2_1 t0_-2_2 t0_-2_3
node tools/perf/air-scenarios/drafts.mjs "$TURKU3D_WORK/air-inputs/coarse" "$((TURKU3D_PORT_BASE+5))"
# In another sourced terminal (Vite dev build for both versions):
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE"
~/turku3d/profiling/air-session start fix-tile-load-hitches \
  --game-port "$TURKU3D_PORT_BASE" --cdp-port "$((TURKU3D_PORT_BASE+8))" \
  --url '/?backend=webgpu&time=2026-10-15T11:00:00Z'
node tools/perf/air-scenarios/run.mjs "http://127.0.0.1:$((TURKU3D_PORT_BASE+8))" \
  "http://localhost:$TURKU3D_PORT_BASE/?backend=webgpu&time=2026-10-15T11:00:00Z" \
  "$TURKU3D_WORK/reports/tile-hitches/before" flight
node tools/perf/air-scenarios/summarize.mjs "$TURKU3D_WORK/reports/tile-hitches/before" flight
~/turku3d/profiling/air-session stop fix-tile-load-hitches
```

Stop the owned Air session even after a failure. See `renderer.md` for the external recorder's
`String.raw` probe correction and the `TURKU3D_RENDER_RECORDER` override. Preserve that same recorder
and scenario instrumentation for the matched pair. Build/serve mode, input identity, frame percentiles,
physics age and heap growth belong in the result; a flight is not evidence of successful normal driving.

The focused local regression needs only Vite, with no draft service or city data:

```sh
python3 scripts/dev/host_slots.py browser -- node tools/perf/air-scenarios/local-warmup.mjs \
  "http://localhost:$TURKU3D_PORT_BASE/" "$TURKU3D_WORK/reports/tile-hitches/local-warmup.json"
```

It exercises the real WebGPU backend, CSM, post passes, material styling and tile ownership. It fails
if publication rebuilds the warmed surface or shared GTAO shader. Its 640x480 synthetic timing is
a diagnostic for shader cache reuse, not an Air performance result. An optional final integer sets
the expected publication surface-builder count when demonstrating an archived baseline (2 on the
original path). The capture-only entry is `game/scripts/tile-warmup-probe.ts`.

`local-flight.mjs GAME_URL OWN_CDP_PORT OUTPUT` launches the same 75-second scenario on the Mac's
sandbox automation Chrome. Run it through the browser slot wrapper. Use frozen source when capturing:
Vite hot reload interrupts the scenario. `TURKU3D_RENDER_TRACE=0` keeps light recording for exploratory
local repeats; omit it for Air captures requiring trace/allocation attribution. Light and traced runs
have different overhead and are not an allocation comparison.
