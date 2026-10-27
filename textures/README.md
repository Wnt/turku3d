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
Decoded panorama pixels are reused through read-only memory maps in
`$TURKU3D_DATA/cache/streetlevel/decoded`; the shared compressed panorama cache
remains the source of truth.
The decoded files are keyed by compressed image SHA-256, so repeated facade runs in separate
sandboxes map the same panorama without another 384 MiB array per 16K image.

## AOI workflow

### Direct photo path for the office core

For a quota-light pass, use the authoritative buildings inventory and rectified
references. `facade_direct` repairs masked occluders from visible bays of the same
wall, matches broad exposure, and uses the library plinth and roof-edge materials.
It validates wall coverage and KTX2 output before publishing each facade under
the additive assembly lock. A wall with no full, geometry-matched reference stays
unbound with an explicit reason in the contact-sheet JSON. The optional grouped
label pass uses eight outlined walls per `gpt-6-luna` low-effort call; its runner
checks the weekly quota before every call and stops at 70%.

### Fully generated facade mode

`facade-batch` also has a `generate` mode. It gives the image worker the measured
wall width/height, up to three full-wall rectified references, a perspective outline,
and a geometry layout guide for extreme aspect ratios. The prompt requires newly
generated wall pixels and excludes street clutter. Use `--max-pct 60` for the pilot:

```sh
source ../sandbox.env
FACADES="$TURKU3D_WORK/textures/pilot/facades.geojson"
RUN="$TURKU3D_WORK/textures/pilot"
textures/.venv/bin/python -m turkutex facade-batch refs "$FACADES" --refs "$TURKU3D_WORK/textures/refs"
textures/.venv/bin/python -m turkutex facade-batch generate "$FACADES" --run "$RUN" --refs "$TURKU3D_WORK/textures/refs" -j 2 --max-pct 60
textures/.venv/bin/python -m turkutex facade-batch visualqa "$FACADES" --run "$RUN" --refs "$TURKU3D_WORK/textures/refs" --output "$RUN/staging" -j 4 --max-pct 60
textures/.venv/bin/python -m turkutex facade-batch post "$FACADES" --mode generate --run "$RUN" --refs "$TURKU3D_WORK/textures/refs" --output "$RUN/staging" --ppm 32
textures/.venv/bin/python -m turkutex.facade_generated_qa --facades "$FACADES" --refs "$TURKU3D_WORK/textures/refs" \
  --generated "$RUN/generated" --staging "$RUN/staging" --visual-qa "$RUN/run_visualqa/out.jsonl" \
  --output "$RUN/acceptance.json" --min-ppm 32
```

Every rendered view is screened: more than 20% pure black (a Look Around crop
outside panorama coverage) or a contrast-normalised Laplacian variance below 0.065
at 512 px (motion blur) makes it invalid. When no rendered full view is valid,
`refs` renders up to four more full views per source in the order Look Around →
Google Street View → Mapillary and stops at the first valid one (`fallback` in
`refs.json`). Generate jobs use the valid full views in that source order, best
score first within a source; a wall with no valid view in any source gets no job,
and `building_jobs.screened_full_views` returns the reason.
Before `generate`, the `refgrid` stage (same arguments as `visualqa`, reviewer route
`reference_grid`, gpt-6-astra/low) reads each wall's valid references into
`facade_reference_grid.schema.json`: whether they show the outlined wall at all, and its
storeys, window columns, ground-floor openings, plinth, cornice and cladding. `generate`
then skips walls whose references do not show them and writes the measured grid and the
exact aspect into the prompt as hard constraints, also for `--feedback` retries.
`TURKU3D_CODEX_MODEL` and `TURKU3D_CODEX_EFFORT` select the image model for one
run. The `visualqa` stage writes the reviewed wall content, without layout-guide
margins, to `$RUN/generated/<facade_id>.png` and asks the fixed reviewer
(`generated_visual_qa` in `codex-routing.toml`, gpt-6-astra/low) to compare it with the
used references under `schemas/facade_visual_qa.schema.json`. Run it without the
model override so every image model is judged by the same reviewer. For one retry
of rejected walls, write `{facade_id: reason}` to a JSON file and pass it to
`generate --feedback FILE`; only those walls are regenerated, the reason goes into
the prompt, and their latest record replaces the old one. Then rerun `visualqa`,
`post` and the audit. The model comparison is in
`docs/reports/w1-09g-generated-office.md`.

### Generated rollout batches

`turkutex.facade_rollout` ranks every street-facing wall of the approved inventory
without a generated map into a queue file written once. Keep it outside the sandbox
(the rollout uses `~/turku3d/logs/facade-rollout/rollout-queue.json`): landing a batch
removes the sandbox, and the queue records which walls earlier batches consumed. Order:
office-core walls ≥ 4 m, the other core walls, then 50 m rings outward from the core
bbox; within a group wider walls nearer the spawn first. Walls beyond 3.5:1 (either
way) are `deferred` and never batched, because the layout guide misses the 3% aspect
check. Each batch takes the next 25 walls and records the starting quota:

