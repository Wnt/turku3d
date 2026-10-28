## Breakable traffic-sign poles

The full props layer and the draft producer emit sign and bus-stop nodes with stable
`pole_id` (`<visual tile>:<source object id>`). The visual packager batches most poles
but retains per-object index ranges. `world/poleExtraction.ts` reads both forms; on a
break it moves only that range into a falling mesh and removes its triangles from the
standing batch. `world/tiles.ts` tracks full and draft model lifetimes. Only poles within
the collision stream radius plus 20 m have sleeping physics bodies.

`world.ts` creates a 2.7 m by 0.1 m solid cylinder for each resident pole, fixed at its
base until it receives a Rapier contact force from a vehicle moving at least 10 km/h.
The joint and body locks release on contact, and a torque starts the fall. The walking
character collides with the pole but never breaks it. Debris settles into a fixed body
after 20 simulated seconds. Break IDs survive tile eviction and multiplayer sync;
`PoleSession` applies one €50 fine, one officer notice and one voice line for each locally
broken pole.

From a sourced sandbox, verify the pure extraction and physics fixtures with
`npm --prefix game test -- --run tests/unit/poleExtraction.test.ts tests/unit/polePhysics.test.ts`.
For the real published office tile and a built draft `t0_2_0`, generate the source list with `bash pipeline/env/run.sh python game/scripts/pole-source.py`,
then run
`cd game && ../scripts/dev/codex-task wait-cmd -- ./node_modules/.bin/vitest run -c vitest.pole.config.ts`.
The headless run writes `pole-qa-full.json`, `pole-qa-draft.json` and pole placement JSON
in the sandbox reports. Render top-down and oblique local QA views for both tiles with
`bash pipeline/env/run.sh python game/scripts/pole-placement-qa.py`; PNGs are kept under
`$TURKU3D_WORK/qa/poles/`.

## Live clock and frame transport

Gameplay calls `PhysicsClient.updateInput(character, vehicle, running)` every frame,
including paused frames. The worker owns a 60 Hz wall clock and feeds the existing
fixed-step accumulator (at most five substeps). Delayed timers discard excess debt;
resuming after pause starts a fresh clock. Inputs expire after 500 ms without a
render-frame heartbeat, so suspended tabs cannot keep driving. Explicit `step(dt, …)`
remains an ordered command for initial query barriers, save/load placement and tools;
it never waits for a state reply. `PhysicsWorld` and its deterministic fixed steps
are unchanged. Timer ticks join the same promise chain as tile decode, actor commands,
remote proxies and pole updates, with at most one timer queued.

When `crossOriginIsolated`, two single-writer SharedArrayBuffer mailboxes carry input
and state. The frame loop writes input and reads `latest` directly; no main-thread
message task is involved in state delivery. A jump counter preserves pulses across
worker reads. Each mailbox has two Int32 header words (sequence, byte length), then
UTF-8 JSON bytes: odd sequence means writing, even means complete. Readers copy only
the published length and validate the sequence afterwards; on contention they retain
the previous snapshot until the next frame. Input capacity is 2 KiB and state capacity
4 MiB; overflow is an explicit physics error, never a truncated actor list. JSON keeps
the complete existing state schema, including all vehicles, string IDs and poles.
`publishedAt` uses `performance.timeOrigin + performance.now()` across contexts;
interpolation uses publication time rather than delayed message receipt time.
Locally caused pole breaks remain latched by stable ID in snapshots, preserving the
existing one-fine/one-sync dedup even when the renderer skips a snapshot.

Without isolation, input messages still feed the independent worker clock. At most
one state message awaits acknowledgement, preventing stale reply queues. Delivery
can still be delayed by browser task priorities on this fallback, but simulation
continues; neither stepping nor elapsed time depends on acknowledgement.

Local Vite and dist servers send COOP `same-origin` and COEP `require-corp` directly.
Static hosts (Pages) bootstrap through `core/isolation.ts` and `public/isolation.js`:
the worker adds these headers to same-origin responses, claims the game subpath and
reloads once. It stores no assets and preserves normal HTTP freshness. Registration
failure or a four-second control timeout starts the fallback. A controlled browser
that still cannot isolate also starts the fallback without a reload loop. Registration
uses document-relative URLs/scope, preserving `/turku-game/game/`, query parameters
and manifest `start_url: ./`, including standalone home-screen launches.

Runtime resource audit: the bundled modules/WASM, models, audio, Basis decoder and
UI images are same-origin; fonts are system fonts. Pages tiles are same-origin
siblings under `../tiles/` (outside the worker's scope but allowed by COEP). Optional
external tiles already use CORS fetches; their server must allow the game origin.
Multiplayer uses WebSocket (not an embedded resource); its HTTP score fetches use
CORS. No credentialless rewriting, cross-origin asset proxy or new endpoint is used.
COEP requirements: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cross-Origin-Embedder-Policy

Verify pure transport, clock, input and existing physics fixtures from a sourced sandbox:

```sh
npm --prefix game test -- --run tests/unit/physicsExchange.test.ts tests/unit/physicsClient.test.ts tests/unit/input.test.ts tests/unit/vehicle.test.ts tests/unit/polePhysics.test.ts
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
```

Manual held-key/load diagnostic (not a gate or browser spec): start sandbox Vite,
then `scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py browser -- node game/scripts/latency-probe.mjs after`.
It invokes `~/turku3d/profiling/record.mjs` against sandbox CDP port +8, dispatches
30 autorepeat keydowns/s and burns 22 ms per animation frame. Per-frame state age,
worker step duration and measured keyboard callback costs stay under
`$TURKU3D_WORK/reports/latency/`. The `before` argument temporarily restores the
reported baseline's transport/input/frame callers during measurement, then restores
the working files. Run it only while this sandbox has no concurrent edits/checks.
