# W1-10 ground textures — 2026-10-25

**Result: BLOCKED for full acceptance.** The texture pipeline, library and renderer API are
implemented and tested. The current branch has no W1-01 ground triangle layers, so the built
splats are explicitly ortho-only previews. Verified lane/stop-line geometry and integration
with the W1-04 assembler / W1-08 streamed renderer remain outstanding.

## Delivered

- Four visual tiles: `t0_0_0`, `t0_0_1`, `t0_1_0`, `t0_1_1`.
- Per tile: 2022 true ortho, colour-matched 2025 ortho, temporal/shadow-reduced combined ortho,
  two material-weight textures, uncertainty mask and material metadata.
- 4 cm output sampling; 6380² including the 65-pixel gutter. Native source detail remains
  approximately 5.5/5.2 cm. Close detail comes from 2 m library repeats, not invented aerial detail.
- Eight generated PBR sets: fresh/worn asphalt, granite curb, cobblestone, concrete slabs,
  grass, gravel and quay stone. Source images were generated with built-in image_gen through
  the quota-aware `textures/` runner. Prompt definitions: `textures/library/ground.json` and
  `textures/turkutex/ground/jobs.py`. All eight calls succeeded; thread IDs, prompts, job keys
  and source hashes are retained in the sandbox runner records.
- Eight individual library KTX2 sets and three shared eight-layer KTX2 arrays. Albedo seam
  ratios are 0.783–0.938 (all pass). Native normals preserve RGB XYZ explicitly; a decoded
  8×8 fixture catches toktx's default RGB=X / A=Y packing.
- TSL API for WebGPU and WebGL2: material weights, world-coordinate stochastic sampling,
  phase-preserving concrete slab offsets, normal/ORM detail, and an 8–45 m ortho transition.
  Six samplers cover all eight materials. TSL shared coordinates are evaluated before material
  branches; screenshot pixel tests prevent the previously observed flat-channel failure.
- 4,013 crossing-paint polygons across the cached `work-3x3` area, derived from OSM crossing
  lines and clipped to city carriageways. This count is for the larger source area, not only
  the four visual tiles. Assumed widths and unresolved point crossings are recorded.
- Source licence/attribution records in `textures/library/ground-sources.json`. City data uses
  CC BY 4.0; OSM uses ODbL. Generated library output is not labelled CC0.

No shipped GLB, `.col`, tileset or `main` branch was modified. Renderer/environment callbacks
were left to W1-08; the explicit material integration API is documented in
[game/src/render/ground](../../game/src/render/ground/README.md).

## Memory and wire size

| Item | Bytes |
|---|---:|
| One L0 tile: combined ortho + two splats, complete mip chains, conservative 8 bpp | 57,180,080 |
| Shared eight-material library, complete mip chains, conservative 8 bpp | 33,555,072 |
| Four resident tiles + shared library | 262,275,392 |
| D4 whole-scene texture limit | 300,000,000 |
| Remaining for other scene textures | 37,724,608 |

Archive 2022/2025 textures are not simultaneously resident. Uncompressed RGBA fallback would
exceed the budget. The estimate assumes block-compression support and does not claim the
unfinished buildings/props fit the remaining allowance.

Runtime KTX2 files total 6,461,677 / 6,524,620 / 6,555,233 / 6,954,132 bytes per tile,
respectively. Geometry still needs to fit within the assembler's 8 MB wire budget.
The four texture builds took 256.94 / 157.14 / 188.00 / 205.10 seconds on the shared Mac.

## Visual evidence and seams

Artifacts are under:

```
/Users/wnt/turku3d/wt/w1-10-groundtex/work/textures/
/Users/wnt/turku3d/wt/w1-10-groundtex/work/reports/ground-qa/
```

[Three-view comparison, before left / after right](../../../work/reports/ground-qa/comparison.jpg).
Individual 1280×960 captures are `webgpu-{0,1,2}-{before,after}.png` and
`webgl-{0,1,2}-{before,after}.png`. The poses use the three office registry x/z positions and
headings, with a 1.7 m eye and downward pitch. These are explicitly **flat ground material
previews**, not complete city screenshots. All 12 final captures completed without browser
or shader errors. Their hashes are in [ground-textures.json](ground-textures.json).

