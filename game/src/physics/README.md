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