```sh
source ../sandbox.env
T="$TURKU3D_WORK/textures"; A="$TURKU3D_DATA/facade-approvals/assembly"; N=1
LOGS=~/turku3d/logs/facade-rollout; Q="$LOGS/rollout-queue.json"
R="$T/rollout/batch-$(printf %02d $N)"; PY=textures/.venv/bin/python
$PY -m turkutex.facade_rollout queue --inventory "$A/APPROVED_INVENTORY.geojson" --assembly "$A" \
  --tiles-manifest "$TURKU3D_DATA/live/tiles/manifest.json" --output "$Q"
$PY -m turkutex.facade_rollout batch $N --queue "$Q" --run "$R" \
  --inventory "$A/APPROVED_INVENTORY.geojson" --refs "$T/refs" --size 25
$PY -m turkutex facade-batch refs "$R/facades.geojson" --run "$R" --refs "$T/refs" \
  --output "$R/staging" --sources lookaround,google,mapillary --ref-workers 8
# generate (luna/low), visualqa, post --ppm 32 --post-workers 4 and the audit as above, then
# write $R/manual.json ({facade_id: {"ok": bool, "note": str}}) from the contact sheet
$PY -m turkutex.facade_rollout contact $N --queue "$Q" --run "$R" --output SHEET.jpg
$PY -m turkutex.facade_rollout summary $N --queue "$Q" --run "$R" --logs "$LOGS"
$PY -m turkutex.facade_rollout publish $N --queue "$Q" --run "$R" \
  --inventory "$A/APPROVED_INVENTORY.geojson" --assembly "$A"
```

`summary` writes `batch-NN.json` (calls, seconds, tokens, quota, verdicts, reference
sources) and regenerates `ledger.md`. `publish` binds only walls that passed the audit
and the manual look, under the export lock, with the inventory hash unchanged.

The office pilot used the built-in image generator and put its three chosen
images in `$RUN/generated`, with equivalent per-image records in
`$RUN/run_generate/out.jsonl`. After image-based opening/foreign-object QA,
`turkutex.facade_generated_qa` verifies that the visual check saw the exact
generated bytes, the wall content aspect is within 3%, horizontal and vertical
texel density agree, source references still match geometry, and all three KTX2
hashes are intact. Its `--facades`, `--refs`, `--generated`, `--staging`,
`--visual-qa` and `--output` arguments name the input and report paths.
The optional `--min-ppm 32` records a minimum assembled density for a small
set of showcase walls. The packager reduces other facades first and still
rejects any tile exceeding the 8 MB wire cap.
Accepted maps can be copied into a sandbox clone of
`$TURKU3D_DATA/textures/assembly`; the assembler reads that clone from
`$TURKU3D_WORK/textures/assembly`. Never write through the live symlink.

```sh
source ../sandbox.env
make textures-setup
make textures-ktx
FACADES="$TURKU3D_DATA/build/layers/buildings/facades.geojson"
ASSEMBLY="$TURKU3D_DATA/textures/assembly"
NEAR="$TURKU3D_WORK/textures/near-120.geojson"
textures/.venv/bin/python - "$FACADES" "$ASSEMBLY/MANIFEST.json" "$NEAR" <<'PY'
import json, math, pathlib, sys
from turku3d import config
inventory, manifest, output = map(pathlib.Path, sys.argv[1:])
data = json.loads(inventory.read_text())
bound = set(json.loads(manifest.read_text())["bound"])
near = []
for wall in data["features"]:
    props = wall["properties"]
    points = wall["geometry"]["coordinates"]
    centre = [(points[0][i] + points[-1][i]) / 2 for i in (0, 1)]
    identity = f"{props['building_id']}_{props['facade_index']}"
    distance = math.dist(centre, (config.OFFICE_E, config.OFFICE_N))
    if props.get("street_facing") and distance <= 120 and identity not in bound:
        near.append((distance, wall))
data["features"] = [wall for _, wall in sorted(near, key=lambda item: item[0])]
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(data))
PY
mkdir -p "$TURKU3D_WORK/textures/library-png"
for key in plinth_granite roof_standing_seam_metal; do
  "$TURKU3D_WORK/tools/ktx/bin/ktx" extract --transcode rgba8 \
    "$ASSEMBLY/library/$key/albedo.ktx2" "$TURKU3D_WORK/textures/library-png/$key.png"
done
textures/.venv/bin/python -m turkutex facade-batch refs "$NEAR" \
  --refs "$TURKU3D_WORK/textures/refs" --sources google,lookaround --ref-workers 3
textures/.venv/bin/python -m turkutex.facade_direct "$NEAR" \
  --refs "$TURKU3D_WORK/textures/refs" --staging "$TURKU3D_WORK/textures/direct-staging" \
  --output "$ASSEMBLY" --library "$TURKU3D_WORK/textures/library-png" \
  --report-inventory "$FACADES" --contact "$TURKU3D_WORK/reports/facade-contact.html"
textures/.venv/bin/python -m turkutex.facade_labels "$NEAR" \
  --refs "$TURKU3D_WORK/textures/refs" --run "$TURKU3D_WORK/textures/label-batch"
textures/.venv/bin/python -m turkutex.facade_direct "$NEAR" \
  --refs "$TURKU3D_WORK/textures/refs" --staging "$TURKU3D_WORK/textures/direct-staging" \
  --output "$ASSEMBLY" --library "$TURKU3D_WORK/textures/library-png" \
  --labels "$TURKU3D_WORK/textures/label-batch/labels.json" \
  --report-inventory "$FACADES" --contact "$TURKU3D_WORK/reports/facade-contact.html"
DEEPBUMP_DIR="$TURKU3D_DATA/tools/DeepBump" \
textures/.venv/bin/python -m turkutex.facade_repair "$NEAR" \
  --refs "$TURKU3D_WORK/textures/refs" \
  --labels "$TURKU3D_WORK/textures/label-batch/labels.json" \
  --run "$TURKU3D_WORK/textures/repair" \
  --staging "$TURKU3D_WORK/textures/repair-staging" --output "$ASSEMBLY" --limit 25
```