Visual review: the normal-packing fix removed the noisy lighting artifact, and whole-slab
stochastic offsets removed doubled paving joints. Close textures are sharp. Ortho-only
classification still produces incorrect asphalt/slab patches across shadows; ground triangle
semantics are required before claiming believable street surfaces. There are no assembled
curbs/buildings/paint decals in this preview. Full in-game visual sign-off is not claimed.

Twelve source-gutter comparisons passed: 99th-percentile channel difference is zero for every
comparison. North/south shared samples are identical. East/west resampling differs by up to
1 code value in primary imagery; temporal thresholding creates rare larger outliers (maximum
29, maximum mean difference 0.002503). These are not bitwise-identical source textures.
Independent KTX2 encoding and distant mip transitions still need full in-game review.

## Verification

From this worktree:

```sh
source ../sandbox.env
make -j2 ground-test
make textures-test
make game-test game-build
GROUND_QA_PORT=5310 make ground-render-test
GROUND_QA_PORT=5310 make ground-qa
PYTHONPATH="$PWD/textures:$PWD/pipeline" bash pipeline/env/run.sh \
  python -m turkutex.ground.seams "$TURKU3D_WORK/textures/ground"
make ground-acceptance
```

Observed results:

| Check | Result | Time |
|---|---|---:|
| Focused ground Python tests | 13 passed | 2.14 s |
| Ground TypeScript contract tests + typecheck | 2 passed / clean | approximately 1 s |
| Complete texture suite, four workers | 45 passed | 11.88 s initially; 91.29 s contended repeat |
| Complete game unit suite | 46 passed | 3.94 s |
| Production game build | passed | Vite 0.667 s, plus typecheck |
| Synthetic Chrome WebGPU/WebGL2 render + pixel variation | passed | 52.77 s initially; 112.63 s contended repeat |
| Final real before/after captures, both backends | 12 passed | 83.52 s (earlier run 40.18 s) |
| Source-grid seams | 12 comparisons passed | 25.50 s |
| Real acceptance audit | blocked as below | 2.01 s |

The fast gate is the small synthetic `ground-test`, approximately 3 seconds overall. Real
encodes, full-size seam comparisons and Chrome captures are separate integration checks.
The last repeat used 14.55 CPU seconds for the texture suite and 5.97 CPU seconds for the
synthetic browser runner despite much longer wall time; the Mac was shared with other agents.
The focused gate had already completed in 3.24 seconds, and no other agents' processes were
interrupted to improve these timings.

Three warnings originate from the native rasterio/affine dependency's pending deprecation;
no test or check was weakened or skipped to hide a failure.

`make ground-acceptance` exits nonzero with:

```
BLOCKED: t0_0_0: W1-01 ground triangle material inputs absent
BLOCKED: t0_0_1: W1-01 ground triangle material inputs absent
BLOCKED: t0_1_0: W1-01 ground triangle material inputs absent
BLOCKED: t0_1_1: W1-01 ground triangle material inputs absent
BLOCKED: stop_line: no verified vector paint emitted
BLOCKED: lane lines: no verified vector paint emitted
make: *** [ground-acceptance] Error 1
```

## Concrete handoff

1. W1-01: supply the ground `.col.npz` layer. Rerun
   `make ground-textures GROUND_LAYER="$TURKU3D_WORK/layers/ground"`; unchanged encodes use
   content-hash sidecars. Check material classification against the real streets.
2. Data/road-marking work: obtain verified lane/stop-line geometry. A fresh WFS capabilities
   and `GIS:Kanta_Liikennevayla` probe found only 62520 carriageway edges and 62525 interpreted
   paths in the Phase 1 bounds. Neither was mislabelled as road paint. Probe evidence is in
   the sandbox's `reports/ground-{wfs-capabilities,city-road-classes}.json`.
3. W1-04/W1-08: copy the texture inputs, bind `loadGroundTile` on ground meshes, share the
   library, dispose per-tile resources, and drape vector paint on real ground triangles.
   Re-run the three views in the complete scene and verify distant seams and the remaining
   whole-scene memory allowance.
4. Rebase on landed Wave 0 tooling before orchestration. The bounded DeepBump worker changes
   a shared texture post-processing entry point and may need reconciliation with W1-09.
