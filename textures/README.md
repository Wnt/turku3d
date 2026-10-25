# textures/ — photo-referenced facade production (Mac)

`turkutex` runs reference discovery, strict-schema building labels, one cleaned image per street
facade, deterministic PBR processing, and an offline coverage/contact sheet. The assembler binds
these textures; this package does not write shipped geometry or tilesets.

Artifacts use `$TURKU3D_WORK/textures/`, WFS downloads use WORK/cache/wfs, and dependencies
use WORK/tools. New panorama/image downloads use the shared atomic cache
`$TURKU3D_DATA/cache/streetlevel`. Legacy WORK/cache/streetview and shared
`$TURKU3D_DATA/raw/streetview` are read-through caches and are never modified. `TURKUTEX_READ_CACHE` can add read-only caches (colon
separated). Without a sandbox, outputs use `turku3d.config.data_root()`. Raw panos are never shipped.

## Setup and fast tests

Run from the repository root after sourcing your sandbox environment:

```sh
source ../sandbox.env
make textures-setup       # Python 3.12, DeepBump, pinned KTX-Software 4.4.2 (Mac arm64)
make textures-test        # synthetic fixtures + fake Codex, three pytest workers
make textures-usage      # quota snapshot; no model call
```

`make textures-ktx` downloads the official Khronos package, verifies its pinned SHA256, and extracts
it under `$TURKU3D_WORK/tools/ktx`. It does not run a system installer. On other platforms supply
`toktx` and `ktx` on PATH, or set `TURKUTEX_TOKTX` to the executable. The old WASM encoder was
removed: it was substantially slower and emitted linear-map primaries incompatible with glTF.

`streetlevel==0.12.13` is installed with `--no-deps` plus `requirements-streetlevel.txt`; unsupported
`pyexiv2` is stubbed only in the Street View adapter. DeepBump uses ONNX, not torch.

## AOI workflow

Use W1-03's `$TURKU3D_WORK/build/layers/buildings/facades.geojson` as soon as it is available.
Until then, derive the full four-visual-tile AOI plus margin from the existing footprint extractor:

