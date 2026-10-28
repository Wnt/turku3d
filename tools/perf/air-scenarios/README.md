# Air startup scenario

Use only the task's exclusive `air-session` Chrome. Screenshots and measurements stay in
`$TURKU3D_WORK/reports/start-loading`; none are committed. The capture server and CDP instrumentation are separate
from the production entry, with no gameplay flag. `startup.mjs` creates a fresh incognito context per scenario,
disables HTTP caching, and runs fresh Multiplayer and Continue at the road-graph point engine `(418.3, 9, 274.3)`.
It clicks the actual menu through CDP, records every request after that click, samples movement readiness and
computed loader visibility each frame, holds W for one second, then observes five more seconds. The existing Air
recorder runs alongside it in light mode. JSON reports contain transition timing, request lists, frame p50/p95/max,
physics state age/round trips/step time, heap growth, and actual canvas dimensions.

From the repo root, with sandbox environment sourced and `npm --prefix game ci` / `npm --prefix server ci` complete:

```sh
source ../sandbox.env
# Freeze current main as a source fixture, inside the checkout's ignored cache.
mkdir -p .cache/startup-main
git archive main | tar -x -C .cache/startup-main
ln -s "$PWD/game/node_modules" .cache/startup-main/game/node_modules
# Keep the same frozen real tile release in $TURKU3D_WORK/dist/tiles for both runs.
TURKU_MP_PORT="$((TURKU3D_PORT_BASE + 4))" TURKU_MP_SCORES="$TURKU3D_WORK/reports/start-loading/scores" node server/index.js
# Separate shells; the capture-only proxy keeps multiplayer inside the Air's single game-port tunnel.
TILE_DIST="$TURKU3D_WORK/dist/tiles" TURKU3D_WORK="$TURKU3D_WORK/reports/start-loading/air-main-work" \
  node tools/perf/air-scenarios/startup-server.mjs .cache/startup-main/game "$((TURKU3D_PORT_BASE + 3))" "$((TURKU3D_PORT_BASE + 4))"
TILE_DIST="$TURKU3D_WORK/dist/tiles" TURKU3D_WORK="$TURKU3D_WORK/reports/start-loading/air-fixed-work" \
  node tools/perf/air-scenarios/startup-server.mjs game "$((TURKU3D_PORT_BASE + 6))" "$((TURKU3D_PORT_BASE + 4))"
# Take the Air only when free. Always stop your session, including after a failing capture.
~/turku3d/profiling/air-session start fix-start-loading --game-port "$((TURKU3D_PORT_BASE + 3))" \
  --cdp-port "$((TURKU3D_PORT_BASE + 8))" --url /__startup-blank
node tools/perf/air-scenarios/startup.mjs air-main "http://127.0.0.1:$((TURKU3D_PORT_BASE + 8))" \
  "http://localhost:$((TURKU3D_PORT_BASE + 3))/" baseline
~/turku3d/profiling/air-session stop fix-start-loading
~/turku3d/profiling/air-session start fix-start-loading --game-port "$((TURKU3D_PORT_BASE + 6))" \
  --cdp-port "$((TURKU3D_PORT_BASE + 8))" --url /__startup-blank
node tools/perf/air-scenarios/startup.mjs air-fixed "http://127.0.0.1:$((TURKU3D_PORT_BASE + 8))" \
  "http://localhost:$((TURKU3D_PORT_BASE + 6))/" fixed
~/turku3d/profiling/air-session stop fix-start-loading
```

The fixed run fails if click-to-movement reaches 200 ms, any visual/collision request starts before movement is
enabled, any measured frame shows the startup loader, or a page error occurs. Baseline reports retain those
failures as evidence without failing the capture command. The measurement window and recorder are identical.
