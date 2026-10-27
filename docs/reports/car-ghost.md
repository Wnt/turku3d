# Moving-car temporal ghost regression

2026-10-25, `fix-car-ghost`, baseline `04037fcfeac5` (main at sandbox creation).
Chrome 156, M1 Max Metal, WebGPU and WebGL2. Synthetic development tile; no raw AOI data required.

## Cause and fix

`PostProcessing` sets the packed-normal output to `UnsignedByteType`. Three.js r186 `PassNode.getTexture()`
creates additional MRT attachments lazily by cloning that output. The velocity attachment was consequently
UNORM8 too. Negative NDC motion was clamped to zero, and positive/subpixel motion was quantised to 1/255 NDC.
TRAA then fetched history at the wrong screen coordinates, leaving repeated body/window/wheel edges.
A GPU readback during a leftward drive confirmed zero motion where the projected car moved 7–17 pixels per frame.

Set the velocity attachment explicitly to `HalfFloatType`. Packed normals stay unsigned bytes, and TRAA remains
active by default at every quality tier. The change applies to every moving mesh, including the articulated wheels.
No asset duplication or velocity-shader replacement is involved.

The diagnostic URL parameter `?aa=off|taa|fxaa` defaults to `taa`. Off and FXAA disable temporal jitter/history and
AO temporal sampling. FXAA runs after tone mapping and sRGB conversion; output conversion happens only once.
Lighting, bloom and grading still run. The ID pass still bypasses post-processing.

## Reproduction and regression

```sh
source ../sandbox.env
npm --prefix game run build
(cd game && ./node_modules/.bin/playwright test tests/e2e/car-ghost.spec.ts --workers=1)
(cd game && ./node_modules/.bin/playwright test tests/e2e/render-controls.spec.ts --grep 'AA diagnostic' --workers=1)
GATE_PYTHON=~/turku3d/envs/pipeline/bin/python scripts/dev/gate --json
```

The driver enters the car and accelerates through the normal keyboard/physics-worker/mesh path. At 15 m/s the
throttle is released. Two fixed roadside viewpoints produce negative and positive screen motion, separating the
velocity test from chase-camera timing. Each viewpoint captures 20 consecutive animation frames at 960×540;
frame IDs must increase by exactly one. Each sample must remain within 10–20 m/s, and visual translation must
match physics. At least nine moving frame pairs in each direction must be exercised.

The test reads the GPU velocity at a visible roof point and checks that its history lookup lands within 1.25 pixels
of the previous projected physics position. This detects incorrect temporal reprojection rather than relying on a
single screenshot. Full colour PNG sequences also show the repeated window and wheel edges before the fix.
Both projects failed this assertion against the original production sources, with no assertion changes afterward.

First focused acceptance measurements (maximum history-position error over moving frames):

| Backend | Baseline | Fixed | Captured speed range, fixed |
|---|---:|---:|---:|
| WebGPU | 17.366 px | 0.179 px | 14.25–15.04 m/s |
| WebGL2 | 17.497 px | 0.128 px | 14.21–15.00 m/s |

The 8 focused checks passed (motion regression plus all three AA settings, both backends).
The baseline regression log, per-frame measurements and images are retained in the sandbox:

- `$TURKU3D_WORK/reports/car-ghost/before-run.log`: both baseline failures.
- `$TURKU3D_WORK/reports/car-ghost/{before,after}/{webgpu,webgl}/motion.json`: projected motion, GPU readback,
  history-position error, physics/visual positions, speed and frame ID.
- The same directories contain `-1-0.png` through `-1-19.png` and `1-0.png` through `1-19.png`.
- `$TURKU3D_WORK/reports/car-ghost/before-after-strip.png`: frames 5–8, leftward motion, both backends;
  tracked crops at 2× nearest-neighbour scale. PNGs remain outside Git.
- `$TURKU3D_WORK/reports/car-ghost/gate.json`: complete selected-gate output and timings.

`GHOST_CAPTURE=before` selects a separate capture directory when running the regression against the baseline.
Normal test/gate runs refresh `after`. No timeouts or existing checks were relaxed.

## Gate result

Selected gate **GREEN**, 7 stages passed, **361.396 s** total under concurrent host load. ESLint, TypeScript,
knip, policy/generated-material checks, 10 related unit tests, 34 game browser tests and 4 synthetic tile browser
tests passed. The two existing four-tile seam tests are skipped for the gate's single-tile fixture. The browser
stage accounts for 358.740 s; no budgets, checks or timeouts were weakened.
