## Result: DONE

Reproduced Jonni's post-start loading on his M1 MacBook Air, then passed the same startup scenario with the fix
on sandbox Vite localhost and a nested static production build. The branch is committed for runner landing;
main was not merged manually.

## What changed

Startup resolves the multiplayer welcome before Ready, preferring the retained player pose over the assigned
spawn. New-game parking, every selectable save's player/owned vehicle, and multiplayer regions remain resident
as a union through placement. Collision, published visual and localhost draft selection share those candidates.
Ground snaps are prepared before GPU warmup; each selectable visual cell gets six real warmup views at the
resolved height. The first menu action reuses prepared placement rather than starting collision/ground queries.

Effects and the 27 opening/initial mission-hint clips decode before Ready. An AudioContext that requires a gesture
resumes inside input; decoding does not wait for that gesture. The loader hides synchronously on entry and cannot
return for streaming or later errors. Exceptional placement holds use a small in-game status. The CSS hidden rule
also takes precedence over the former compact menu dock. Build/draft statuses share a wrapping vertical stack.

The preconnected client is reused safely; solo play keeps shared pole reporting detached. The brief-budget parser
also honors inline assignments, matching the correction coordinated with the physics task (five tooling tests).
Capture-only Air tooling and the compile-checked recorder adapter are outside the production entry.

### Air reproduction and acceptance

Chrome 156, WebGPU, Jonni's 8 GB M1 Air, headed exclusive task profile, fresh incognito context and disabled HTTP
cache per case. Both source versions use the identical fixed real tile snapshot. A fresh sandbox multiplayer
room supplies the same first assigned spawn each run. Continue restores engine `(418.3, 9, 274.3)` from a save.
Timing starts at the actual menu activation (`pointerup`, capture phase) and ends at the first unpaused,
not-loading gameplay frame; the scenario then holds W for one second and observes five more seconds.

Baseline game source: `38e57affa949`, identical under `game/` to current main `4e9f3574` (intervening landings change
the roof producer and host scheduler). Measured canvas: **2541×1450**, the current game's pixel cap on the Air; these numbers
are not a reproduction at the earlier uncapped 3200×1826 canvas. The production run uses the ordinary full-menu
static bundle so the same Continue case remains selectable, served under `/game/` with sibling `/tiles` paths.

| Build | Case | Activation → movement ms | World requests in transition | All requests in transition | Visible loader frames |
| --- | --- | ---: | ---: | ---: | ---: |
| Main Vite | multiplayer | 140.5 | 3 | 4 | 29 |
| Main Vite | continue | 355.4 | 20 | 25 | 18 |
| Fixed Vite | multiplayer | 49.0 | 0 | 0 | 0 |
| Fixed Vite | continue | 51.2 | 0 | 0 | 0 |
| Fixed static /game/ | multiplayer | 67.1 | 0 | 0 | 0 |
| Fixed static /game/ | continue | 51.8 | 0 | 0 | 0 |

The baseline Multiplayer log reproduces the exact reported collision cells `c_4_7`, `c_6_4`, `c_6_3`, plus the first
voice phrase after activation. Baseline effects were still locked/unloaded (0/20) at Ready; input starts their
fetch/decode on pointer-down. Fixed runs have all 20 effects decoded at Ready, and no audio/voice requests start
in the measured post-activation interval. Fixed Multiplayer waits for 49/49 collision cells, 40/40 models and 6/6
warmup views; the remote save waits for 72/72 cells, 46/46 models and 12/12 views.

All four fixed transitions have **zero resource requests**, **zero loader frames**, no page/game errors, and no
new GPU pipeline calls. Subsequent commanded walking streams six look-ahead collision cells in the background,
without an overlay. Small pipeline variants also appear after movement (tree/edge/GTAO; individual API calls at
most 0.8 ms), outside the startup transition. Those are visible in the retained recorder logs, not hidden by a
claim of zero allocations throughout play.

A long-label badge stress capture using the actual badge constructors measured **925.9 px² overlap on main**,
then **0 px²** with the fixed vertical stack (4 px row gap). Both gameplay and badge screenshots were inspected.

