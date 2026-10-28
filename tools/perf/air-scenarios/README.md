# Air startup scenario

Use only the task's exclusive `air-session` Chrome. Screenshots and measurements stay in
`$TURKU3D_WORK/reports/start-loading`; none are committed. The capture server and CDP instrumentation are separate
from the production entry, with no gameplay flag. `startup.mjs` creates a fresh incognito context per scenario,
disables HTTP caching, and runs fresh Multiplayer and Continue at the road-graph point engine `(418.3, 9, 274.3)`.
It starts a fresh sandbox multiplayer room on port base +4 for each paired scenario command, so baseline and fixed
receive the same first assigned spawn. Keep that port free. It clicks the actual menu through CDP, records every request after that click, samples movement readiness and
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
# Separate shells; the capture-only proxy keeps multiplayer inside the Air's single game-port tunnel.
TILE_DIST="$TURKU3D_WORK/dist/tiles" TURKU3D_WORK="$TURKU3D_WORK/reports/start-loading/air-main-work" \
  node tools/perf/air-scenarios/startup-server.mjs .cache/startup-main/game "$((TURKU3D_PORT_BASE + 3))" "$((TURKU3D_PORT_BASE + 4))"
TILE_DIST="$TURKU3D_WORK/dist/tiles" TURKU3D_WORK="$TURKU3D_WORK/reports/start-loading/air-fixed-work" \
  node tools/perf/air-scenarios/startup-server.mjs game "$((TURKU3D_PORT_BASE + 6))" "$((TURKU3D_PORT_BASE + 4))" "$TURKU3D_WORK/build/game"
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

For Pages-style production verification, repeat the fixed session command with the same server and use
`http://localhost:$((TURKU3D_PORT_BASE + 6))/game/` as the scenario URL, with a separate report label such as
`air-pages`. The optional final server argument mounts the frozen production bundle there; sibling `/tiles`
continues to use the fixed sandbox snapshot.

The supplied external recorder had invalid escapes inside its injected JavaScript template. `recorder.mjs` creates
an output-only, compile-checked copy, preserving a future `String.raw` correction. It also attaches RTT observation
to the already-running physics worker in `current` mode. Shared profiling files are never modified, and empty
recorder samples fail the capture. The actual menu action fires on `pointerup`, so activation timing starts in its
capture phase before the menu can remove the selected row.

# Air physics latency scenario

Manual performance diagnostic, never bundled into production or run in a gate.
It uses the exclusive `~/turku3d/profiling/air-session` session and only that task's
remote Chrome profile. It stops the session in `finally` and debits its actual Air
reservation against the task's browser budget. Never stop another task's session.

From a sourced sandbox, serve the real published tile release read-only at the
sandbox's `dist/tiles` path and start Vite on port base +0:

```sh
source ../sandbox.env
npm --prefix game run dev -- --host 127.0.0.1
```

Run each command separately; the `before` run temporarily serves current main's
existing game files, adding only a publication timestamp to its state packet, and
restores the branch files in `finally`. Do not edit or build this sandbox during it.
New branch-only modules remain unused by main's entry. Vite dev serving is used for
both sides, with the same real tile release, native headed Air viewport and default
quality; this is not the synthetic fixture or a headless M1 Max measurement.

```sh
scripts/dev/codex-task wait-cmd -- node tools/perf/air-scenarios/physics-latency.mjs before
scripts/dev/codex-task wait-cmd -- node tools/perf/air-scenarios/physics-latency.mjs after
scripts/dev/codex-task wait-cmd -- node tools/perf/air-scenarios/physics-latency.mjs controller
```

The script opens New game, skips the introduction, places the character beside the
player car and enters with F. It records 10 s idle, W for 20 s, resets to the starting
pose, settles for 2 s, W+A from rest for 20 s, W+D while driving for 20 s, then releases
all keys for 10 s. The last held key repeats at 30 Hz through CDP, matching keyboard
autorepeat when multiple keys are held. The controller run uses the same route and
pedal/steering phases through a virtual standard Gamepad with no keyboard repeat.

`~/turku3d/profiling/record.mjs` runs in light mode, installing its normal probes
before workers are constructed. Output remains local under
`$TURKU3D_WORK/reports/latency/air/{before,after,controller}/`: its per-second
`frames.json` and `recorder.log`, per-render-frame publication age/physics ticks,
worker step duration, shared packet byte count and heap in `state-age.json`, phase
percentiles in `summary.json`, and an Air screenshot. Publication age uses absolute
performance time across worker and page. Shared transport has no state message
round trip; use publication age and tick progression to compare it with baseline.
The scenario measures real render load rather than adding a synthetic busy loop.