```sh
make textures-facade-inventory OUT="$TURKU3D_WORK/textures/aoi/all-facades.geojson"
export FACADES="$TURKU3D_WORK/textures/aoi/all-facades.geojson"
make textures-library-batch FACADES="$FACADES" TEX_J=6 TEX_MAX_PCT=100
make textures-facade-batch FACADES="$FACADES" TEX_J=6 TEX_MAX_PCT=100

```bash
# From the repository root, after sourcing the sandbox environment
make textures-setup        # textures/.venv + npm ci in textures/node + DeepBump clone in <data>/tools/DeepBump
make textures-test         # offline fixtures, four pytest workers
make textures-usage        # {"used_percent": 1, ...}  (free: codex app-server JSON-RPC, no model call)
```

The temporary `turkutex/inventory.py` explicitly marks heights and facade indices provisional.
Its AOI uses `turku3d.config` tile bounds and margin, without another coordinate/grid definition.
Delete this extractor when W1-03's facade contract lands. Do not ship its provisional indices as
if they were the building mesher's indices.

Individual stages can be resumed independently:

```sh
textures/.venv/bin/python -m turkutex facade-batch refs "$FACADES" --ref-workers 3
textures/.venv/bin/python -m turkutex facade-batch label "$FACADES" -j 6
textures/.venv/bin/python -m turkutex facade-batch improve "$FACADES" -j 6
textures/.venv/bin/python -m turkutex facade-batch post "$FACADES"
textures/.venv/bin/python -m turkutex facade-batch report "$FACADES"
```

Default run directory: `$TURKU3D_WORK/textures/aoi`. Override with `--run`, `--refs`, `--output`.
`report` exits 1 until every listed facade and roof is covered. Missing data remains visible;
empty job batches do not imply a complete AOI. For long acquisition runs, `label` can process
buildings whose reference attempts are already complete while other buildings are still downloading.

### Input

GeoJSON FeatureCollection, EPSG:3877 LineStrings `[[E0,N0],[E1,N1]]`, properties:

- `id`: unique safe identifier; `building_id`: safe identifier; `facade_index`: nonnegative integer.
- `height_m`: positive wall height; optional `base_h`: N2000 base height.
- `normal`: `right` (default) or `left` relative to the segment direction.
- `street_facing`: boolean; `material_key`: courtyard/fallback material;
  `roof_material_key`: roof material. Metadata such as `address`, `floors`, `year` are retained.
- Optional `bay_count` from W1-03 geometry. It overrides the label count. Image labels never change
  geometry heights or authoritative bay counts. Missing geometry counts remain explicitly unknown.

Duplicate IDs, output-path collisions, unsafe IDs, zero-length segments and invalid dimensions fail.
Reference geometry must still match the input; a material/index change alone does not invalidate crops.

### References

Default sources: `google,lookaround,mapillary,photospheres`. `--sources` selects available providers.
Selection ranks angle, distance, resolution, depth-proxy occlusion and recency, then adds viewpoint
and facade-section diversity. It retains at least one full view when available and up to four views.
Each chosen view has a perspective crop, outlined crop, approximate occlusion mask, **wall-plane
rectified** crop and rectified mask, with pose/date/panorama provenance and its covered u interval.
Cached successful reference sets do not refetch or rewrite images.
Transient provider failures are recorded by provider and exception type, without request URLs.
Other available providers and replacement views remain usable; a partially failed source search
is retried on the next reference run.

Google depth is flipped horizontally; camera heights follow the verified campaign table. Apple
camera height and panorama pitch/roll remain approximate. Masks catch nearer depth planes, not
all trees or vehicles. Label jobs judge visual usability; entirely rejected references are never
silently promoted into usable inputs. Photospheres use the same third-party discovery RPC as
`tools/refs/gmaps_photospheres.py`.

Mapillary Graph v4 uses `Authorization: OAuth <token>` from
`~/.config/turku3d/mapillary.token`. The token is never put in a URL, logged or copied. Pagination
uses cursors, not token-bearing next URLs. Spherical, calibrated perspective and calibrated fisheye
images are supported. Perspective/fisheye require focal/radial parameters, a computed rotation,
EXIF orientation 1 and at least 95% wall coverage in the source photo; unsupported metadata stays
in the cache. Projection follows MapillaryJS's OpenSfM model and converts grid ENU into true ENU.
Camera height remains approximate: labels must reject misregistered or wrong-building crops.
[Official field reference](https://mapillary.github.io/mapillary-python-sdk/docs/mapillary.config.api/mapillary.config.api.entities/).

### Codex and provenance

One strict-schema label job per building produces `facades/<building_id>/spec.json`; one improve
job per usable street facade preserves the entire unique wall, including doors and ornaments.
Long walls are not replaced by repeated bays. Library jobs create missing registered roof,
courtyard, plinth and ground materials. Defaults: `gpt-6-astra`, six concurrent runners,
read-only child sandbox, weekly cap 100%, fail closed on unknown usage.

The runner validates JSON against the schema locally. A content hash covers prompt, schema,
model and input bytes. Resume/cache hits verify output image hashes. `attempts.jsonl` includes
all attempts, including retries; `out.jsonl` uses the last record per ID. The rollout records exact
image_gen prompts and generated-image count, selected output, tokens and quota readings.
`usage_log.jsonl` records stage snapshots; `usage.jsonl` records each call. Usage is account-wide,
so a before/after difference cannot be attributed exclusively to this worker while others run.

```sh
textures/.venv/bin/python -m turkutex codex run JOBS.jsonl --out RUN_DIR -j 6 --max-pct 100
textures/.venv/bin/python -m turkutex codex collect THREAD_ID --copy OUTPUT_DIR
```

### Outputs and QA

Sandbox staging mirrors the assembler contract:

```text
$TURKU3D_WORK/textures/
  facades/<building_id>/<facade_index>.{ktx2,json}
  facades/<building_id>/<facade_index>.{normal,orm}.ktx2
  facades/<building_id>/spec.json
  facades/<building_id>/bindings.json
  library/<material_key>/{albedo,normal,orm}.ktx2
  library/<material_key>/material.json
  refs/<facade_id>/...
  post/<facade_id>/{albedo,normal,height,orm}.png
  aoi/{contact-sheet.html,qa.json,stage-errors.json,usage_log.jsonl}
  MANIFEST.json
