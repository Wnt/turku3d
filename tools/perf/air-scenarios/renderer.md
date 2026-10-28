# Air renderer scenario

Run from the task checkout after sourcing its sandbox environment. This capture-only tool connects to the
exclusive Air Chrome over CDP; no game entry imports its runtime and no gameplay query flag enables it.

The flight follows both Aura banks between Teatterisilta (engine x≈4,z≈394) and Myllysilta (x≈−354,z≈592),
at H=8 m, returning to the office over 75 seconds. It moves the player streaming focus with the camera.
A 120-second physics-driven car route then follows the loaded street graph through the same corridor.
The input wrapper supplies steering/throttle only during the measured drive. Screenshots stay local.
Canvas conditions are 1600×913 CSS, DPR 2, medium quality, WebGPU and a fixed autumn afternoon.

Freeze identical published inputs and existing drafts before comparing builds. Resolve the published release's
symlink once; `drafts.mjs` reads the frozen products and never generates new city data. Missing draft manifests
return 202, so use a snapshot covering the requested ring and record any missing coverage.

```sh
source ../sandbox.env
mkdir -p "$TURKU3D_WORK/air-inputs"
cp -cR "$TURKU3D_DATA/coarse" "$TURKU3D_WORK/air-inputs/coarse"
node tools/perf/air-scenarios/drafts.mjs "$TURKU3D_WORK/air-inputs/coarse" "$((TURKU3D_PORT_BASE+5))"
```

Serve the baseline and final game, one at a time, on the same owned port. For a dev comparison use Vite with
`TILE_DIST` pointing at the same frozen published release, its normal `/coarse` proxy and separate baseline cache.
The baseline source archive is retained under `.cache/perf-air-render/baseline`; its game source is identical to
main `38e57aff` (the only game-tree difference is the Playwright config). Build/serve mode belongs in the result.

```sh
source ../sandbox.env
TILE_DIST="$TURKU3D_WORK/dist/tiles" npm --prefix game run dev -- --host 127.0.0.1 --port 5244
~/turku3d/profiling/air-session start perf-air-render --game-port 5244 --cdp-port 5248 \
  --url '/?backend=webgpu&tiles=/tiles/tileset.json&time=2026-10-15T11:00:00Z'
node tools/perf/air-scenarios/run.mjs http://127.0.0.1:5248 \
  'http://localhost:5244/?backend=webgpu&tiles=/tiles/tileset.json&time=2026-10-15T11:00:00Z' \
  "$TURKU3D_WORK/reports/air-render/air-after"
~/turku3d/profiling/air-session stop perf-air-render
```

Never stop another task's session. Always stop this session after capture, including a failed scenario.
`run.mjs` runs the external recorder with a trace/allocation profile for the flight and light logging for the drive.
It also retains every RAF interval, phase, per-second pose/heap data and node-builder creation events, including
whether a builder was created inside the animation callback. `flight-state.json` survives a later drive failure;
`last-state.json` retains diagnostic state on failure. `scenario.json` is the complete successful run.
Report nearest-rank p50/p95/max, >100 ms frames per measured minute, allocation/heap growth and actual drive progress.
