# Draft ground and facade material correction

## Cause and change

Draft ground displayed a single 512 px orthophoto over 250 m (about 49 cm per pixel). Its baked shadows, objects and blue-white patches became ground albedo, and planar aerial UVs stretched on banks. Faster ETC1S compression could not restore street-level detail. The game additionally desaturated draft materials by 55% and applied a cool colour multiplier.

Drafts now classify city street polygons, OSM surfaces/land cover and ground-level road/path corridors. Green aerial pixels only supply fallback vegetation classification. Polygon-boundary cuts preserve the original terrain planes and smooth normals. Own-source repeating asphalt, setts, slabs, grass, gravel and quay stone use metre UVs, with vertical projection on steep faces. Near-vertical faces that only had default paving use neutral rock/gravel rather than bright slabs. Shared textures use 512 px albedo and 256 px normal/ORM per two metres, with existing KTX2 encoding. Draft ground no longer displays the orthophoto as albedo.

Full tiles keep their existing splat classification and texture arrays. Their material albedo now remains visible at all distances; aerial imagery supplies only bounded low-frequency luminance (0.96–1.06), rather than colour/shadows. Normal and roughness detail still fade with distance.

Mapped OSM crossing/lane/stop linework is draped on dry road triangles. Explicit kerbs use an unmeasured 18 cm width and 12 cm raised profile, with matching collision. Water/buildings exclude kerbs; lowered/flush tags and mapped crossings leave openings. Bridge/tunnel/nonzero-layer ways are excluded. Full strips are clipped after extrusion, and terrain heights come from the owning grid on both sides of tile seams. Collision stays in world coordinates until the standard COL encoder subtracts its origin.

Cached register and spatially matched OSM building attributes enrich the existing facade parameter store fallback: material, colour, storeys, era, shop floors and blank sheds/garages. Explicit parameter descriptions retain precedence. The kit adds seven blank modules after stable indices 0–41. Draft rendering preserves supplied material colours. This is source-driven variation, not a claim of measured window layouts where appearance evidence is missing.

## Verification scope

Six draft tiles: `t0_-1_1`, `t0_-2_2`, `t0_-2_3`, `t0_5_-3`, `t0_0_0`, `t0_0_1`. Published full tiles: `t0_0_0`, `t0_0_1`, `t0_1_0`. Local evidence lives under `$TURKU3D_WORK/reports/texture-sanity/`.

The baseline producer is `312849f6`, which includes the landed roof correction. The after producer also includes separately landed traffic poles, so draft geometry/frame counts include that concurrent change. Full before/after renders use one frozen release recorded in `full-source.json`, isolating the ground shader change. Live retention removed its former path during QA, so the release was copied into this sandbox; all three visual GLB hashes match the earlier after renders.

Private reference images remain outside the repository. The private capture reached 97.2% terminal-node coverage at the last owner report, below its required complete coverage, so per-surface reference dE is **unmeasured**. The scorer also abstains on runtime ground shaders and facade kits. Old overview-based colour scores are provisional and are not used as acceptance evidence.

## Commands

Source `../sandbox.env` first. Each heavy command uses host admission; poll at 30 seconds or wrap with `scripts/dev/codex-task wait-cmd --`.

```sh
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.texture_sanity before --baseline-ref 312849f6
python3 scripts/dev/host_slots.py build -- bash pipeline/env/run.sh python -m turku3d.coarse.texture_sanity after
npm --prefix game run dev -- --host 127.0.0.1 --port "$TURKU3D_PORT_BASE"
python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs before 312849f6
python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs after 312849f6
python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs before 312849f6 --full
python3 scripts/dev/host_slots.py browser -- node qa/render/texture-sanity.mjs after 312849f6 --full
bash pipeline/env/run.sh python -m pytest -q pipeline/tests/test_coarse_surfaces.py pipeline/tests/test_facade_kit.py
scripts/dev/gate --fast --base main
```

`views.json` is a local EPSG-derived camera fixture, including player-eye, chase, top-down/oblique and the comparison tool's 20 street poses. Each view warms 40 frames and samples 60 frames using the game renderer, material binding, environment and post-processing. Screenshots and raw CPU/GPU/frame distributions remain local. Append `--perf` for 2560×1440 eye/chase/oblique measurements with unlocked vsync, 120 warm-up frames, 240 measured frames and a final GPU drain. The 720p screenshot samples are not the 1440p budget evidence.

## Measured asset and input checks

