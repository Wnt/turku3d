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
export GATE_PYTHON="$HOME/turku3d/envs/pipeline/bin/python"
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