```

The orchestrator promotes the staged facade tree to `data/textures/facades/` and library tree to
`textures/library/` at integration. Shared production paths are not overwritten by this sandbox.
`bindings.json` supplies fallback keys and unique texture filenames; roof keys are explicit.
Failed QA outputs remain available for inspection but are not bound as runtime facade textures.

Unique facades use clamped metre-based UVs, u from segment start and v from facade base. JSON
records metre extent, pixel density, UV scale, left-normal orientation, source hashes and calls.
Dimensions are multiples of four with approximately equal pixel density. Whole facades are neither
period-cropped nor flattened (that would destroy unique architectural bands). Library materials
always receive low-frequency flattening and a make-tileable pass, with seam and low-frequency QA.
Paired, flat near-white presentation margins are removed before metric scaling. The original
image size and exact content box are recorded; isolated light cornice/plinth bands are preserved.
Very wide/tall walls receive a deterministic layout guide within the five-image tool limit.
QA rejects a generated content aspect more than 30% away from geometry, even when output pixel
dimensions are correct: resizing a squat generated building is not a valid metric facade.
Production requires DeepBump; the generic `post --sobel` option remains available for diagnostics.

Albedo is ETC1S/sRGB/BT709; ORM is ETC1S/linear/unspecified primaries; normals are RGB XYZ
UASTC + RDO + Zstd, linear/unspecified primaries, with normalized mipmaps. `--normal_mode` is
intentionally not used because it repacks XY into RGB/A. Every encoded file must pass
`ktx validate --gltf-basisu`. Post caches cover input bytes, geometry, code, encoder and all output
hashes; rerunning unchanged stages makes no model calls and preserves material output timestamps.

`contact-sheet.html` embeds small reference/result previews and lists every missing facade.
It opens on street facades and supports address/ID search and complete/missing/courtyard filters.
`qa.json` reports reference counts, source coverage, calls per facade (a building label is shared),
scale/asset failures and roof coverage. Calls include targeted repair runs under the AOI run
directory, deduplicated by Codex thread. Unique walls do not wrap, so their wrap-seam check is explicitly
not applicable. Library materials must pass both seam and low-frequency checks.

The original two-facade Phase 0 pilot remains available with `make textures-pilot`; it uses the
legacy per-facade label/strip recipes for reproducibility. It is not the AOI production command.

## Selective quality checks

From the repository root, run
`GATE_PYTHON="$PWD/textures/.venv/bin/python" scripts/dev/gate --base main` for this branch. See the
[quality gate guide](../scripts/dev/README.md) for dependency setup, caches and sandbox ports.

DeepBump subprocesses set `ORT_DISABLE_TELEMETRY=1` before ONNX Runtime starts and have a
600-second timeout. A live macOS stack sample found ORT 1DS teardown spinning indefinitely after
inference; disabling its uploader keeps the offline worker from waiting on network teardown. The dedicated
worker supplies one ONNX inference thread with spinning disabled, avoiding oversubscription during
parallel production; the DeepBump algorithm and vendor files remain unchanged.
See [ONNX Runtime telemetry controls](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md).

## Scoped landing and authoritative assembly

The original W1-09 batch stopped at 127 generated facade images. Incomplete or rejected walls use
library fallbacks; full photo coverage remains a follow-up batch. The loader also accepts W1-03
schema v1 (`base_height`, `top_height`, engine XYZ `normal`, `building_id`, `facade_index`).
Never give the assembler the provisional staging tree directly. Export into a separate lookup
root; only QA-passing assets with an exact baseline, normal, base and height match are copied:

```sh
textures/.venv/bin/python -m turkutex facade-export \
  "$TURKU3D_WORK/build/layers/buildings/facades.geojson" \
  --source-inventory "$TURKU3D_WORK/textures/aoi/all-facades.geojson" \
  --source "$TURKU3D_WORK/textures" --qa "$TURKU3D_WORK/textures/aoi/qa.json" \
  --output "$TURKU3D_WORK/textures/assembly"