All six final draft builds completed. The sixth used a sandbox-only WFS overlay for 83 signs,
65 centrelines and an empty stop layer missing from the shared cache; the shared raw cache was not modified.

| Draft | Visual brotli bytes | Visual plus all referenced shared textures | Kerb triangles | Paint triangles |
|---|---:|---:|---:|---:|
| t0_-1_1 | 1,994,203 | 4,504,717 | 0 | 722 |
| t0_-2_2 | 2,094,798 | 6,548,868 | 2,264 | 1,512 |
| t0_-2_3 | 2,687,546 | 5,377,907 | 7,179 | 575 |
| t0_5_-3 | 2,334,569 | 3,689,513 | 2,591 | 0 |
| t0_0_0 | 1,982,269 | 3,337,213 | 4,426 | 1,944 |
| t0_0_1 | 2,100,180 | 4,610,694 | 0 | 0 |

All are below 8,000,000 bytes even with the conservative per-tile shared-texture charge.
Largest collision cell: 112,072 bytes. All 30 shared edges touched by new kerb triangles have
exactly matching decoded world-coordinate vertex sets. This caught and corrected an early
extra origin subtraction; a non-origin synthetic regression test also covers the COL conversion.

The six tiles contain 151 facade candidates by building: 52 have mapped wall colours, 83 have
material classes, 134 have storey counts, 38 have shop-floor evidence and 12 are blank sheds/garages.
These are input-coverage counts, not measured facade accuracy. The previous draft reader passed
neither mapped colours nor cladding nor shop/blank semantics through this fallback.

Full-tile wire sizes remain unchanged: 7,575,768 bytes (`t0_0_0`), 7,876,841 (`t0_0_1`),
and 7,393,566 (`t0_1_0`), including per-tile ground maps under the existing manifest budget.
The shared library arrays are unchanged. The after drafts also include the independent traffic-pole
landing; `t0_5_-3` additionally includes the later roof fallback fix from `38e57affa949`.

Local numerical evidence: `metrics.json`, `kerb-seams.json`, `before/build-summary.json`,
`after/build-summary.json`. The focused synthetic suite passes 21 tests.

## Visual and frame-time review

The final six draft GLBs have 58 before and 58 after screenshots, including 20 paired player-eye
reference poses. Three frozen full tiles have 21 before and 21 after views. All eight render
reports (`textures`, `full` and their `-perf` variants, before and after) have `complete: true`,
zero browser errors and 100% valid GPU samples. Top-down and low-oblique views of every draft
tile were reviewed. Street views show the aerial blue-white smears replaced by textured asphalt,
paving, grass and gravel, with mapped paint and raised kerbs visible. Facades have more source-driven
colour, cladding and shop/blank variation. The steep terrain correction changes only a small
number of default-paving triangles; pale hard geometry under the bridge in `t0_-2_3` and the
serrated quay/water boundary remain visible geometry issues.

The 2560 × 1440 WebGPU measurements used 120 warm-up and 240 sampled frames at each eye, chase
and oblique view with vsync unlocked. The table gives the largest p95 or GPU-drained time among
18 draft and eight full-tile views, in milliseconds:

| Region | Phase | CPU p95 | GPU p95 | Work p95 | Frame p95 | GPU-drained/frame |
|---|---|---:|---:|---:|---:|---:|
| Draft | Before | 5.23 | 5.19 | 9.33 | 6.04 | 5.10 |
| Draft | After | 6.87 | 6.63 | 12.30 | 7.91 | 5.91 |
| Full | Before | 6.48 | 6.54 | 12.84 | 7.48 | 7.06 |
| Full | After | 6.37 | 6.44 | 12.23 | 7.29 | 6.90 |

Every sampled view is below the 16.6 ms budget. Draft before/after timing includes concurrent
traffic-pole and roof changes and shared host load, so it is a budget check rather than an
isolated speed claim. Full timing uses identical frozen GLBs, isolating the shader correction.
Raw samples and per-tile maxima are in `render-*-perf.json` and local `metrics.json`.

Authoritative per-surface CIE76 dE against the private reference remains **unmeasured**: its
capture has only 97.2% terminal-node coverage, below the private scorer's complete-coverage
gate. Runtime shader and facade-kit colours are separate scorer abstentions. The 20 own-render
before/after pose pairs are retained locally; no incomplete reference value or image entered the
repository or texture inputs. Once the reference capture qualifies, the private tool can score
the immutable six-tile builds and create local triplets. Remaining shoreline and under-bridge
geometry should be corrected from our own lidar, OSM and city sources in a separate geometry pass.