The assembly manifest retains earlier bindings; direct staging and reports stay
in the current sandbox. The bundled label schema records uncertainty explicitly.

After a building rebuild, `make refresh REFRESH_TARGET=facades` reuses approved
maps and writes `textures/refresh-new.geojson` under `$TURKU3D_WORK`. Only walls
in that inventory need new reference photos. Walls that exceed the approved photo
bounds are left on their library material and listed in
`$TURKU3D_WORK/reports/facade-refresh.json` under `rejected`. Missing or corrupt
approved assets still stop the refresh. The no-image-generation path is:

```sh
T="$TURKU3D_WORK/textures"
textures/.venv/bin/python -m turkutex facade-batch refs "$T/refresh-new.geojson" \
  --run "$T/refresh-new-run" --refs "$T/refresh-refs" --output "$T" \
  --sources google,lookaround,mapillary --ref-workers 3
textures/.venv/bin/python -m turkutex.facade_direct "$T/refresh-new.geojson" \
  --refs "$T/refresh-refs" --staging "$T/refresh-direct-staging" \
  --output "$T/assembly" --library "$T/library-png"
```

For rejected walls, run `python -m turkutex facade-batch refs` again into a
separate sandbox reference directory with
`--sources google,lookaround,mapillary`. Run
`python -m turkutex.facade_alternates FACADES --original-refs OLD --alternate-refs NEW
--output SELECTED.geojson` to write `preferred.json` for a different full-wall
panorama per wall. Label `SELECTED.geojson` with `turkutex.facade_labels`, then
run `turkutex.facade_direct` with `--refs NEW --labels LABELS`. A selected view
is recorded by ID; source scores remain intact. To inspect older Street View
dates, use `--sources google,google_history --ref-max-views 8` in a separate
reference directory, then add `--older-than-newest` to
`turkutex.facade_alternates` to choose a rendered historical view. Keep
the default shared cache for panorama downloads; keep crops and run outputs in
the sandbox reference directory.

The direct contact sheet defaults to 120 m around the spawn. For the four-tile
run, combine resumed batches with:

```sh
T="$TURKU3D_WORK/textures"
R="$TURKU3D_WORK/reports"
textures/.venv/bin/python -m turkutex.facade_contact "$T/four-tiles-all.geojson" \
  --assembly "$TURKU3D_DATA/textures/assembly" \
  --refs "$T/refs" "$T/alternate-refs" "$T/history-refs" \
  --staging "$T/direct-staging" "$T"/direct-staging-part{1,2,3} \
    "$T"/direct-staging-acquired{1..8} "$T/direct-staging-acquired7-rest" \
    "$T/alternate-direct-staging" \
    "$T/history-direct-staging" \
  --labels "$T/label-batch/labels.json" "$T/alternate-labels/labels.json" \
    "$T/history-labels/labels.json" \
  --results "$R"/four-tiles-direct-*.log "$R"/alternate-direct*.log \
    "$R"/history-direct.log \
  --contact "$R/facade-contact-four-tiles.html"
```

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

The same runner makes the shop menu art: `turkutex.ui_art` writes its jobs and encodes the results
to WebP for `game/public/assets/ui/` ([commands and prompts](../game/src/ui/README.md#menu-art)).

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
`TURKUTEX_STREETVIEW` overrides that root. Existing WORK/cache/streetlevel files are imported
on first default `PanoCache` use without deleting their source. `TURKU3D_STREETLEVEL_CACHE_GB`
caps the shared LRU cache (60 by default). `make cache-gc`
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

Cost routing is configured in `textures/codex-routing.toml`. Explicit job model/effort
wins over `TURKU3D_CODEX_MODEL` / `TURKU3D_CODEX_EFFORT`, which wins over the routing
file. See `docs/reports/codex-model-bench.md` for measured quality and limits; unknown
label truth is excluded from scoring. The emergency pilot makes no improve/image calls.
