# W1-04 assembler verification — 2026-10-25

Branch: `wt/w1-04-assembler`. Historical pre-rebase commits: shared layer contract first as `9934bd7`.
Runtime recovery: `553b929`. Layer packaging: `e02b743`. Complete coarse DTM coverage:
`d71223e`; persistent coarse sibling quadrants: `b103332`.

## Scope and inputs

The L0 integration inventory was explicitly generated synthetic ground, not real Turku
buildings or streets. No real Wave 1 layer manifests were available in this sandbox.
The 500 m / 1 km DTM and ortho were built from the existing shared maanpinta and 2022 ortho
cache, read-only. Outputs, encoded texture cache, reports and Chrome profiles stayed local
to this worktree/sandbox. Chrome used port 5291.

Production `make tile` reads layer manifests. The retired ground/stub builder exists only
under test fixtures. Unit tests isolate the raw-data root and use tiny synthetic layers,
including triangles, primitives, roofs, facades and a four-point LAZ/raster fixture.

## Commands and results

```sh
make tile-test
make game-test
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" TURKU3D_PORT_BASE=5290
```

- `make tile-test`: **20 passed**, **8.70 s**. One dependency deprecation warning from
  rasterio's affine multiplication. Includes codec precision, ETC1S/UASTC+Zstandard headers,
  facade fallback/rebinding, mixed layers, seam rejection, parent coverage/bounds, incremental
  AOI flags, removed files, output corruption repair and unchanged-build timestamp checks.
- `make game-test`: **49 passed**, **6.73 s**. Includes transient fetch/backoff, permanent
  size/CRC failures, failed-step acknowledgements and continued worker message processing.
- Full four-tile `make tile`: **exit 0**, **93.09 s** wall time. The assembly phase reported
  `changed: []` for all nine visual tiles. Vite/typecheck passed. All four Rapier runs passed
  format, holes and below-ground checks; each cast 125,006 coverage rays with zero misses.
  Buildings/curbs/stairs were explicitly skipped for the ground-only integration fixture.
- Chrome: **8 passed**, approximately **1 minute**, across WebGPU and WebGL2. Walking,
  all four L0 seam crossings, textured color/ID/projection passes, and the new outside-AOI
  coarse terrain view all passed. The latter identifies `t1_1_1` ground at the image centre.

At this initial verification, `scripts/dev/gate` and repository `AGENTS.md` were not present;
the brief's area-target fallback was used. No tests or thresholds were weakened.

## Output measurements

- 4 L0 GLBs, 4 persistent 500 m terrain GLBs, 1 full 1 km parent; 64 collision tiles.
- Maximum **synthetic L0** size: **17,114 bytes brotli**, measured at the server's quality 5.
- Maximum **synthetic collision** size: **352 bytes**. These sizes do not establish the
  budgets of future real L0 layers; production assembly enforces 8 MB / 1 MB respectively.
- Maximum decoded visual position error: **0 m**. Collision precision is checked against
  float64 source positions within **2 mm**, followed by lossless float32 codec round trips.
- Manifest hash errors: **0**. Decoded continuous collision seam errors: **0**.
- The 1 km parent covers engine x/z `[0,1000]`, with 8,192 terrain triangles; its 500 m
  children each contain 2,048 terrain triangles. Encoded ortho maps are embedded KTX2.
- A repeated unchanged build performed no writes. A measured cached invocation took **4.78 s**
  before the three extra coarse sibling leaves were added; the final full command also
  confirmed a nine-tile cache hit.

The first synthetic render fixture used fine random pixels, which mipmapped to nearly flat
color and failed the existing `>50` color-variety assertion. Replacing that test image with
broad diagnostic color bands produced 376 colors in both backends; the assertion was retained.

## Reproduce the synthetic inventory

Only generate into an empty fixture sandbox; do not replace a producer's published layers.
The integration invocation used:

```sh
bash pipeline/env/run.sh python pipeline/tests/fixtures/make_layers.py \
  --out "$TURKU3D_WORK/build/layers"
make tile T="t0_0_0 t0_0_1 t0_1_0 t0_1_1" TURKU3D_PORT_BASE=5290
```

See `pipeline/turku3d/package/README.md` for separate fixture input/output paths.
The full-footprint coarse coverage test requires the cached working-area LAZ and ortho;
without complete inputs, the assembler uses available published ground for parents.

## Follow-ups

Run the same four-tile command with the ground, structures and buildings agents' actual published manifests to
measure real L0 budgets and enable their geometry-specific validator checks.

## W0 / Wave 1 integration verification

Rebased the six implementation commits onto main `b3e34d8`, preserving the character/vehicle
protocol, camera casts, request-capacity optimization, browser isolation, QA and test tags.
The shared layer helpers remain the first implementation commit (`4a0bf65`).

The gate now generates four explicit synthetic layer tiles and invokes the production assembler,
with isolated raw/texture roots. Registered the layer/fixture test dependencies and visual Node
entry point. Restored the now-used `ColFormatError` export, applied Ruff formatting, and aligned
facade lookup with `$TURKU3D_WORK/textures/facades`. No checks, assertions or time limits changed.

```sh
source ../sandbox.env
scripts/dev/gate --base main --json
make tile-test
```

The complete gate passed all **19 stages in 267.640 s**: 79 pipeline, 74 game unit, 32 QA Python,
32 texture, 11 gate and 14 tooling tests; both typechecks, lint, knip, generated data, validator
and vehicle suites passed. Browser checks passed 18 game routes plus 6 four-tile routes on
WebGPU/WebGL2. Two outside-AOI coarse tests require real cached terrain and are skipped in the
offline gate; those views passed in the initial native integration above. One optional DeepBump
test remains skipped. The focused native packaging target passed **20 tests in 23.90 s**.

Evidence: `$TURKU3D_WORK/reports/rebase-gate-final.json`, `rebase-assembler-tests.log` and
`rebase-gate-tests.log`. An initial loaded-host run hit the existing 20 s tooling timeout; its
cache test also observed source edits during hashing. The final fixed-source run passed both.
