## Result: DONE

Committed on the sandbox branch for runner landing; main was not merged manually.

## What changed

The physics worker now requires actual resident collision for every cell touched by an actor's swept ground
footprint before each runtime substep. Missing cells hold walking characters with zero vertical velocity,
including immediately after flight is toggled off. Flight can move horizontally/upward across unbuilt regions,
but cannot descend without collision. Every vehicle, including the parked car left behind during flight, becomes
fixed while coverage is missing and resumes dynamically from rest when collision returns. Custom chassis sizes
contribute to the coverage radius. Waking a previously sleeping car and invalidating its cached pose are explicit.
The existing gentle braking remains; the worker check is the final guard against gravity, eviction and input latency.

The streamer already follows the actual character, rather than an independent fly camera. The failures were
insufficient enforcement: `speedLimit` only considered horizontal movement into manifest cells, ignored stationary
actors and unknown cells, and did not hold gravity. Edge walls have a finite height and do not close boundaries
towards known-but-unloaded cells. Draft decode checks only prove the files can be read, not that their colliders
are resident. Parked vehicles continued stepping after collision eviction. No shared-transport state loss was
needed to reproduce either failure.

Startup retains collision and published visual regions for every selectable start. Draft requirements now cover
those starting collision radii instead of the expanded 3×3 window's distant REPLACE siblings. Further draft
expansion uses ordinary streaming after placement. Startup uploads all maps and warms real GPU passes, initially
with frustum culling disabled, followed by six directions per selectable cell and the restored initial view.
Per-model paced async shader compilation starts only after Ready. This removes repeated startup preparation
and r186's per-draw async yields without reducing the preloaded published ring or moving preparation after entry.
The small position hint also covers missing-collision holds.

### Real-data reproduction and verification

Chrome for Testing 156.0.8078.73, WebGPU/Metal, M1 Max, 1280×720, fresh browser/context and disabled HTTP cache.
The manual probe uses sandbox ports/profiles and host browser admission. Its screenshots were inspected.

- Before: a 1.88 km fast flight took the character above unloaded ground. The parked car began falling after
  about 300 m. Thirty seconds after leaving flight, the character was at H −1818.47 m and the car at H −51854.09 m.
- Fixed: a 2.019 km flight crossed published and draft regions. With flight off, the character stayed at
  H 128.922 m while the cell was absent. Once collision arrived, it descended and landed at H 10.44355 m on
  draft `t0_-4_-7`, cell `c_-13_-25`, remaining grounded. The parked car stayed at H 4.43875 m throughout.
- A separate normal menu respawn/walk/entry/drive check walked about 1.19 m and drove about 39.23 m, with
  stable ground contact. Entry used F; the probe placed the capsule beside the car through the normal worker
  placement API. Runtime unit fixtures verify hold, flight descent, collision eviction and resumption directly.

Evidence is retained under `$TURKU3D_WORK/reports/fly-fallthrough/`:
`before-local`, `before-pages`, `draws-local`, `fixed-drive`, `fixed-entry`, `fixed-pages`, `before-pre-start-pages`.
`flight.json` includes sampled actor poses and actual loaded cell IDs; `startup.json` includes loader logs,
resource timings and counters. No imagery or raw city data was committed.

### Startup measurements

The actual pre-change published bundle is Pages commit `d9d9e936` (source `4e9f35749727`), fetched from public
`gh-pages` history and served through the probe's game-only route on the actual Pages origin. It uses the same
current hosted 43-model region as the fixed comparison. Ready improved **62.3 → 17.2 s** while required
collision grew from 46 to 49 cells. The old click took 201.6 ms, requested three collision cells plus a voice clip,
and showed eight loader frames; the fixed click took 103.1 ms with zero such requests and zero loader frames.
The initial historical source rebuild stayed queued, so the published artifact supplied the stronger comparison;
no build admission was bypassed. The public history is retained in `pages-history` and its extracted game in
`pre-start-release`. Reproduce extraction with:

```sh
git clone --filter=blob:none --no-checkout --depth=50 --branch gh-pages https://github.com/Wnt/turku-game.git "$TURKU3D_WORK/reports/fly-fallthrough/pages-history"
mkdir -p "$TURKU3D_WORK/reports/fly-fallthrough/pre-start-release"
git -C "$TURKU3D_WORK/reports/fly-fallthrough/pages-history" archive d9d9e93 game | tar -x -C "$TURKU3D_WORK/reports/fly-fallthrough/pre-start-release"
python3 scripts/dev/host_slots.py browser -- node game/scripts/fly-loading-probe.mjs before-pre-start-pages https://wnt.github.io/turku-game/game/ transition "$TURKU3D_WORK/reports/fly-fallthrough/pre-start-release/game"
```

