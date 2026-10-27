# W0-C local pipeline verification

Date: 2026-10-25. Machine: M1 Max, macOS arm64. Worktree: `wt/w0-macpipe`.
Shared input root: `~/turku3d/data`; writable root: `~/turku3d/wt/w0-macpipe/work`.
This run used only sandbox ports 5200–5209 (tile server: 5201).

## Native environment

`make pipeline-bootstrap && make pipeline-versions` passes from a clean shell (`env -i`,
HOME and PATH only). Micromamba 2.8.1, Python 3.12.15, PDAL 2.10.2, python-pdal 3.5.5,
GDAL 3.13.2, scipy 1.18.1, rasterio 1.5.1, geopandas 1.2.0, pyarrow 25.0.0,
laspy 2.7.0, shapely 2.2.0, pyproj 3.8.0, osmium 1.19.1, Triangle 20250106,
pytest 9.1.1, xdist 3.8.0, gltf-transform 4.5.1, gltfpack 1.3.0, roofer 1.0.0,
toktx 4.4.2. The committed conda explicit lock was also checked with an offline create dry run.
Roofer uses the official ARM64 release; no source-build workaround is required.

## Fetch and consolidation

LAZ registration and missing maanpinta downloads: 5 s. WFS: 267 s. OSM clip: 9 s.
WMS: 583 s for 64 + 64 + 900 requests/chunks, no errors. The two full orthos have 100%
coverage; drone has 17.5%. Existing raw LAZ bytes were not changed or fetched again.
See `data-manifests/checksums/REPORT-work-3x3.json` for counts, extents, source epochs,
coverage and expected restricted WFS layers. The Finland snapshot was already cached.

Consolidation verified all 319 files (245,925,086 bytes) before removing the old root.
Active texture/report metadata and repository consumers now use the consolidated paths.
Six QA reference crops were regenerated using the existing 0.64-degree true/grid-north fix.

## Performance changes

- One canonical heightfield for the input area replaces independent L0 interpolations.
  587,581 maanpinta points produce a 1201 × 1201 grid. Cold computation: 20.080 s;
  warm cache load: 0.023 s. Cache identities include source paths/sizes/mtimes, parameters
  and interpolation code. Rebuilding one tile or all four uses the same edge values.
- One Node/WASM encoder worker replaces 64 process startups per four-tile build.
  The codec, material permutation and output bytes remain covered by Python/TypeScript
  round-trip tests. The worker is reused after failed requests and cleaned up at exit.
- A normal tile run performs no network operations, full point-cloud statistics scans,
  remote syncing or raw writes. `fetch-aoi` and `fetch-report` are explicit preparation tasks.

Profiling was done while other worktrees were active, so aggregate timings are not a controlled
benchmark. The original warm build cProfile run took 21.52 s, including 9.76 s in 64 encoder
calls and 68 subprocess launches. The worker version removed 63 encoder process launches;
raw profile files and ground-cache measurements are kept in the sandbox `work/reports`.

## Acceptance results

- Full `make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1"`: **PASS, 169.12 s** under concurrent
  worktree/test/report load. Four textured GLBs, 64 collision files, manifest hashes and every
  shared collision edge checked. Per-L0 construction: 6.00 / 7.92 / 4.85 / 2.12 s.
- Rapier: all four L0 groups PASS (19.406 / 10.717 / 12.205 / 12.787 s), each with 125,006
  ground rays and 2,482 drops, zero misses/voids/below-ground samples. Building/curb/stair checks
  are intentionally inapplicable to Phase 0 ground and remain exercised by synthetic fixtures.
- Chrome: **6/6 tests PASS**, both WebGPU and WebGL2, including the four-L0 walk route.
  The 48 m junction route's height range was 0.032 m on both backends. The route avoids the
  test car parked beside spawn; no controller/physics behavior was changed to make it pass.
- `make tile-show`: HTTP 200 and local tile manifest (4 visual / 64 collision) on port 5201.
- `make pipeline-test`: **53 passed in 6.65 s**. Synthetic source points were reduced to a
  small fixture while retaining the full 64-tile outputs and every seam assertion.
- `make col-test`: **16 Python tests in 1.50 s + 22 Node tests in 61.68 s**, all PASS.
  The Node suite includes the existing full Rapier fixture validation, not just codec tests.
- `make foundation-test`: **20 passed in 2.34 s**; `make qa-test-py`: **18 passed in 0.96 s**;
  `make game-dev-tools-test`: **5 passed in 0.20 s**.
- `python -m turku3d.fetch verify`: **all four registries OK, 7 s**. Report generation: 55 s
  (full statistics over nine large LAZ files; excluded from normal builds).

The fast pipeline gate is within the 5–20 s target. The real-data acceptance run and the existing
Rapier fixture suite are deliberately longer. No geometry checks, walker counts or assertions
were relaxed. Logs, profiles, QA crops and validation reports remain in the sandbox work directory;
only code, small reports and checksum manifests are committed.
