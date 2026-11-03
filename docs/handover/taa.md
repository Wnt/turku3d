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
