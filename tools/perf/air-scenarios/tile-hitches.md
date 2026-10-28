# Draft tile loading on the Air

These capture-only tools never enter the production bundle. Use only your owned `air-session`.
The 120-second drive controls the real physics car from the office toward the west bank, extending
its road route as new draft graphs arrive. It records every RAF, actual physics publication age,
heap, synchronous shader builds, texture uploads, shadow preparation and publication. Runtime route
extensions can differ with arrival timing; report actual distance and endpoints as well as duration.

Freeze inputs once with `snapshot.mjs`; keep all data, traces and screenshots outside git:

```sh
source ../sandbox.env
node tools/perf/air-scenarios/snapshot.mjs https://turku.lab.madekivi.fi/coarse/ \
  "$TURKU3D_WORK/air-current-inputs/coarse" \
  t0_0_0 t0_0_1 t0_0_2 t0_0_3 t0_1_0 t0_1_1 t0_1_2 t0_1_3 \
  t0_-1_0 t0_-1_1 t0_-1_2 t0_-1_3 t0_-2_0 t0_-2_1 t0_-2_2 t0_-2_3
node tools/perf/air-scenarios/drafts.mjs "$TURKU3D_WORK/air-current-inputs/coarse" "$((TURKU3D_PORT_BASE+5))"
```

Use production dist builds with `VITE_PUBLIC_RELEASE=1`, including for the baseline. The historical
renderer predates current facade schemas. `baseline.mjs` archives the current release and restores
only its pre-fix renderer/ownership code and decoder behavior; it retains current asset compatibility
and request ordering. This is a counterfactual baseline, not a historical release measurement.

```sh
node tools/perf/air-scenarios/baseline.mjs 7847e0e9 148a553a "$TURKU3D_WORK/air-matched-baseline"
python3 scripts/dev/host_slots.py build -- env VITE_PUBLIC_RELEASE=1 \
  TURKU3D_WORK="$TURKU3D_WORK/air-baseline-work" node game/node_modules/vite/bin/vite.js \
  build "$TURKU3D_WORK/air-matched-baseline/game" --config "$TURKU3D_WORK/air-matched-baseline/game/vite.config.ts"
# Serve exact published game bytes as the primary root, and the baseline as a mount.
node tools/dev/serve-dist.mjs --root "$TURKU3D_WORK/air-pages/game" --port "$TURKU3D_PORT_BASE" \
  --mount "/baseline=$TURKU3D_WORK/air-baseline-work/build/game"
```

Start your own multiplayer server on port base+9 and reverse-tunnel only that port to the Air;
use its isolated room for both builds. `air-session` forwards the game port. Never join another
measurement's room or stop another session. The runner unloads before clearing storage (pagehide
autosave otherwise restores the previous position), handles the public Multiplayer menu, resets the
car once before measurement, then drives without teleporting.

```sh
~/turku3d/profiling/air-session start fix-tile-load-hitches \
  --game-port "$TURKU3D_PORT_BASE" --cdp-port "$((TURKU3D_PORT_BASE+8))"
# Use /baseline/ for before, / for published/current; same query and 120 seconds.
node tools/perf/air-scenarios/run.mjs "http://127.0.0.1:$((TURKU3D_PORT_BASE+8))" \
  "http://localhost:$TURKU3D_PORT_BASE/?backend=webgpu&time=2026-10-15T11:00:00Z&mp=ws://localhost:$((TURKU3D_PORT_BASE+9))/" \
  "$TURKU3D_WORK/reports/tile-hitches/after" drive 120
node tools/perf/air-scenarios/summarize.mjs "$TURKU3D_WORK/reports/tile-hitches/after" drive
~/turku3d/profiling/air-session stop fix-tile-load-hitches
```

Always stop the owned session after failures too. Default drive recording is light. Set
`TURKU3D_DRIVE_TRACE=1` for Chrome tracing/allocation sampling, then run
`python3 tools/perf/air-scenarios/attribute.py OUTPUT drive 100` to attribute each interval above
100 ms (optional threshold 50). The marker is emitted after tracing starts. Traced runs have
additional overhead; do not combine their percentiles with the primary light comparison.
See `renderer.md` for the external recorder's String.raw correction and
`TURKU3D_RENDER_RECORDER` sandbox-copy override. No shared recorder changes are required.

The focused local WebGPU reproduction needs Vite but no city data:

```sh
python3 scripts/dev/host_slots.py browser -- node tools/perf/air-scenarios/local-warmup.mjs \
  "http://localhost:$TURKU3D_PORT_BASE/" "$TURKU3D_WORK/reports/tile-hitches/local-warmup.json"
```

It asserts surface/AO shader reuse at publication. Its 640x480 synthetic timing is not an Air
benchmark. `local-flight.mjs GAME_URL OWN_CDP_PORT OUTPUT` runs the 75-second low Aura flight;
use frozen source and the browser slot wrapper. `TURKU3D_RENDER_TRACE=0` selects light flight capture.
