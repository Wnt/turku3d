# Ground texture products

W1-10 builds assembler inputs in `$TURKU3D_WORK/textures`, never shipped GLB, `.col`, or tilesets.
Shared `raw/` data is read-only. Coordinates and the 250 m grid come from `turku3d.config`.

## Build

Run from the repository root with the native Mac toolchain installed:

```sh
source ../sandbox.env
export PYTHONPATH="$PWD/textures:$PWD/pipeline"
export TMPDIR="$TURKU3D_WORK/tmp"
mkdir -p "$TMPDIR"
make ground-jobs
bash pipeline/env/run.sh python -m turkutex codex run \
  "$TURKU3D_WORK/textures/ground-jobs.jsonl" \
  --out "$TURKU3D_WORK/textures/ground-run" \
  --cache "$TURKU3D_WORK/cache/codex" -j 2 --max-pct 100
make ground-library
make ground-textures GROUND_LAYER="$TURKU3D_WORK/layers/ground"
make ground-markings
bash pipeline/env/run.sh python -m turkutex.ground.seams "$TURKU3D_WORK/textures/ground"
make ground-qa ground-acceptance
```

`GROUND_LAYER` contains `c_i_j.col.npz` arrays defined by COMMON.md: `positions` float64 world-local,
`indices` uint32 triangles, `material` uint8 per triangle. All neighbouring collision tiles that
intersect the texture gutter are read. Vertical faces are ignored by the planar splat; curb tops
keep their material. Unsupported classes (water/building/unknown triangles) fail validation.
Absent files leave uncovered pixels for ortho segmentation; a supplied directory with no
intersecting files fails. Omitting `GROUND_LAYER` deliberately produces an **ortho-only preview**,
records zero triangle coverage, and fails real acceptance. It is not a shipped fallback.

The OSM default is the cached 2026-10-24 snapshot; override `GROUND_OSM` for another snapshot.
The marking report records all source hashes, assumed crossing widths and unresolved point
crossings. It explicitly lists absent lane and stop-line geometry. Source CRS is EPSG:3877;
the assembler converts through `turku3d.config`, clips, triangulates and drapes the paint.

## Products and memory

Each `ground/t0_i_j/` contains:

- `ortho2022.ktx2`: primary true ortho, resampled to 4 cm.
- `ortho2025.ktx2`: recent ortho, colour-matched to the primary.
- `ortho.ktx2`: conservative temporal-outlier selection and shadow reduction.
- `splat0.ktx2`, `splat1.ktx2`: eight material weights, two linear RGBA UASTC maps.
- `material.json`: texture URIs, north-first image orientation, gutter transforms, material
  order, source triangle hashes, coverage, uncertainty, GPU bytes and measured time.
- Source PNGs and `uncertainty.png` for inspection. They are not runtime assets.

The 6250-pixel core has a 65-pixel gutter on each side: **6380×6380** images, block aligned.
The splat core is 1024 pixels with an eight-pixel gutter: **1040×1040**. Splat weights use
largest-remainder quantization (sum exactly 255), then the shader renormalizes decoded values.
Albedo is sRGB ETC1S; splats are linear UASTC+zstd. All products have full mip chains.
Native toktx uses four threads. Large ortho images use quality 128 / compression effort 0;
small library materials use quality 160 / effort 2. Compression effort affects build time;
texture sampling and mip allocation are unchanged. Encoder sidecars provide content-hash caching
and outputs are atomically renamed after successful encoding.

The shared library has eight 1024² PBR sets and three eight-layer array textures. Each source is
an actual image_gen result from the quota-aware runner, with job key, thread ID, source hash and
prompt version recorded. It is generated output under OpenAI terms, **not a CC0 claim**. Albedo
is flattened and quilted for wrapping; normal/height use the documented Sobel estimator and ORM
uses ground-specific presets. Every albedo must pass the existing seam test before encoding.
Native normal encoding uses `--normal_mode --input_swizzle rgb1` to preserve XYZ in RGB,
rather than toktx's default X/Y packing. A decoded-pixel regression checks this contract.
The individual `{material_key}/{albedo,normal,orm}.ktx2` files satisfy the assembler library
lookup contract; runtime ground sampling uses the arrays to stay within WebGL2 sampler limits.

`MANIFEST.json` reports GPU allocation using complete mip chains and 4×4 block sizes, rather
than `.ktx2` bytes. The worst supported 8-bpp estimate includes four resident ortho/splat sets
and the shared library, compared with D4's 300,000,000-byte whole-scene limit. Remaining budget
must also cover buildings/props/other textures. Archive 2022/2025 textures are **not** loaded in
addition to the combined ortho. Uncompressed RGBA fallback is not budget compliant.

Source licences and attribution strings are in `textures/library/ground-sources.json`, verified
against the [City of Turku source policy](https://www.turku.fi/tietoa-turusta/kartat-ja-paikkatieto)
and [OpenStreetMap copyright page](https://www.openstreetmap.org/copyright). The assembler must
preserve these source credits in the shipped attribution records.

## Limits and acceptance

A 4 cm grid is output sampling: the native imagery contains approximately 5.5 cm (2022) and
5.2 cm (2025) detail. No new aerial detail is claimed; the 2 m library maps provide close detail.
Two dates cannot guarantee complete vehicle/person removal. Large temporal colour changes and
missing single-epoch coverage are written to the uncertainty mask. Colour calibration uses
one fixed named-area footprint, independent of tile list/order. Filters use world-metre radii
and overlap gutters. `seams.py` checks matching source texels in neighbours; independent lossy
KTX2 blocks and coarse mip seams still require rendered inspection.

`make -j2 ground-test` runs small synthetic tests. `make ground-render-test` checks both actual
Chrome backends and verifies that material detail survives shader branching. `ground-acceptance`
is a separate real-data audit and deliberately fails when required upstream data or QA captures
are absent. See `game/src/render/ground/README.md` for the renderer API and integration steps.