Observed page navigation → Ready, not just click latency. Stage completion times overlap and must not be added.
HTTP cache is disabled; driver/compiler cache and concurrent host load were not controlled. These are individual
observations, not a throughput benchmark. The live Pages publication advanced during the task (40 → 43 required
models); the fixed Pages run still completed 43/43 models and 49/49 collision cells before enabling the menu.
The Pages fixed case routes only the locally built game files into the actual Pages origin; its hosted tiles and
multiplayer endpoint remain real. Nothing was published by this task.

| Case | Ready | Last published GLB download | Last observed KTX2 completion | Required collision decoded | GPU warmup |
| --- | ---: | ---: | ---: | ---: | ---: |
| Current source, localhost before | 136.5 s | 18.2 s | 89.7 s | 20.5 s | 46.0 s |
| Published Pages before startup-loading change | 62.3 s | 3.8 s | 5.1 s | 5.5 s (46 cells) | 56.1 s |
| Current deployed Pages before | 68.0 s | 5.8 s | 8.2 s | 8.9 s | 58.6 s |
| Fixed localhost, flight proof | 29.2 s | 14.1 s | 20.7 s | 16.4 s | 7.5 s |
| Fixed localhost, menu proof | 13.1 s | 1.7 s | 7.4 s | 4.2 s | about 5.3 s |
| Fixed bundle on Pages origin | 17.2 s | 3.9 s | 10.0 s | 5.9 s | 6.4 s |

Voice clips finished decoding at 3.55/3.45 s before and 1.08/1.60 s in the final localhost/Pages cases;
audio was not the long critical path. KTX2 completion timestamps include when the loader admitted the work,
not continuous transcoder CPU time. In particular, the baseline draft completions were delayed behind repeated
model preparation. Tile generation can additionally hold localhost startup when the expanded draft window is
not built. The measured dominant costs were repeated shader preparation and GPU warmup, not a missing
published download.

| Fixed case | Menu activation → first unpaused frame | New world/audio requests in interval | Loader frames |
| --- | ---: | ---: | ---: |
| Localhost multiplayer | 92.2 ms | 0 | 0 |
| Pages-origin multiplayer | 103.1 ms | 0 | 0 |

## How to verify

```sh
source ../sandbox.env
npm --prefix game test -- tests/unit/physics.test.ts tests/unit/collision-coverage.test.ts tests/unit/coarse-region.test.ts tests/unit/streaming.test.ts tests/unit/coarse-tiles.test.ts
scripts/dev/codex-task wait-cmd -- scripts/dev/gate --fast --base main
# Separate terminals, using a fixed real tile copy in $TURKU3D_WORK/dist/tiles:
npm --prefix game run dev -- --host 127.0.0.1
tools/dev/coarse-tiles --port "$((TURKU3D_PORT_BASE + 5))"
TURKU_MP_PORT="$((TURKU3D_PORT_BASE + 4))" TURKU_MP_SCORES="$TURKU3D_WORK/reports/fly-fallthrough/scores" node server/index.js
# Run each browser command with ordinary 30-second polling if it remains active:
python3 scripts/dev/host_slots.py browser -- node game/scripts/fly-loading-probe.mjs flight "http://localhost:$TURKU3D_PORT_BASE/?mp=ws://127.0.0.1:$((TURKU3D_PORT_BASE + 4))/" fly
python3 scripts/dev/host_slots.py browser -- node game/scripts/fly-loading-probe.mjs entry "http://localhost:$TURKU3D_PORT_BASE/?mp=ws://127.0.0.1:$((TURKU3D_PORT_BASE + 4))/" transition
scripts/dev/codex-task wait-cmd -- python3 scripts/dev/host_slots.py build -- npm --prefix game run build
python3 scripts/dev/host_slots.py browser -- node game/scripts/fly-loading-probe.mjs pages https://wnt.github.io/turku-game/game/ transition "$TURKU3D_WORK/build/game"
```

`startup` only measures Ready; `transition` checks entry; `drive` skips the long flight and checks respawn/walking/
car entry/driving. `fly` additionally runs the two-minute fast flight, then leaves flight for 30 seconds.
The optional final directory replaces only game files on the Pages origin for a production-bundle comparison.

## Gate output

Selected unit checks: 52 passed. First fast gate GREEN in 54.340 s, including 45.262 s first-use environment sync;
policy, Chrome-launch policy, lint, game types and game units all passed. Final fast gate GREEN in 3.920 s after rebasing onto `624a4811bfad`.
No checks were weakened and no browser spec or slow gate test was added.

## Follow-ups

None.
