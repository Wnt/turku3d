# Handover: taa (TRAA standing-still wander)

Jonni's report: when standing still, the landscape and trees wandered a few pixels under the default AA, while
`?aa=fxaa` stayed still. There were two causes. Both are fixed and merged into `local/loop-integration`.

## 1. TRAA velocity projection (main cause): integration 62296075

- **Cause:** three.js r186 `VelocityNode` picks the current-frame projection when a program is built.
  - It uses TRAA's unjittered matrix only inside `RenderPipeline.render`, and the live, jittered camera projection
    anywhere else.
  - Streaming preparation (`render/compileModel.ts`) builds between frames. Every prepared tile program (terrain,
    trees) therefore measured the sub-pixel jitter as motion against an unjittered previous frame: up to
    0.49 drawing-buffer px on static pixels, every frame.
  - TRAA reprojected its history by that jitter, so the image walked about 1 rendered px on the 32-frame Halton
    cycle. At pixel ratio 0.75 on a Retina display that is 2–3 screen px.
  - FXAA has no jitter, so it stood still.
- **Fix** (`game/src/render/rigidVelocity.ts`):
  - Both velocity paths (rigid and deformed) read one render-group uniform, `velocityProjection`.
  - It is filled once per camera and frame from the same matrix r186 keeps as the next frame's previous
    projection.
  - Static velocity is now exactly zero wherever the program was built, and camera motion still gives the true
    unjittered motion.
- **Test:** `game/tests/unit/traa-velocity.test.ts`.
  - Static frozen ground and instanced trees have zero velocity whether built outside or inside the pipeline.
    The old code gave 0.49 px for prepared programs.
  - A slowly turning camera gives the correct motion.
  - Jitter is sub-pixel and centred at pixel ratios 0.75, 1 and 1.5.
- **Ruled out:**
  - perf3's frozen transforms add no model motion.
  - Jitter scale is correct at pixel ratio ≠ 1.
  - The prepass and beauty pass share the same jittered camera.

## 2. Idle character creep on slopes: integration 21006efa

- **Cause:** a headless probe ran Rapier on the dev tiles and on 8–35° synthetic hillsides, with the play loop's
  `CharacterInterpolation` and `OrbitCamera`.
  - The grounded push (`groundedVy` −0.5 m/s) leaked down walkable slopes through Rapier's controller, even below
    the slide angle.
  - A standing player crept 8–40 mm per 10 s, with up to 5 mm single-step pops.
  - Ground 6 m ahead drifted 1–2.5 px and popped up to 0.4 px per frame. On flat road it did not move.
- **Fix** (`PhysicsWorld.stepCharacter` in `game/src/physics/world.ts`): a character holds its place when it
  - stays grounded with no input,
  - moves less than 1 cm in the step, and
  - stands on ground flatter than `minSlideDeg` (50°).
- **Unchanged:** steeper ground (55–70°) still slides at the old 0.33 m/s; walking and jumping work as before.
- **Test:** `game/tests/unit/character-idle-slope.test.ts`.
- **Interaction with physstall:** its render extrapolation (b1a78863) sees a still character, because the
  velocity is 0.
- **Probe scripts** (scratch, not in git): `~/turku3d/loops/taa/probes/`.

## 3. Shaking trees and their reflections: still-instance motion vectors (foliage-stable)

Jonni's report (2026-11-04, city.lab, M1 Max medium, Aurajoki by the main library, 14:34, camera and player still):
"The trees and some reflections shake around." Every crown and its river reflection flickered per pixel.

- **Cause:** c528e55a (2026-11-03) let still instances (`shareInstanceProgram(mesh, true)`: tree pools and tile draws,
  facade modules) share programs in TRAA's velocity passes, built without previous instance data
  (`needsPreviousData` returns false). r186's `positionPrevious` is then the bare geometry, not the instance-placed
  vertex, so every tree reported its whole placement as motion. TRAA's history UV left the screen or landed
  elsewhere, its weight went to 1, and the crowns showed the raw, jittered alpha-tested leaves each frame. The SSR
  river reflection reads TRAA's history, so the reflected trees shook too.
- **Not the cause:** the wind (the shake stayed with `motion=0`, wind frozen), the LOD dither, the SSR march itself.
- **Fix** (`render/rigidVelocity.ts`): still instances take the rigid path (camera motion only). Still instances
  with a vertex animation (vegetation wind, `world/nature/materials.ts`) keep the deformed path and their
  positionNode writes `positionPrevious`: the same placed vertex swayed at the previous frame's wind time
  (`windTimePrevious`), so wind sway gets its true frame-to-frame motion instead of its whole offset.
- **Test:** `tests/unit/traa-velocity.test.ts` "still shared instances never read the bare geometry…".
- **Measurement** (probe: `~/turku3d/loops/foliagestable/probe.mjs`, 90 consecutive frames read back in
  `afterFrame` at Jonni's pose, 1300x708, medium, wind on). Mean |luma change| between consecutive frames (0-255):

  | run | foliage pixels | other pixels | p99 |
  |---|---|---|---|
  | before, Mac M1 Max | 6.99 | 0.90 | 32.3 |
  | before, Windows (Radeon 560X, ~14 fps) | 6.91 | 0.90 | 31.3 |
  | after, Mac M1 Max | 0.45 | 0.10 | 2.3 |

  The temporal high-pass (|L_t - (L_t-1 + L_t+1)/2|) falls alike, 6.62 -> 0.37: the change was per-frame noise, not
  sway. GPU frame p50 15.2 -> 15.1 ms (no cost: the rigid path is cheaper than the deformed one). A blind Claude
  judge on 4-frame strips: after 7/10, before 4/10 ("shimmer over every crown" vs "calm, artifact-free"); its
  remaining note is a softer, slightly hazier crown than the aliased before, i.e. resolved alpha coverage.
- **Note for treelook:** the 2026-11-04 crown and leaf decisions (stretch crowns, alpha test 0.5, double-sided) were
  judged on unresolved, aliased crowns; resolved crowns read softer and denser.

## Open: the `?taafix=0` A/B

- `?taafix=0` restores r186's build-time projection choice, which is the old wandering behaviour.
- The idle-slope hold has no switch.
- **Remove** the switch and the `buildTimeProjection` branch in `rigidVelocity.ts` after Jonni's retest: stand
  still on a hillside with trees for 20 s, with and without `?taafix=0`. Limit: at most ten merges.
- airperf saw no wander on the M1 Air.

## Why TRAA stays the default

- The wander was a bug, not how TRAA behaves; with correct motion vectors the jittered image converges and stays
  still.
- TRAA is better than FXAA on foliage alpha, thin geometry and specular, and it recovers detail at pixel ratio 0.75.
- The dithered LOD cross-fades depend on it: lodblend's tile dissolves and per-tree fades use per-frame
  screen-door dither that only temporal AA resolves.
- `?aa=fxaa` (and `?aa=off`) stay available as diagnostic selectors; mobile quality defaults to FXAA.

## Not done

- History clamping on dithered pixels (suspect 4) was not investigated; lodblend's 59105dd1 ("No dither outlives
  a fade") was merged meanwhile.
- `tests/unit/night-pipelines.test.ts` "idle light slots…" already failed on integration before these changes;
  it is unrelated.