### Short-window performance evidence

Frame columns are p50/p95/max (the max is the longest sampled frame); state age is p95/max. Heap is the signed
JavaScript heap change over the capture, including transient allocations and garbage collection. These individual
six-second walking windows are startup evidence, not a controlled throughput or retained-memory benchmark.

| Build | Case | Frame ms p50 / p95 / max | Physics state age ms p95 / max | JS heap change MiB |
| --- | --- | --- | --- | ---: |
| Main Vite | multiplayer | 23.4 / 24.6 / 60.5 | 6.8 / 42.6 | +75.6 |
| Main Vite | continue | 24.1 / 53.3 / 373.8 | 20.5 / 110.6 | +201.8 |
| Fixed Vite | multiplayer | 23.3 / 24.3 / 43.6 | 6.3 / 29.4 | +93.5 |
| Fixed Vite | continue | 24.3 / 27.1 / 80.1 | 6.2 / 30.8 | +90.1 |
| Fixed static /game/ | multiplayer | 23.3 / 24.2 / 59.6 | 5.8 / 36.2 | +94.6 |
| Fixed static /game/ | continue | 24.1 / 25.2 / 86.4 | 4.6 / 28.6 | +87.4 |

Raw requests, per-frame flags, physics round trips/step times, per-second light-recorder samples, pipeline calls
and screenshots are retained locally under `$TURKU3D_WORK/reports/start-loading/{air-main,air-fixed,air-pages}`.
Failed capture setup attempts are separately retained as `air-main-attempt-1`; they are not the numbers above.
The supplied recorder had malformed nested-template escapes and did not attach its current-mode RTT observer to
an existing worker. The capture adapter creates an output-only, compile-checked copy and rejects empty samples;
the shared profiling installation is untouched. The Air was explicitly stopped and handed back to physics/perf.

Fixed manifest SHA-256: `85fc30e9444f3a219cc9d6e4d21c7304e079438993e2f21d0968274d84b0d803`.
It contains 67 visual tiles and 268 collision cells; its embedded tile code is `d78eb3c7`.

## How to verify

Exact Air source/server/session commands are in `tools/perf/air-scenarios/README.md`. In the already-prepared
sandbox (start the matching exclusive Air session before each scenario command):

```sh
source ../sandbox.env
node tools/perf/air-scenarios/startup.mjs air-main http://127.0.0.1:5218 http://localhost:5213/ baseline
node tools/perf/air-scenarios/startup.mjs air-fixed http://127.0.0.1:5218 http://localhost:5216/ fixed
node tools/perf/air-scenarios/startup.mjs air-pages http://127.0.0.1:5218 http://localhost:5216/game/ fixed
# Always stop your own exclusive session after capture.
~/turku3d/profiling/air-session stop fix-start-loading
npm --prefix game test -- tests/unit/start-points.test.ts tests/unit/audio.test.ts tests/unit/voice.test.ts tests/unit/load-region.test.ts tests/unit/streaming.test.ts
python3 -m unittest scripts/dev/tests/test_browser_budget.py
node_modules/.bin/eslint tools/perf/air-scenarios/*.mjs game/scripts/start-loading-probe.mjs
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

The desktop startup probe and exact static build/server commands remain in `game/src/world/README.md`.
The original local M1 Max Chrome queues were terminated while awaiting host admission and supply no acceptance
claim; the required localhost/static verification above was completed on the actual Air instead. The final admitted
production bundle completed in 0.686 s; a lightweight low-priority rebuild also completed in 0.89 s including types.
No heavy build or local Chrome suite bypassed host admission. Remote Air capture was exclusive and booked as
7.6 browser-minutes including launch/cleanup allowance, within the updated 120-minute brief budget.

## Gate output

Fast gate: **GREEN in 2.922 s** after rebasing onto `4e9f3574`. Policy, Python/JS lint, game types and game unit
stages pass. Five browser-budget tests also pass. No tests or checks were weakened. The slow/deep
and integration Chrome stages remain advisory under project policy.

## Follow-ups

None for this brief. The shared source-recorder repair was reported to the orchestrator.
