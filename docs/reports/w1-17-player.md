# W1-17: Autumn player

The shipped mannequin is replaced with a CC0 MakeHuman human in a forest-green hoodie,
blue jeans and brown shoes. Short hair, face, eyes and clothing retain their textures.
The 1.79 m asset has 37,225 triangles and is 3,527,780 bytes (3.36 MiB), with embedded
KTX2 textures and meshopt-compressed geometry, skinning and animation.

The offline build retargets the source mesh and four-weight skin into the existing UAL
bind pose. UAL's animation tracks, rig hierarchy and inverse bind matrices are retained,
so runtime input, locomotion phase blending, stair gait and analytic foot IK use the same
contract as before. All nine original clips remain. There is no runtime rig conversion.
The player loader now configures KTX2 and meshopt, and releases its texture workers after
loading. The previous mannequin GLB is deleted from the shipped assets.

Source licence notes, individual input hashes, pinned revisions and exact rebuild commands
are in `game/public/assets/character/`. Only CC0 exported data is used from CharacterCreator;
its GPL generator/runtime code is not included.

## Verification

Run from this worktree:

```bash
source ../sandbox.env
scripts/dev/gate --base main
# Focused character acceptance (after the gate has built the game):
cd game
npx playwright test tests/e2e/character.spec.ts tests/e2e/gamepad.spec.ts
```

All routes use the small offline synthetic fixture; no large AOI or raw-data build is needed.
The camera regression checks actual asset height and top/bottom viewport margins, required
clips, KTX2/meshopt extension presence and browser errors on WebGPU and WebGL2. Existing
virtual Xbox tests cover analog walk, sprint, jumping, camera controls, pause/hot-plug,
stair ascent/descent with ankle-error limits, and enter/drive/reverse/exit.

The first concurrent browser run had focus/startup and stair timing failures during host
contention. The isolated WebGPU controller rerun passed in 6.4 s (22.8 s including startup).
No thresholds, timeouts or input focus guards were changed. The final gate is recorded in
`$TURKU3D_WORK/reports/player-gate-rebased.json`. All seven selected stages passed in
510.452 s after rebase onto `aaf46d1`: policy, generated files, ESLint, TypeScript, knip,
unit selection and browser acceptance. The explicit four-test character/IK unit run also
passed (0.873 s). The selective unit stage found no additional affected tests. Browser
acceptance passed on both backends, including the unchanged controller/stair assertions.
The run substantially exceeds the 5–20 s target; shared-host swapping and broad inherited
browser routing remain tracked with the gate-speed owner, rather than relaxing checks.

## Screenshots

Before/after use the same office spawn and default third-person camera on the synthetic
fixture. These are actual Chrome captures, not asset previews. The body stays inside the
viewport with useful ground visibility; no camera tuning was needed.

| View | Before, relative to `$TURKU3D_WORK/reports/` | After |
|---|---|---|
| WebGPU default framing | `player-before/webgpu-start.png` | `screenshots/webgpu-player-framing.png` |
| WebGL2 default framing | `player-before/webgl-start.png` | `screenshots/webgl-player-framing.png` |
| WebGPU stair tread placement | — | `screenshots/webgpu-stair-ik.png` |
| WebGL2 stair tread placement | — | `screenshots/webgl-stair-ik.png` |

The before captures are from baseline `2d75e2c`; final after captures are from the rebased
player version. The artist-facing result is a first version; physical controller feel remains
the existing manual hardware check, while injected Xbox controls are automated.

## Landing follow-up: asynchronous movement acknowledgement

The zero-speed failure was in the test sampler, not the retarget or locomotion. The
sampler had an independent five-second wall-clock deadline and returned the current
state when it expired. `ready && grounded` only establishes spawn readiness; it does
not acknowledge a newly submitted movement command. Sampling on animation frames does
not help if the physics response itself is delayed.

Repeated normal routes measured 0.64–0.80 m/s with the textured model and advancing
physics ticks. A controlled diagnostic delayed the first movement response by 6.5 s
without blocking Chrome. The old sampler failed after 5.010 s with speed 0.000003 m/s,
while input was active, focus was true, tiles were settled and 287 frames had been
sampled. This isolates the premature snapshot from loading, focus and skin deformation.
The state-driven wait passes that delayed response.

The fix keeps input injection and first matching state capture in one browser task,
removes the independent five-second deadline, and uses the existing 120 s test timeout.
Both speed assertions (>0.6 and <1.6 m/s) and all movement/IK assertions remain intact.
The browser worker cap is three. No asset, runtime input or physics changes are needed.
Temporary diagnostic response interception is removed after verification; its before/after
sources and logs remain under `$TURKU3D_WORK/reports/player-delayed-worker*`.

The sandbox now has its own ignored Python environment, created with:

```bash
source ../sandbox.env
uv venv --python 3.12 .venv
uv pip install -e 'pipeline[dev,fetch,lidar]' -e 'textures[post,dev]' -r scripts/dev/requirements.txt
```

The requested `@input|@walk` subset passed all 12 tests on WebGPU and WebGL2 in
2.1 minutes with three workers, including the controlled 6.5 s movement-response
delay on both backends. The final scoped gate runs without diagnostic interception:

```bash
source ../sandbox.env
(cd game && npx playwright test --grep '@input|@walk' --workers=3)
scripts/dev/gate --paths game/tests/e2e/gamepad.spec.ts --json
(cd game && npx eslint playwright.config.ts)
```

The verification artifacts are `player-input-walk.log` and `player-startup-gate.json`
in `$TURKU3D_WORK/reports/`. This follow-up uses synthetic fixtures and does not rebuild
the wider AOI. The existing office camera screenshots remain applicable; the player
asset and camera code are unchanged.

The final scoped gate passed all six selected stages in **141.171 s**: policy,
generated materials using the local `.venv`, ESLint, TypeScript, knip and browser
acceptance. Its eight `@input` cases passed on both backends with three workers and
no diagnostic interception. Separate ESLint for the worker-cap configuration passed.
Unrelated pipeline/browser suites were skipped by the normal subsystem selector.
