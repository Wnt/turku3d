# Ground textures: first integrated office-core version

The first integration rebases the original ground-texture work onto the landed ground,
assembler, and renderer. Splats now use the published ground triangle material IDs: all four
visual cores report 100% semantic coverage, with 25 neighbouring collision inputs per tile.
Collision-only water beds are excluded. The renderer retains the river material.

## Changes

- Assembler-declared, content-addressed ground bindings validate both source geometry and map hashes.
  Missing or stale products retain the existing library fallback.
- MaterialStyler shares eight-layer PBR arrays and owns tile maps through load/unload, including
  late asynchronous completion. QA readiness waits for ground textures.
- The original 2022/2025 colour calibration, cleaned 4 cm ortho, eight generated PBR materials,
  world-space stochastic sampling, and mip gutters remain in use.
- Incremental `--splats-only` avoids rereading/reencoding the orthos when semantics change.
- L0 transfer accounting now includes ground maps in addition to the compressed building/terrain GLB.
  Runtime ortho encoder quality is reduced where needed; close PBR detail and pixel sampling are unchanged.

## Verification

Exact build/server/capture commands: [ground material README](../../game/src/render/ground/README.md).
Fast checks: `source ../sandbox.env && make -j2 ground-texture-test`.
The scene capture runner uses real assembled terrain and building shells at three office-core
positions, with a raycast 1.7 m eye height. Before/after cameras and lighting match. Baseline
requests remove only the declared ground binding; no scene geometry is substituted.

Artifacts are under `/Users/wnt/turku3d/wt/w1-10-groundtex/work/`:

- `dist/tiles/manifest.json`: assembled scene and per-tile transfer accounting.
- `textures/ground/MANIFEST.json`: real input hashes, splats, ortho provenance and GPU residency.
- `reports/ground-scene/`: before/after PNGs, camera/mesh/error records and image hashes.
- `reports/ground-gate-green.log`: final gate evidence.

GPU ground allocation is 57,180,080 bytes per L0 plus 33,555,072 bytes for the shared library:
262,275,392 bytes for four resident tiles, below D4's 300,000,000-byte cap. This accounts for ground
assets, not other layers or renderer attachments. Archived year products are not runtime-resident.
The original source-overlap seam check remains in `textures/ground/seams.json`; final screenshot
inspection covers the office street views and the z=250 boundary.

## Screenshot evidence

Three matched WebGPU views passed with no console errors. The cameras are exactly equal before
and after, at 1.7 m above the raycast terrain. All six PNG SHA-256 values were verified. The after
views contain 21, 15, and 21 textured ground meshes respectively, alongside real building shells.
Visual inspection shows sharp paving and no visible texture colour seam at the z=250 crossing.
The existing river-bank geometry gaps and untextured building shells remain visible in both modes.

[Three-view before/after comparison](../../../work/reports/ground-scene/comparison.jpg)
(before on the left; after on the right). Full-resolution PNGs and the manifest are in the same directory.

## Gate and transfer results

The full selected gate passed all 13 stages in **628.612 seconds**, using serial stages and two
browser test workers under heavy host contention. No checks or thresholds were removed. The exact
bounded invocation is in the material README. Fast splat and binding tests use synthetic inputs.

| Tile | GLB plus runtime ground maps (bytes) |
|---|---:|
| t0_0_0 | 6,841,183 |
| t0_0_1 | 6,961,675 |
| t0_1_0 | 7,891,946 |
| t0_1_1 | 7,326,235 |

Each material JSON adds less than 10 KB, so all remain below 8 MB including metadata. The shared
library is downloaded once separately. The four retained ortho encoder quality settings are
80, 16, 128, 16 respectively; all retain 6380² pixels including gutters and full mips. Future full
builds default runtime ortho to quality 16. The archived years retain their original encodes.

## First-version scope and follow-ups

The requested first version integrates the available ground and buildings. Building facade quality,
missing props/structures, and full-scene performance remain their owners' work. The current ground
layer is the accepted plain terrain, without the later measured curb refinement.

The original full `make ground-acceptance` audit is intentionally unchanged. It still requires
verified lane/stop-line vectors and the complete original material-preview matrix. The existing
OSM/city crosswalk extraction is not yet terrain-draped into shipped geometry. These paint items
are follow-ups to this timeboxed integration, not claimed complete here. Full-scene WebGL captures
can be run with the same QA command; the first-version evidence prioritizes three matched WebGPU
views. The separate synthetic ground shader check covers both renderer backends.