```

Point assembler facade/library roots at `assembly/facades` and `assembly/library`. Missing unique
textures keep the building's library key. The assembler must apply sidecar `uv.scale` / `uv.offset`,
use `files.normal` and `files.orm`, and apply library `material.json` `uv_scale` to metre UVs.
The export preserves file timestamps when unchanged. It removes stale unique bindings in its own
output tree. Provisional heights without a measured base cannot match authoritative geometry.

Resume remaining acquisition and generation only when a new batch is authorized:

```sh
make textures-facade-batch \
  FACADES="$TURKU3D_WORK/build/layers/buildings/facades.geojson" TEX_J=6 TEX_MAX_PCT=100
```

[W1-09b round 1](../docs/reports/w1-09b-facades.md) uses an unrounded subset of the
authoritative inventory, prioritized by distance from the office spawn. Its report records
validated bindings, per-facade calls/tokens/wall time, screenshots, and exact export/assembly
commands. Generated-image counts from a provisional inventory are not binding counts:
always compare the current geometry and run the filtered export before assembly.

From the repository root, run `scripts/dev/gate --base HEAD` for local changes or
`scripts/dev/gate --all` for the complete offline suite. See the [quality gate guide](../scripts/dev/README.md)
for dependency setup, path selection, caches, sandbox ports and Git hooks.

For focused offline checks, source the sandbox environment and run
`scripts/dev/gate --paths textures/turkutex/post/pipeline.py`. Post-processing encodes albedo,
ORM and normal maps concurrently with unchanged codec settings. The KTX2 header/mipmap test
uses a 128×128 two-colour fixture; noisy seam and normal-quality tests remain separate.

## Shared download cache

New panorama downloads use `$TURKU3D_DATA/cache/streetlevel` with locking and atomic writes.
`TURKUTEX_STREETVIEW` overrides that root. Existing caches are not migrated. `make cache-gc`
reports sizes; `make cache-gc APPLY=1` removes only old partial downloads.
See [region cache details](../pipeline/turku3d/region/README.md).

## Ground materials and ortho products (W1-10)

See [turkutex/ground/README.md](turkutex/ground/README.md) for the four-tile build, quota-aware
library generation, vector markings, memory accounting, and exact verification commands.
The renderer/assembler interface is [game/src/render/ground](../game/src/render/ground/README.md).
Run `source ../sandbox.env && make -j2 ground-texture-test` for the fast checks. The `ground` Python extra
provides rasterio, shapely, geopandas and pyarrow; `make textures-venv` includes it.
DeepBump runs in a bounded ONNX worker (one intra-op thread, one inter-op thread, no spinning)
so concurrent texture jobs do not each occupy a full-machine thread pool.


## Shared Codex accounting and routing

Every uncached attempt is appended to `~/turku3d/ledger/codex-calls.jsonl` before image collection.
`TURKU3D_CODEX_LEDGER` overrides the destination for isolated tests. `turn.completed.usage` is
canonical; rollout totals are retained separately because resumed sessions can accumulate usage.
Missing usage is null, including interrupted attempts, and cached/reasoning tokens are subsets.

`textures/codex-routing.toml` supplies job-kind model and effort defaults. Explicit job `model`
and `effort` override it; effort participates in the cache key. `TURKU3D_CODEX_ROUTING` can select
another TOML file. QA reads the same `[qa]` route; task workers read `[task]` while preserving
explicit task effort. Current conservative defaults and pilot limitations are in
[`docs/reports/codex-model-bench.md`](../docs/reports/codex-model-bench.md).

```sh
source ../sandbox.env
scripts/dev/codex-cost --by job_type
scripts/dev/codex-cost --by model --json
```
